from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

import utils
from session_store import SessionStore
from web_models import EventEnvelope, EventType
from web_session import ActiveRun, SessionManager


# Before-write file snapshots, most-recently-written session last. Bounded:
# a long-lived server that touches many sessions would otherwise hold every
# file it ever wrote. Evicted sessions fall back to the persisted change
# manifests for diff baselines (see workspace_diff/_persisted_before).
_file_snapshots: "OrderedDict[str, dict[str, str]]" = OrderedDict()
_snapshots_lock = threading.Lock()

# Cap for before-write snapshots and content hashes; must match on both the
# capture (here) and revert (workspace routes) sides.
SNAPSHOT_MAX_BYTES = 512_000

# Total retained snapshot text across all sessions before LRU eviction starts.
SNAPSHOT_CACHE_MAX_BYTES = 64_000_000


def _session_snapshot_bytes(snaps: dict[str, str]) -> int:
    return sum(len(text) for text in snaps.values() if text)


def _evict_snapshots(keep_session_id: str) -> None:
    """Drop least-recently-written sessions until the cache fits its budget.

    The session passed in is never evicted, so an in-flight run keeps its own
    baselines regardless of how large they are.
    """
    total = sum(_session_snapshot_bytes(s) for s in _file_snapshots.values())
    if total <= SNAPSHOT_CACHE_MAX_BYTES:
        return
    for sid in [s for s in _file_snapshots if s != keep_session_id]:
        if total <= SNAPSHOT_CACHE_MAX_BYTES:
            break
        total -= _session_snapshot_bytes(_file_snapshots[sid])
        del _file_snapshots[sid]


def read_capped(path: str) -> str | None:
    """Read up to SNAPSHOT_MAX_BYTES of a text file, or None if unreadable."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(SNAPSHOT_MAX_BYTES)
    except (OSError, IsADirectoryError):
        return None


def content_hash(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


class WebUI:
    """AgentUI implementation that emits WebSocket events and persists to JSONL."""

    def __init__(
        self,
        session_id: str,
        manager: SessionManager,
        run: ActiveRun,
        store: SessionStore,
        run_settings: dict | None = None,
        run_id: str | None = None,
    ):
        self._sid = session_id
        self._manager = manager
        self._run = run
        self._store = store
        # Effective settings snapshotted when the run starts; mid-run changes
        # in this or any other session must not alter a running worker.
        self.run_settings: dict = dict(run_settings or {})
        self.run_id = run_id or f"run_{uuid.uuid4().hex[:10]}"
        # Durable record of files this run touches: before-content captured at
        # first write, action/after-hash updated per change, persisted so diffs
        # and reverts survive a backend restart.
        self._change_manifest: dict = {
            "run_id": self.run_id,
            "session_id": session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "files": {},
        }
        self.finished_reason: str | None = None
        self._active_tool_calls: dict[str, list[tuple[str, float]]] = {}
        with _snapshots_lock:
            if session_id not in _file_snapshots:
                _file_snapshots[session_id] = {}
            _file_snapshots.move_to_end(session_id)

    @property
    def session_id(self) -> str:
        return self._sid

    def _emit(self, event_type: EventType, data: dict) -> None:
        event = EventEnvelope(session_id=self._sid, type=event_type, data=data)
        self._store.append_event(event)
        self._manager.emit_event(event)

    def show_user_message(self, text: str, images: list[dict] | None = None) -> None:
        data = {"text": text}
        attachments = images or []
        if attachments:
            data["attachments"] = attachments
            data["images"] = [
                item for item in attachments
                if str(item.get("mime", "")).startswith("image/")
            ]
        self._emit(EventType.USER_MESSAGE, data)

    def show_assistant_markdown(self, text: str) -> None:
        self._emit(EventType.ASSISTANT_MESSAGE, {"markdown": text})

    def show_assistant_delta(self, text: str) -> None:
        # Live-only: deltas are broadcast but never persisted. The final
        # assistant_message event carries the full markdown for replay.
        event = EventEnvelope(session_id=self._sid, type=EventType.ASSISTANT_DELTA, data={"text": text})
        self._manager.emit_event(event)

    def show_thinking(self, text: str) -> None:
        self._emit(EventType.THINKING, {"markdown": text})

    def show_thinking_delta(self, text: str) -> None:
        # Live-only, mirroring show_assistant_delta: reasoning deltas are
        # broadcast but never persisted. The completed thinking event carries
        # the full markdown for replay.
        event = EventEnvelope(session_id=self._sid, type=EventType.THINKING_DELTA, data={"text": text})
        self._manager.emit_event(event)

    def show_tool_call(
        self, name: str, args: dict, status_line: str, verbose: bool
    ) -> None:
        call_id = f"tool_{uuid.uuid4().hex[:10]}"
        self._active_tool_calls.setdefault(name, []).append((call_id, time.monotonic()))
        if not verbose:
            self._emit(
                EventType.TOOL_CALL,
                {"call_id": call_id, "name": name, "status_line": status_line, "verbose": False},
            )
            return
        self._emit(
            EventType.TOOL_CALL,
            {
                "call_id": call_id,
                "name": name,
                "args": args,
                "status_line": status_line,
                "verbose": verbose,
            },
        )

    def show_tool_result(self, name: str, result_preview: str, verbose: bool) -> None:
        active = self._active_tool_calls.get(name) or []
        call_id, started = active.pop(0) if active else (None, None)
        if not active:
            self._active_tool_calls.pop(name, None)
        duration_ms = round((time.monotonic() - started) * 1000) if started is not None else None
        first_line = str(result_preview or "").splitlines()[0][:240]
        ok = not first_line.startswith("ERROR")
        if name == "shell_run" and first_line.startswith("Exit code:"):
            ok = first_line.strip() == "Exit code: 0"
        if not verbose:
            self._emit(
                EventType.TOOL_RESULT,
                {
                    "call_id": call_id,
                    "name": name,
                    "preview": first_line,
                    "ok": ok,
                    "duration_ms": duration_ms,
                    "verbose": False,
                },
            )
            return
        self._emit(
            EventType.TOOL_RESULT,
            {
                "call_id": call_id,
                "name": name,
                "preview": result_preview,
                "ok": ok,
                "duration_ms": duration_ms,
                "verbose": verbose,
            },
        )

    def show_status(self, text: str, style: str = "") -> None:
        self._emit(EventType.STATUS, {"text": text, "style": style})

    def show_error(self, text: str) -> None:
        self._emit(EventType.ERROR, {"text": text})

    def show_iteration(self, n: int) -> None:
        self._emit(
            EventType.ITERATION,
            {
                "n": n,
                "max": self.run_settings.get("max_iterations", utils.MAX_AGENT_ITERATIONS),
                "verbose": self.run_settings.get("verbose_tools", utils.UI_VERBOSE_TOOLS),
            },
        )

    def show_compaction(self, before_tokens: int, after_tokens: int) -> None:
        self._emit(
            EventType.COMPACTION, {"before": before_tokens, "after": after_tokens}
        )

    def show_context_usage(self, usage_str: str) -> None:
        self._emit(EventType.CONTEXT_USAGE, {"usage_str": usage_str})

    def show_api_metrics(self, metrics: dict) -> None:
        self._emit(EventType.API_METRICS, metrics)

    def show_run_metrics(self, metrics: dict) -> None:
        self._emit(EventType.RUN_METRICS, metrics)

    def show_agent_finished(self, reason: str) -> None:
        self.finished_reason = reason
        self._emit(EventType.RUN_FINISHED, {"reason": reason})

    def request_approval(
        self,
        tool_name: str,
        args_json: str,
        diff_preview: str | None,
    ) -> bool:
        approval_id = f"apr_{uuid.uuid4().hex[:8]}"
        self._emit(
            EventType.APPROVAL_REQUIRED,
            {
                "approval_id": approval_id,
                "tool_name": tool_name,
                "args_json": args_json,
                "diff_preview": diff_preview,
            },
        )
        self._manager.notify_run_state(self._sid, "waiting_approval")
        approved = self._run.request_approval(approval_id)
        self._manager.notify_run_state(self._sid, "running")
        self._emit(
            EventType.APPROVAL_RESOLVED,
            {"approval_id": approval_id, "approved": approved},
        )
        return approved

    def ask_user_question(
        self,
        question: str,
        options: list[str] | None = None,
        allow_free_text: bool = True,
    ) -> str | None:
        """Put one question to the user and block the run until they answer.

        Returns the answer text, or None if it went unanswered — callers tell
        the model to proceed on its own judgement rather than inventing one.
        """
        question_id = f"qst_{uuid.uuid4().hex[:8]}"
        choices = [str(option) for option in (options or []) if str(option).strip()]
        self._emit(
            EventType.QUESTION_REQUIRED,
            {
                "question_id": question_id,
                "question": question,
                "options": choices,
                "allow_free_text": bool(allow_free_text) or not choices,
            },
        )
        self._manager.notify_run_state(self._sid, "waiting_input")
        answer = self._run.ask_question(question_id)
        self._manager.notify_run_state(self._sid, "running")
        self._emit(
            EventType.QUESTION_RESOLVED,
            {"question_id": question_id, "answer": answer, "answered": answer is not None},
        )
        return answer

    def snapshot_file_before_write(self, path: str) -> None:
        normalized = os.path.normpath(os.path.abspath(path))
        # Per-run manifest entry: baseline as of this run's first write.
        if normalized not in self._change_manifest["files"]:
            existed = os.path.isfile(normalized)
            before = read_capped(normalized) if existed else None
            self._change_manifest["files"][normalized] = {
                "path": normalized,
                "existed_before": existed,
                "before": before,
                "action": None,
                "tool": None,
                "after_hash": None,
                "changed_at": None,
            }
        # Session-lifetime in-memory snapshot: baseline as of the session's
        # first-ever write (feeds the live Changes-panel diff).
        with _snapshots_lock:
            snaps = _file_snapshots.setdefault(self._sid, {})
            _file_snapshots.move_to_end(self._sid)
            if normalized not in snaps:
                snaps[normalized] = read_capped(normalized)
                _evict_snapshots(self._sid)

    def show_file_change(self, path: str, action: str, tool: str) -> None:
        self._emit(
            EventType.FILE_CHANGE,
            {"path": path, "action": action, "tool": tool},
        )
        try:
            normalized = os.path.normpath(os.path.abspath(path))
            entry = self._change_manifest["files"].get(normalized)
            if entry is None:
                # Creations (e.g. apply_patch Add File) have no before-write
                # snapshot call; the file did not exist before this run.
                entry = {
                    "path": normalized,
                    "existed_before": False,
                    "before": None,
                    "action": None,
                    "tool": None,
                    "after_hash": None,
                    "changed_at": None,
                }
                self._change_manifest["files"][normalized] = entry
            entry["action"] = action
            entry["tool"] = tool
            entry["changed_at"] = datetime.now(timezone.utc).isoformat()
            entry["after_hash"] = (
                None if action == "deleted" else content_hash(read_capped(normalized))
            )
            self._store.save_change_manifest(self._sid, self._change_manifest)
        except Exception:
            # Manifest persistence must never break the run loop.
            pass

    def show_observed_file_change(self, path: str, action: str, tool: str) -> None:
        """Record a provider-owned edit without inventing a revert baseline."""
        self._emit(
            EventType.FILE_CHANGE,
            {"path": path, "action": action, "tool": tool},
        )

    def show_plan_update(self, items: list[dict]) -> None:
        self._emit(EventType.PLAN_UPDATE, {"items": items})

    def show_generated_artifact(self, path: str, media_type: str) -> None:
        version = str(time.time_ns())
        try:
            stat = os.stat(path)
            version = f"{stat.st_mtime_ns}-{stat.st_size}-{version}"
        except OSError:
            pass
        data = {"path": path, "name": os.path.basename(path), "media_type": media_type}
        if version:
            data["version"] = version
        self._emit(
            EventType.GENERATED_ARTIFACT,
            data,
        )

    def show_codex_command(self, command: str, status: str) -> None:
        self._emit(EventType.CODEX_COMMAND, {"command": command, "status": status})

    def show_codex_file_change(self, path: str, status: str) -> None:
        self._emit(
            EventType.CODEX_FILE_CHANGE,
            {"path": path, "status": status, "records_change": False},
        )

    def show_codex_item(self, item_type: str, raw: dict) -> None:
        self._emit(EventType.CODEX_ITEM, {"item_type": item_type, "raw": raw})

    def show_provider_warning(self, message: str, detail: str = "") -> None:
        self._emit(EventType.PROVIDER_WARNING, {"message": message, "detail": detail})

    def prompt_user_input(self, prompt_text: str) -> str:
        raise EOFError("Web UI does not support interactive input prompts")
