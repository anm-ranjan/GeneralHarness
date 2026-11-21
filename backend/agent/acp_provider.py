"""Agent Client Protocol (ACP) provider: drives an external ACP agent over stdio.

The first preset is Google Antigravity (``agy acp``) on a personal AI Pro
sign-in. The agent runs its own loop and tools; MyHarness renders its session
updates, answers its permission requests and keeps the transcript.

Consumer sign-in is held to what Google allows for third-party ACP clients:
one person driving an interactive session with the unmodified binary, which
owns sign-in. Concretely (see ``AcpAgentConfig`` and ``_policy_error``):
- only ``oauth-personal`` sign-in; credentials are never read or forwarded,
- never ``--dangerously-skip-permissions``,
- only models matching ``allowed_models`` (Gemini 4 by default),
- the agent's background and multi-agent slash commands are refused,
- no routines, agent runs, sub-agents or asides (enforced by their callers,
  which accept native only, and by ``aside`` refusing ACP providers),
- every prompt is one the user typed: provider handoffs in and out are
  summarised by the native summary model, never by a hidden agent turn,
- the system prompt addition is only the folder limits.

Provider ids are ``acp:<agent key>``, e.g. ``acp:antigravity``.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import hmac
import json
import secrets
import sys
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import utils


ACP_PREFIX = "acp:"
PROTOCOL_VERSION = 1
AUTH_REQUIRED_CODE = -32000
ALLOWED_AUTH = ("oauth-personal",)
FORBIDDEN_ARGS = ("--dangerously-skip-permissions",)
DEFAULT_HIDDEN_COMMANDS = ("schedule", "swarm", "teamwork", "boost", "run-workflow")
EFFORT_SUFFIXES = ("low", "medium", "high", "xhigh")
APPROVAL_TO_MODE = {"always_ask": "default", "shell_only": "accept-edits", "auto_approve": "auto"}
_LINE_LIMIT = 64 * 1024 * 1024
# The question tool MyHarness gives every ACP session (acp_ask_mcp.py). Agents
# name MCP tools mcp_<server>_<tool>; this one never needs the user's approval.
ASK_SERVER_NAME = "myharness"
API_ENV = "AGENT_HOST_API"
ASK_TOOL_ACP_NAME = f"mcp_{ASK_SERVER_NAME}_ask_user"
ASK_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acp_ask_mcp.py")
# Where the runtime publishes its question socket (port + token); set by the app.
ASK_FILE: str | None = None


def is_acp(provider: str | None) -> bool:
    return isinstance(provider, str) and provider.startswith(ACP_PREFIX)


def acp_key(provider: str) -> str:
    return provider[len(ACP_PREFIX):] if is_acp(provider) else ""


def provider_id(key: str) -> str:
    return f"{ACP_PREFIX}{key}"


# ── configuration ───────────────────────────────────────────────────


@dataclass(frozen=True)
class AcpAgentConfig:
    key: str
    label: str
    command: str
    args: tuple[str, ...] = ("acp",)
    auth: str = "oauth-personal"
    model: str = ""
    thought_level: str = "high"
    mode: str = "default"
    allowed_models: tuple[str, ...] = ("gemini-4-*",)
    hidden_commands: tuple[str, ...] = DEFAULT_HIDDEN_COMMANDS
    timeout_seconds: int = 1800
    idle_timeout_seconds: int = 600
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def provider(self) -> str:
        return provider_id(self.key)

    @property
    def policy(self) -> str:
        # Only consumer sign-in is supported, so the consumer policy always applies.
        return "consumer"

    def model_allowed(self, model_id: str) -> bool:
        return any(fnmatch.fnmatchcase(model_id, pattern) for pattern in self.allowed_models)


def _str_list(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return default


def _int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def parse_agent_configs(raw: Any) -> dict[str, AcpAgentConfig]:
    """``acp:`` config block -> agents by key. Invalid agents carry ``errors``."""
    if not isinstance(raw, dict) or str(raw.get("enabled", "false")).lower() not in ("true", "1", "yes"):
        return {}
    agents: dict[str, AcpAgentConfig] = {}
    for key, entry in (raw.get("agents") or {}).items():
        if not isinstance(entry, dict):
            continue
        key = str(key).strip()
        errors: list[str] = []
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", key):
            errors.append(f"agent key '{key}' must be lowercase letters, digits, '-' or '_'")
        if str(entry.get("enabled", "true")).lower() in ("false", "0", "no"):
            continue
        args = _str_list(entry.get("args"), ("acp",))
        bad_args = [arg for arg in args if arg.split("=", 1)[0] in FORBIDDEN_ARGS]
        if bad_args:
            errors.append(f"argument {bad_args[0]} is not allowed")
        auth = str(entry.get("auth") or "oauth-personal").strip()
        if auth not in ALLOWED_AUTH:
            errors.append(f"auth '{auth}' is not supported; only oauth-personal sign-in is")
        hidden = tuple(c.lstrip("/") for c in _str_list(entry.get("hidden_commands"), ()))
        agents[key] = AcpAgentConfig(
            key=key,
            label=str(entry.get("label") or key.title()),
            command=str(entry.get("command") or "agy").strip(),
            args=args,
            auth=auth,
            model=str(entry.get("model") or "").strip(),
            thought_level=str(entry.get("thought_level") or "high").strip().lower(),
            mode=str(entry.get("mode") or "default").strip(),
            allowed_models=_str_list(entry.get("allowed_models"), ("gemini-4-*",)),
            # Config can add to the hidden list, never shrink it.
            hidden_commands=tuple(dict.fromkeys(DEFAULT_HIDDEN_COMMANDS + hidden)),
            timeout_seconds=_int(entry.get("timeout_seconds"), 1800),
            idle_timeout_seconds=_int(entry.get("idle_timeout_seconds"), 600),
            errors=tuple(errors),
        )
    return agents


def configured_agents() -> dict[str, AcpAgentConfig]:
    return parse_agent_configs(getattr(utils, "ACP_CONFIG", {}))


def agent_for(provider: str) -> AcpAgentConfig | None:
    return configured_agents().get(acp_key(provider)) if is_acp(provider) else None


def agent_available(agent: AcpAgentConfig | None) -> bool:
    return bool(agent and not agent.errors and shutil.which(agent.command))


def available_agents() -> list[AcpAgentConfig]:
    return [agent for agent in configured_agents().values() if agent_available(agent)]


def unavailable_reason(provider: str) -> str | None:
    agent = agent_for(provider)
    if agent is None:
        return f"The ACP agent '{acp_key(provider)}' is not configured or ACP is disabled."
    if agent.errors:
        return f"The {agent.label} agent config is invalid: {'; '.join(agent.errors)}."
    if not shutil.which(agent.command):
        return f"The {agent.label} command '{agent.command}' was not found on PATH."
    return None


# ── model catalog ───────────────────────────────────────────────────


def _split_effort(model_id: str) -> tuple[str, str]:
    for effort in EFFORT_SUFFIXES:
        if model_id.endswith(f"-{effort}"):
            return model_id[: -len(effort) - 1], effort
    return model_id, ""


def _strip_effort_label(name: str) -> str:
    return re.sub(r"\s*\((low|medium|high|xhigh)\)\s*$", "", name, flags=re.I).strip()


def catalog_from_variants(agent: AcpAgentConfig, variants: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Model families with their effort levels, filtered by ``allowed_models``.

    ``variants`` are (model id, display name) pairs as the agent lists them,
    e.g. ("gemini-4-neon-high", "Gemini 4 Neon (High)").
    """
    families: dict[str, dict[str, Any]] = {}
    for model_id, name in variants:
        family, effort = _split_effort(model_id)
        if not agent.model_allowed(family):
            continue
        entry = families.setdefault(family, {
            "id": family,
            "display_name": _strip_effort_label(name) or family,
            "description": "",
            "supported_efforts": [],
        })
        if effort and effort not in entry["supported_efforts"]:
            entry["supported_efforts"].append(effort)
    models = []
    for entry in families.values():
        efforts = [e for e in EFFORT_SUFFIXES if e in entry["supported_efforts"]]
        entry["supported_efforts"] = efforts
        entry["default_effort"] = (
            agent.thought_level if agent.thought_level in efforts else ("high" if "high" in efforts else (efforts[-1] if efforts else ""))
        )
        models.append(entry)
    default = agent.model if any(m["id"] == agent.model for m in models) else (models[0]["id"] if models else "")
    for entry in models:
        entry["is_default"] = entry["id"] == default
    return models


def variants_from_session(result: dict[str, Any]) -> list[tuple[str, str]]:
    models = (result or {}).get("models") or {}
    return [
        (str(item.get("modelId")), str(item.get("name") or item.get("modelId")))
        for item in models.get("availableModels") or []
        if item.get("modelId")
    ]


# The model list comes only from the agent's own ACP replies (session/new and
# session/load), never from running its CLI. The last good list per agent is
# kept in memory and, once the app sets CATALOG_FILE, on disk for restarts.
_catalog_cache: dict[str, list[dict[str, Any]]] = {}
CATALOG_FILE: str | None = None
_catalog_lock = threading.Lock()


def _load_catalog_file() -> dict[str, list[dict[str, Any]]]:
    if not CATALOG_FILE:
        return {}
    try:
        with open(CATALOG_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_catalog(agent: AcpAgentConfig, variants: list[tuple[str, str]]) -> None:
    """Keep the allowed models from a session reply; a list with none allowed
    (e.g. a stale list from a just-started agent) leaves the last one in place."""
    models = catalog_from_variants(agent, variants)
    if not models:
        return
    with _catalog_lock:
        if _catalog_cache.get(agent.key) == models:
            return
        _catalog_cache[agent.key] = models
        if CATALOG_FILE:
            stored = _load_catalog_file()
            stored[agent.key] = models
            try:
                os.makedirs(os.path.dirname(CATALOG_FILE) or ".", exist_ok=True)
                tmp = f"{CATALOG_FILE}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(stored, fh, indent=2)
                os.replace(tmp, CATALOG_FILE)
            except OSError:
                pass


def model_catalog(agent: AcpAgentConfig) -> list[dict[str, Any]]:
    """Allowed models for ``agent`` as its sessions last reported them.

    Before any session has reported (first use on this machine), only the
    configured default model is offered.
    """
    with _catalog_lock:
        if agent.key not in _catalog_cache:
            stored = _load_catalog_file().get(agent.key)
            if stored:
                _catalog_cache[agent.key] = stored
        cached = _catalog_cache.get(agent.key)
    if cached:
        return cached
    if agent.model and agent.model_allowed(agent.model):
        return [{
            "id": agent.model, "display_name": agent.model, "description": "Configured default",
            "supported_efforts": list(EFFORT_SUFFIXES[:3]), "default_effort": agent.thought_level,
            "is_default": True,
        }]
    return []


# ── sign-in state ──────────────────────────────────────────────────
# What the last turn or check learned, per agent key: True, False or absent.
_signed_in: dict[str, tuple[float, bool]] = {}
SIGN_IN_TTL = 60.0


def note_signed_in(agent: AcpAgentConfig, value: bool) -> None:
    _signed_in[agent.key] = (time.monotonic(), value)


def known_sign_in(agent: AcpAgentConfig) -> bool | None:
    entry = _signed_in.get(agent.key)
    if entry and time.monotonic() - entry[0] < SIGN_IN_TTL:
        return entry[1]
    return None


def is_auth_error(exc: BaseException) -> bool:
    return isinstance(exc, AcpError) and (exc.code == AUTH_REQUIRED_CODE or "authentication required" in str(exc).lower())


# ── policy ──────────────────────────────────────────────────────────


def hidden_command_in(text: str, agent: AcpAgentConfig) -> str | None:
    """The hidden agent slash command a prompt starts with, if any."""
    match = re.match(r"\s*/([A-Za-z0-9_-]+)", text or "")
    if match and match.group(1).lower() in agent.hidden_commands:
        return match.group(1).lower()
    return None


def filter_commands(commands: list[dict[str, Any]], agent: AcpAgentConfig) -> list[dict[str, Any]]:
    return [c for c in commands if str(c.get("name", "")).lstrip("/").lower() not in agent.hidden_commands]


def command_list(commands: list[dict[str, Any]], agent: AcpAgentConfig) -> list[dict[str, str]]:
    """The agent's slash commands the user may use, as {name, description} for the composer."""
    return [
        {"name": "/" + str(c.get("name", "")).lstrip("/"), "description": str(c.get("description") or "")[:200]}
        for c in filter_commands(commands, agent) if str(c.get("name", "")).strip()
    ]


# ── prompt building ─────────────────────────────────────────────────


def build_system_append(meta) -> str:
    """Only the folder limits; MyHarness adds no persona, skills or project notes."""
    roots = "\n".join(f"- {root}" for root in utils.ALLOWED_PATHS) or "- (the working directory)"
    return f"Work only inside these folders:\n{roots}"


def build_prompt_text(user_prompt: str, *, context_summary: str | None = None, note: str | None = None) -> str:
    sections = []
    if context_summary:
        sections.append(
            "Context carried over from earlier in this MyHarness thread (your own session memory "
            f"was not available):\n{context_summary.strip()}"
        )
    if note:
        sections.append(note.strip())
    sections.append(user_prompt)
    return "\n\n".join(sections)


# ── tool presentation ───────────────────────────────────────────────


_KIND_TOOL = {
    "read": "file_read",
    "edit": "file_replace",
    "delete": "file_write",
    "move": "file_write",
    "search": "content_search",
    "execute": "shell_run",
    "fetch": "web_fetch",
}


def _tool_path(update: dict[str, Any]) -> str:
    for location in update.get("locations") or []:
        if isinstance(location, dict) and location.get("path"):
            return str(location["path"])
    return ""


COMMAND_LABEL_LIMIT = 100
_HEREDOC = re.compile(r"""^\s*(?:\S*/)?(\S+)[^\n]*<<-?\s*['"]?\w+""")


def command_label(command: str) -> str:
    """A short "Run: ..." label; the full command stays in the call's details.

    An inline script (``python3 - <<'PY' ...``) becomes "Run python3 script
    (N lines)"; other multi-line or long commands keep their first line, cut.
    """
    lines = str(command or "").strip().splitlines()
    if not lines:
        return "Run: a command"
    heredoc = _HEREDOC.match(lines[0])
    if heredoc and len(lines) > 1:
        # Every line between the command and the closing marker, blank ones too.
        body = len(lines) - 2 if len(lines) > 2 else len(lines) - 1
        return f"Run {heredoc.group(1)} script ({body} line{'s' if body != 1 else ''})"
    first = " ".join(lines[0].split())
    more = f" (+{len(lines) - 1} more line{'s' if len(lines) > 2 else ''})" if len(lines) > 1 else ""
    if len(first) > COMMAND_LABEL_LIMIT:
        first = first[:COMMAND_LABEL_LIMIT].rstrip() + "…"
    return f"Run: {first}{more}"


def tool_activity(update: dict[str, Any]) -> tuple[str, str]:
    """(native-style tool name, one-line label) for an ACP tool call."""
    kind = str(update.get("kind") or "other")
    raw = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
    title = str(update.get("title") or update.get("name") or kind)
    path = _tool_path(update)
    name = os.path.basename(path.rstrip("/\\")) if path else ""
    tool = _KIND_TOOL.get(kind, str(update.get("name") or kind))
    if kind == "execute":
        return tool, command_label(raw.get("CommandLine") or raw.get("command") or title)
    if kind == "read":
        return tool, f"Read {name or title}"
    if kind == "edit":
        return tool, f"Update {name or title}"
    if kind in ("delete", "move"):
        return tool, f"{kind.title()} {name or title}"
    if kind == "fetch":
        return tool, f"Fetch {raw.get('url') or raw.get('Url') or title}"
    if kind == "search":
        return tool, f"Search {raw.get('query') or raw.get('Query') or title}"
    return tool, title


def tool_status(status: Any) -> str:
    status = str(status or "")
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    return "running"


def diffs_in(update: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in update.get("content") or []
        if isinstance(item, dict) and item.get("type") == "diff" and item.get("path")
    ]


MAX_PROMPT_IMAGES = 8
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def image_blocks(attachments: list[dict]) -> list[dict[str, Any]]:
    """ACP image content blocks for the image attachments of a prompt.

    The attachment paths are listed in the prompt text too, so an image left
    out here (too large, unsupported type, beyond the count) stays reachable.
    """
    import base64

    blocks = []
    for attachment in attachments:
        mime = str(attachment.get("mime") or attachment.get("mime_type") or "").lower()
        path = attachment.get("path")
        if mime not in IMAGE_TYPES or not path or len(blocks) >= MAX_PROMPT_IMAGES:
            continue
        try:
            if os.path.getsize(path) > MAX_IMAGE_BYTES:
                continue
            with open(path, "rb") as fh:
                data = base64.b64encode(fh.read()).decode("ascii")
        except OSError:
            continue
        blocks.append({"type": "image", "mimeType": mime, "data": data})
    return blocks


def workspace_previews(workspace: str) -> dict[str, tuple]:
    """Previewable files at the top of the thread's workspace: path -> (mtime, size, digest).

    The same file types and change test as native's shell_run artifacts. The
    workspace is the folder MyHarness gave the agent, so no allowed-path check.
    """
    import harness_agent

    types = harness_agent._BROWSER_PREVIEW_TYPES
    snapshot = {}
    try:
        for entry in os.scandir(workspace):
            if os.path.splitext(entry.name)[1].lower() not in types or not entry.is_file(follow_symlinks=False):
                continue
            stat = entry.stat(follow_symlinks=False)
            snapshot[entry.path] = (stat.st_mtime_ns, stat.st_size, harness_agent._content_digest(entry.path, stat.st_size))
    except OSError:
        return {}
    return snapshot


def changed_previews(before: dict[str, tuple], after: dict[str, tuple]) -> list[tuple[str, str]]:
    """(path, media type) of files that are new or whose content changed."""
    import harness_agent

    types = harness_agent._BROWSER_PREVIEW_TYPES
    changed = []
    for path, (mtime, size, digest) in after.items():
        previous = before.get(path)
        if previous is not None:
            old_mtime, old_size, old_digest = previous
            if (old_mtime, old_size) == (mtime, size) or (digest is not None and digest == old_digest):
                continue
        changed.append((path, types[os.path.splitext(path)[1].lower()]))
    return sorted(changed)


def usage_metrics(provider: str, model: str, usage: dict[str, Any] | None) -> dict[str, Any]:
    """``session/prompt`` usage -> an api_metrics record (cache counted inside input)."""
    if not isinstance(usage, dict):
        return {}

    def count(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    read, write = count("cachedReadTokens"), count("cachedWriteTokens")
    metrics = {
        "provider": provider,
        "model": model,
        "input_tokens": count("inputTokens") + read + write,
        "cache_read_tokens": read,
        "cache_write_tokens": write,
        "output_tokens": count("outputTokens"),
        "reasoning_tokens": count("thoughtTokens"),
    }
    if not (metrics["input_tokens"] or metrics["output_tokens"]):
        return {}
    return metrics


# ── JSON-RPC transport ──────────────────────────────────────────────


class AcpError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class AcpTransport:
    """Newline-delimited JSON-RPC over a child's stdio, routed by ``sessionId``."""

    def __init__(self, command: list[str], cwd: str | None = None, env: dict[str, str] | None = None):
        self.command = command
        self.cwd = cwd
        self.env = env
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._session_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._request_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._reader: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_tail: list[str] = []
        self._write_lock = asyncio.Lock()
        self.closed = asyncio.Event()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and not self.closed.is_set()

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=self.env,
            limit=_LINE_LIMIT,
        )
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def stop(self) -> None:
        proc = self.proc
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        for task in (self._reader, self._stderr_task):
            if task:
                task.cancel()
        self._fail_all(AcpError("The ACP agent stopped."))
        self.closed.set()

    def on_session(self, session_id: str, handler: Callable[[dict[str, Any]], Any] | None) -> None:
        if handler is None:
            self._session_handlers.pop(session_id, None)
        else:
            self._session_handlers[session_id] = handler

    def on_request(self, session_id: str, handler: Callable[[dict[str, Any]], Any] | None) -> None:
        if handler is None:
            self._request_handlers.pop(session_id, None)
        else:
            self._request_handlers[session_id] = handler

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail[-20:])

    async def request(self, method: str, params: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        if not self.alive:
            raise AcpError("The ACP agent is not running.")
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            if timeout:
                return await asyncio.wait_for(future, timeout)
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        if self.alive:
            await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def respond(self, request_id: Any, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result or {}
        if self.alive:
            await self._write(message)

    async def _write(self, message: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()

    def _fail_all(self, exc: BaseException) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    return
                self._stderr_tail.append(line.decode("utf-8", "replace").rstrip())
                del self._stderr_tail[:-50]
        except asyncio.CancelledError:
            return

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    await self._dispatch(message)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # malformed stream: fail callers, never hang them
            self._fail_all(AcpError(f"ACP stream error: {exc}"))
        finally:
            self._fail_all(AcpError(f"The ACP agent exited. {self.stderr_tail()}".strip()))
            self.closed.set()

    async def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method and "id" in message:
            params = message.get("params") or {}
            handler = self._request_handlers.get(str(params.get("sessionId") or ""))
            if handler is None:
                await self.respond(message["id"], error={"code": -32601, "message": f"{method} is not supported"})
                return
            # Requests such as permission prompts wait on the user: never block the reader.
            asyncio.create_task(self._run_request(handler, message))
            return
        if method:
            params = message.get("params") or {}
            handler = self._session_handlers.get(str(params.get("sessionId") or ""))
            if handler is not None:
                result = handler(message)
                if asyncio.iscoroutine(result):
                    await result
            return
        request_id = message.get("id")
        future = self._pending.get(request_id) if isinstance(request_id, int) else None
        if future is None or future.done():
            return
        if "error" in message:
            error = message.get("error") or {}
            future.set_exception(AcpError(str(error.get("message") or "ACP error"), error.get("code"), error.get("data")))
        else:
            future.set_result(message.get("result") or {})

    async def _run_request(self, handler, message: dict[str, Any]) -> None:
        try:
            result = handler(message)
            if asyncio.iscoroutine(result):
                result = await result
            await self.respond(message["id"], result=result)
        except Exception as exc:
            await self.respond(message["id"], error={"code": -32603, "message": str(exc)})


# ── one agent process ───────────────────────────────────────────────


class AcpConnection:
    """A started and initialized agent process plus what it told us."""

    def __init__(self, agent: AcpAgentConfig):
        self.agent = agent
        # Commands the agent runs can reach this backend (skills use it with
        # ANTIGRAVITY_CONVERSATION_ID to find their thread; see /api/acp/session).
        env = {**os.environ, API_ENV: utils.local_api_url()}
        self.transport = AcpTransport([agent.command, *agent.args], env=env)
        self.info: dict[str, Any] = {}
        self.loaded_sessions: set[str] = set()

    async def start(self) -> None:
        await self.transport.start()
        self.info = await self.transport.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "myharness", "version": "1"},
        }, timeout=60)

    @property
    def alive(self) -> bool:
        return self.transport.alive

    @property
    def can_load(self) -> bool:
        caps = self.info.get("agentCapabilities") or {}
        return bool(caps.get("loadSession") or (caps.get("sessionCapabilities") or {}).get("load") is not None)

    @property
    def accepts_images(self) -> bool:
        caps = self.info.get("agentCapabilities") or {}
        return bool((caps.get("promptCapabilities") or {}).get("image"))

    @property
    def can_resume(self) -> bool:
        caps = self.info.get("agentCapabilities") or {}
        return (caps.get("sessionCapabilities") or {}).get("resume") is not None

    @property
    def agent_version(self) -> str:
        return str((self.info.get("agentInfo") or {}).get("version") or "")


def _option_values(config_options: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(option.get("id")): str(option.get("currentValue"))
        for option in config_options or []
        if isinstance(option, dict) and option.get("id")
    }


# ── provider ────────────────────────────────────────────────────────


class AcpRunError(RuntimeError):
    pass


class AcpProvider:
    """Runs one MyHarness turn on an ACP agent. Lives on the runtime's event loop."""

    def __init__(self, agent: AcpAgentConfig, connection_factory: Callable[[], Any], bridge: Any = None):
        self.agent = agent
        self._connection_factory = connection_factory
        self._bridge = bridge  # AcpRuntime: question socket + active turns; None in bare use
        self._reopen_commands: list[dict[str, str]] | None = None

    async def _mcp_servers(self, meta) -> list[dict[str, Any]]:
        if self._bridge is None:
            return []
        return await self._bridge.mcp_servers(meta)

    async def run(
        self,
        meta,
        user_prompt: str,
        workspace: str,
        ui,
        cancel_event: threading.Event,
        store,
        display_prompt: str | None = None,
        display_images: list[dict] | None = None,
        model: str = "",
        reasoning_effort: str = "",
        approval_mode: str = "always_ask",
        images: list[dict] | None = None,
    ) -> None:
        agent = self.agent
        provider = agent.provider
        user_prompt = (user_prompt or "").strip()
        if not user_prompt:
            ui.show_error("Empty prompt.")
            return
        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            ui.show_error(f"Workspace is not a directory: {workspace_path}")
            return

        ui.show_user_message(display_prompt if display_prompt is not None else user_prompt, display_images)

        hidden = hidden_command_in(display_prompt if display_prompt is not None else user_prompt, agent)
        if hidden:
            ui.show_error(
                f"/{hidden} runs {agent.label} unattended or with several agents, which personal sign-in "
                "does not allow from MyHarness. Use it in Antigravity's own apps instead."
            )
            ui.show_agent_finished("error")
            return
        model = (model or agent.model or "").strip()
        if model and not agent.model_allowed(model):
            ui.show_error(
                f"{model} is not available to MyHarness on personal sign-in. "
                f"Choose one of the allowed models ({', '.join(agent.allowed_models)})."
            )
            ui.show_agent_finished("error")
            return

        state = dict(getattr(meta, "acp_state", None) or {})
        if state.get("agent") not in (None, agent.key):
            state = {}
        pending_summary = state.pop("pending_context_summary", None)
        note = state.pop("resume_note", None)

        try:
            connection = await self._connection_factory()
        except Exception as exc:
            ui.show_error(f"Could not start {agent.label}: {exc}")
            ui.show_agent_finished("error")
            return

        session_id = state.get("session_id") if state.get("cwd") == str(workspace_path) else None
        fallback_summary = None
        if session_id and session_id not in connection.loaded_sessions:
            reopened = await self._load(connection, session_id, str(workspace_path), meta, store)
            if reopened is None:
                fallback_summary = self._fallback_summary(store, meta)
                ui.show_status(f"{agent.label} could not reopen its session; continuing from this thread's summary.")
                session_id = None
                state.pop("applied", None)
            elif reopened.get("configOptions"):
                # What the agent really has now, so only real changes are sent.
                state["applied"] = _option_values(reopened["configOptions"])

        try:
            if not session_id:
                result = await connection.transport.request("session/new", {
                    "cwd": str(workspace_path),
                    "mcpServers": await self._mcp_servers(meta),
                    "_meta": self._system_meta(meta, store),
                }, timeout=120)
                session_id = str(result.get("sessionId") or "")
                if not session_id:
                    raise AcpError("The agent returned no session id.")
                connection.loaded_sessions.add(session_id)
                remember_catalog(agent, variants_from_session(result))
                state = {
                    "agent": agent.key,
                    "session_id": session_id,
                    "cwd": str(workspace_path),
                    "applied": _option_values(result.get("configOptions") or []),
                }
        except AcpError as exc:
            if is_auth_error(exc):
                note_signed_in(agent, False)
                ui.show_error(
                    f"{agent.label} is not signed in. Run `{agent.command} acp --login` in a terminal, "
                    "sign in with your Google account, then send your message again."
                )
            else:
                ui.show_error(f"{agent.label} could not start a session: {exc}")
            ui.show_agent_finished("error")
            return

        note_signed_in(agent, True)
        state["agent_version"] = connection.agent_version
        if self._reopen_commands is not None:
            state["commands"] = self._reopen_commands
        meta.acp_state = state
        store.update_session(meta)

        desired = {
            "model": model,
            "thought_level": (reasoning_effort or agent.thought_level or "").lower(),
            "mode": APPROVAL_TO_MODE.get(approval_mode, agent.mode or "default"),
        }
        applied = dict(state.get("applied") or {})
        for config_id, value in desired.items():
            if not value or applied.get(config_id) == value:
                continue
            try:
                result = await connection.transport.request("session/set_config_option", {
                    "sessionId": session_id, "configId": config_id, "value": value,
                }, timeout=60)
                applied.update(_option_values(result.get("configOptions") or []) or {config_id: value})
            except AcpError as exc:
                ui.show_error(f"{agent.label} did not accept {config_id} = {value}: {exc}")
                ui.show_agent_finished("error")
                return
        state["applied"] = applied
        meta.acp_state = state
        store.update_session(meta)

        prompt_text = build_prompt_text(
            user_prompt, context_summary=pending_summary or fallback_summary, note=note,
        )
        turn = _Turn(self, connection, session_id, ui, cancel_event, provider, applied.get("model") or model,
                     workspace=str(workspace_path))
        if self._bridge is not None:
            self._bridge.register_turn(meta.id, turn)
        try:
            blocks = image_blocks(images or []) if connection.accepts_images else []
            stop_reason, usage = await turn.run(prompt_text, blocks)
        except AcpError as exc:
            state["resume_note"] = "Your previous turn in this thread ended with an error before it finished."
            meta.acp_state = state
            store.update_session(meta)
            if not connection.alive:
                connection.loaded_sessions.discard(session_id)
            ui.show_error(f"{agent.label} error: {exc}")
            ui.show_agent_finished("error")
            return
        finally:
            if self._bridge is not None:
                self._bridge.unregister_turn(meta.id, turn)

        metrics = usage_metrics(provider, applied.get("model") or model, usage)
        if metrics:
            ui.show_api_metrics(metrics)
        if turn.context:
            ui.acp_context = dict(turn.context)
            used, size = turn.context["used"], turn.context["size"]
            hook = getattr(ui, "show_context_usage", None)
            if callable(hook):
                hook(f"{min(999.9, used / size * 100):.1f}% context ({used}/{size} tokens)")

        if stop_reason == "cancelled" or cancel_event.is_set():
            state["resume_note"] = (
                "Your previous turn in this thread was stopped by the user"
                + (f" after: {turn.last_tool_title}." if turn.last_tool_title else ".")
            )
            meta.acp_state = state
            store.update_session(meta)
            ui.show_error(f"{agent.label} run cancelled.")
            return

        final_text = turn.final_text()
        if not final_text and turn.denied_titles:
            note_text = f"Stopped after you denied: {turn.denied_titles[-1]}."
            ui.show_status(note_text)
            state["resume_note"] = f"In your previous turn the user denied: {turn.denied_titles[-1]}. You stopped without replying."
        elif stop_reason in ("max_tokens", "max_turn_requests"):
            ui.show_error(f"{agent.label} stopped early ({stop_reason.replace('_', ' ')}). Send a message to continue.")
        elif stop_reason == "refusal":
            ui.show_error(f"{agent.label} declined to continue this request.")
        if turn.commands is not None:
            state["commands"] = turn.commands
        meta.acp_state = state
        store.update_session(meta)
        self._update_summary(meta, user_prompt, final_text, store)
        ui.show_agent_finished("completed")

    async def _load(self, connection: AcpConnection, session_id: str, cwd: str, meta, store) -> dict[str, Any] | None:
        """Reopen a session in this agent process; its reply, or None when it is gone.

        ``session/resume`` reconnects without replaying history; agents that only
        offer ``session/load`` replay it, which is swallowed: the transcript has it.
        """
        params = {"sessionId": session_id, "cwd": cwd, "mcpServers": await self._mcp_servers(meta),
                  "_meta": self._system_meta(meta, store)}
        methods = [m for m, ok in (("session/resume", connection.can_resume), ("session/load", connection.can_load)) if ok]
        def swallow(message: dict[str, Any]) -> None:
            update = (message.get("params") or {}).get("update") or {}
            if update.get("sessionUpdate") == "available_commands_update":
                self._reopen_commands = command_list(update.get("availableCommands") or [], self.agent)

        connection.transport.on_session(session_id, swallow)
        try:
            for method in methods:
                try:
                    result = await connection.transport.request(method, params, timeout=120)
                except AcpError:
                    if not connection.alive:
                        return None
                    continue
                remember_catalog(self.agent, variants_from_session(result))
                connection.loaded_sessions.add(session_id)
                return result
            return None
        finally:
            connection.transport.on_session(session_id, None)

    def _system_meta(self, meta, store) -> dict[str, Any]:
        return {"antigravity": {"systemPrompt": {"append": build_system_append(meta)}}}

    @staticmethod
    def _fallback_summary(store, meta) -> str | None:
        summary = store.load_codex_summary(meta.id) or {}
        parts = []
        if summary.get("working_summary"):
            parts.append(str(summary["working_summary"]))
        if summary.get("last_user_prompt"):
            parts.append(f"Last user message:\n{summary['last_user_prompt']}")
        if summary.get("last_assistant_final"):
            parts.append(f"Your last reply:\n{summary['last_assistant_final']}")
        return "\n\n".join(parts) or None

    def _update_summary(self, meta, user_prompt: str, final_text: str, store) -> None:
        previous = store.load_codex_summary(meta.id) or {}
        store.write_codex_summary(meta.id, {
            "project_id": meta.project_id,
            "task_id": meta.task_id,
            "session_id": meta.id,
            "working_summary": previous.get("working_summary", f"{self.agent.label} session managed by the harness."),
            "last_user_prompt": user_prompt[-4000:],
            "last_assistant_final": final_text[-4000:],
            "acp_session_id": (getattr(meta, "acp_state", None) or {}).get("session_id"),
            "provider": self.agent.provider,
            "message_count": meta.message_count,
        })


def _request_approval(ui, tool_name: str, detail: str) -> bool:
    hook = getattr(ui, "request_approval_for_agent", None)
    if callable(hook):
        return bool(hook(tool_name, detail, None, {}))
    return bool(ui.request_approval(tool_name, detail, None))


class _Turn:
    """One ``session/prompt``: routes updates to the UI and answers permission requests."""

    def __init__(self, provider: AcpProvider, connection: AcpConnection, session_id: str, ui,
                 cancel_event: threading.Event, provider_name: str, model: str, workspace: str | None = None):
        self.provider = provider
        # Previewable files in the workspace, to show files the agent produced
        # by other means than its edit tool (a shell command, an MCP tool).
        self.workspace = workspace
        self.agent = provider.agent
        self.connection = connection
        self.session_id = session_id
        self.ui = ui
        self.cancel_event = cancel_event
        self.provider_name = provider_name
        self.model = model
        self.texts: list[str] = []
        self._text: list[str] = []
        self._thought: list[str] = []
        self._announced: dict[str, tuple[str, str, str]] = {}
        self._hidden_calls: set[str] = set()
        self._changed: set[tuple[str, str]] = set()
        self.denied_titles: list[str] = []
        self.last_tool_title = ""
        self.context: dict[str, int] = {}
        self.commands: list[dict[str, str]] | None = None
        self.last_activity = time.monotonic()
        self.waiting_for_user = 0
        self._artifacts = self._artifact_snapshot()

    def final_text(self) -> str:
        return "\n\n".join(t.strip() for t in self.texts if t.strip())

    async def run(self, prompt_text: str, extra_blocks: list[dict[str, Any]] | None = None) -> tuple[str, dict[str, Any] | None]:
        transport = self.connection.transport
        transport.on_session(self.session_id, self._on_update)
        transport.on_request(self.session_id, self._on_request)
        cancel_sent = False
        try:
            task = asyncio.ensure_future(transport.request("session/prompt", {
                "sessionId": self.session_id,
                "prompt": [{"type": "text", "text": prompt_text}, *(extra_blocks or [])],
            }))
            started = time.monotonic()
            while not task.done():
                await asyncio.wait({task}, timeout=0.3)
                if task.done():
                    break
                now = time.monotonic()
                idle = now - self.last_activity
                timed_out = (
                    not self.waiting_for_user
                    and (idle > self.agent.idle_timeout_seconds or now - started > self.agent.timeout_seconds)
                )
                if (self.cancel_event.is_set() or timed_out) and not cancel_sent:
                    cancel_sent = True
                    if timed_out:
                        self.ui.show_error(
                            f"{self.agent.label} made no progress for "
                            f"{max(1, round(self.agent.idle_timeout_seconds / 60))} minutes, so the run was stopped."
                        )
                        self.cancel_event.set()
                    await transport.notify("session/cancel", {"sessionId": self.session_id})
                if cancel_sent and not transport.alive:
                    break
            result = task.result() if task.done() else {"stopReason": "cancelled"}
        finally:
            transport.on_session(self.session_id, None)
            transport.on_request(self.session_id, None)
            self._flush_thought()
            self._flush_text()
            self._show_new_artifacts()
        return str(result.get("stopReason") or "end_turn"), result.get("usage")

    # generated files

    def _artifact_snapshot(self) -> dict[str, tuple]:
        hook = getattr(self.ui, "show_generated_artifact", None)
        if not self.workspace or not callable(hook):
            return {}
        return workspace_previews(self.workspace)

    def _show_new_artifacts(self) -> None:
        """Show workspace files created or changed since the last look, as native does after shell_run."""
        hook = getattr(self.ui, "show_generated_artifact", None)
        if not self.workspace or not callable(hook):
            return
        after = workspace_previews(self.workspace)
        changed = changed_previews(self._artifacts, after)
        self._artifacts = after
        for path, media_type in changed:
            hook(path, media_type)

    # updates

    def _on_update(self, message: dict[str, Any]) -> None:
        if message.get("method") != "session/update":
            return
        update = (message.get("params") or {}).get("update") or {}
        self.last_activity = time.monotonic()
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            self._flush_thought()
            text = (update.get("content") or {}).get("text") or ""
            if text:
                self._text.append(text)
                _stream(self.ui, "show_assistant_delta", text)
        elif kind == "agent_thought_chunk":
            self._flush_text()
            text = (update.get("content") or {}).get("text") or ""
            if text:
                self._thought.append(text)
                _stream(self.ui, "show_thinking_delta", text)
        elif kind in ("tool_call", "tool_call_update"):
            self._flush_thought()
            self._flush_text()
            self._tool(update)
        elif kind == "plan":
            items = [
                {"content": str(entry.get("content") or "").strip(), "status": entry.get("status") or "pending"}
                for entry in update.get("entries") or []
                if isinstance(entry, dict) and str(entry.get("content") or "").strip()
            ]
            if items and hasattr(self.ui, "show_plan_update"):
                try:
                    self.ui.show_plan_update(utils.normalize_plan_items(items))
                except ValueError:
                    pass
        elif kind == "usage_update":
            used, size = update.get("used"), update.get("size")
            if isinstance(used, int) and isinstance(size, int) and size > 0:
                self.context = {"used": used, "size": size}
        elif kind == "available_commands_update":
            self.commands = command_list(update.get("availableCommands") or [], self.agent)

    def _flush_text(self) -> None:
        if self._text:
            text = "".join(self._text)
            self._text = []
            if text.strip():
                self.texts.append(text)
                self.ui.show_assistant_markdown(text)

    def _flush_thought(self) -> None:
        if self._thought:
            text = "".join(self._thought)
            self._thought = []
            if text.strip():
                self.ui.show_thinking(text)

    def _tool(self, update: dict[str, Any]) -> None:
        call_id = str(update.get("toolCallId") or "")
        if update.get("name") == ASK_TOOL_ACP_NAME or call_id in self._hidden_calls:
            # The question card already shows MyHarness's own question tool.
            self._hidden_calls.add(call_id)
            return
        tool, label = tool_activity(update)
        status = tool_status(update.get("status"))
        if update.get("title"):
            self.last_tool_title = label
        hook = getattr(self.ui, "show_provider_tool", None)
        if call_id and callable(hook):
            previous = self._announced.get(call_id)
            if previous and update.get("sessionUpdate") == "tool_call_update" and not update.get("title"):
                tool, label = previous[0], previous[1]
            entry = (tool, label, status)
            if previous != entry:
                self._announced[call_id] = entry
                hook(tool, label, status, call_id)
        if status == "completed" and update.get("kind") != "read":
            if diffs_in(update):
                self._artifacts = self._artifact_snapshot()  # reported as file changes below
            else:
                self._show_new_artifacts()
        if status == "completed":
            for diff in diffs_in(update):
                path = str(diff["path"])
                action = "created" if diff.get("oldText") in (None, "") else "modified"
                if (call_id, path) in self._changed:
                    continue
                self._changed.add((call_id, path))
                hook = getattr(self.ui, "show_observed_file_change", None)
                if callable(hook):
                    hook(path, action, self.agent.key)

    # the question tool

    async def ask_user(self, question: str, options: list[str]) -> str | None:
        """Show one question from the agent's ask_user tool; the answer or None."""
        ask = getattr(self.ui, "ask_user_question", None)
        if not callable(ask) or self.cancel_event.is_set():
            return None
        self.waiting_for_user += 1
        try:
            answer = await asyncio.to_thread(ask, question, options, True)
        finally:
            self.waiting_for_user -= 1
            self.last_activity = time.monotonic()
        return str(answer).strip() if answer is not None and str(answer).strip() else None

    # requests from the agent

    async def _on_request(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method")
        if method != "session/request_permission":
            raise AcpError(f"{method} is not supported by MyHarness")
        params = message.get("params") or {}
        tool_call = params.get("toolCall") or {}
        options = params.get("options") or []
        if self.cancel_event.is_set():
            return {"outcome": {"outcome": "cancelled"}}
        if tool_call.get("name") == ASK_TOOL_ACP_NAME:
            # MyHarness's own question tool: asking is its own confirmation.
            option = next((o for o in options if o.get("kind") == "allow_once"), None)
            if option is not None:
                return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}
        tool, label = tool_activity(tool_call)
        detail = json.dumps({
            "title": tool_call.get("title"),
            "kind": tool_call.get("kind"),
            "paths": [loc.get("path") for loc in tool_call.get("locations") or [] if isinstance(loc, dict)],
            "input": tool_call.get("rawInput"),
        }, indent=2, ensure_ascii=False)
        self.waiting_for_user += 1
        try:
            approved = await asyncio.to_thread(_request_approval, self.ui, f"{self.agent.label}: {label}", detail)
        finally:
            self.waiting_for_user -= 1
            self.last_activity = time.monotonic()
        if self.cancel_event.is_set():
            return {"outcome": {"outcome": "cancelled"}}
        wanted = ("allow_once", "allow_always") if approved else ("reject_once", "reject_always")
        option = next((o for kind in wanted for o in options if o.get("kind") == kind), None)
        if not approved:
            self.denied_titles.append(label)
        if option is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}


def _stream(ui, method_name: str, text: str) -> None:
    hook = getattr(ui, method_name, None)
    if callable(hook):
        hook(text)


# ── runtime: a background event loop owning the agent processes ────


class AcpRuntime:
    """One event loop thread; one long-lived process per agent, shared by its sessions."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._connections: dict[str, AcpConnection] = {}
        self._starting: dict[str, asyncio.Lock] = {}
        self._turns: dict[str, _Turn] = {}
        self._ask_server: asyncio.AbstractServer | None = None
        self._ask_token = secrets.token_hex(24)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(target=self._thread_main, name="acp-runtime", daemon=True)
            self._thread.start()
        self._ready.wait(10)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def _submit(self, coro, timeout: float | None = None):
        self.start()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)

    async def connection(self, agent: AcpAgentConfig) -> AcpConnection:
        lock = self._starting.setdefault(agent.key, asyncio.Lock())
        async with lock:
            current = self._connections.get(agent.key)
            if current and current.alive and current.agent == agent:
                return current
            if current:
                with contextlib.suppress(Exception):
                    await current.transport.stop()
            connection = AcpConnection(agent)
            try:
                await connection.start()
            except Exception:
                with contextlib.suppress(Exception):
                    await connection.transport.stop()
                raise
            self._connections[agent.key] = connection
            return connection

    # question bridge: acp_ask_mcp.py -> local socket -> the thread's active turn

    def register_turn(self, session_id: str, turn: "_Turn") -> None:
        self._turns[session_id] = turn

    def unregister_turn(self, session_id: str, turn: "_Turn") -> None:
        if self._turns.get(session_id) is turn:
            self._turns.pop(session_id, None)

    async def mcp_servers(self, meta) -> list[dict[str, Any]]:
        """The question MCP server for this thread, once its socket is published."""
        if not ASK_FILE or not await self._ensure_ask_server():
            return []
        return [{
            "name": ASK_SERVER_NAME,
            "command": sys.executable,
            "args": [ASK_SCRIPT],
            "env": [
                {"name": "MYHARNESS_ASK_FILE", "value": ASK_FILE},
                {"name": "MYHARNESS_SESSION_ID", "value": str(meta.id)},
            ],
        }]

    async def _ensure_ask_server(self) -> bool:
        if self._ask_server is not None:
            return True
        try:
            server = await asyncio.start_server(self._serve_question, "127.0.0.1", 0, limit=_LINE_LIMIT)
            port = server.sockets[0].getsockname()[1]
            os.makedirs(os.path.dirname(ASK_FILE) or ".", exist_ok=True)
            tmp = f"{ASK_FILE}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"port": port, "token": self._ask_token}, fh)
            os.replace(tmp, ASK_FILE)
        except OSError:
            return False
        self._ask_server = server
        return True

    async def _serve_question(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        answer = None
        try:
            request = json.loads(await reader.readline() or b"{}")
            token = str(request.get("token") or "")
            if hmac.compare_digest(token, self._ask_token):
                turn = self._turns.get(str(request.get("session_id") or ""))
                question = str(request.get("question") or "").strip()
                raw = request.get("options")
                options = [str(o) for o in raw if str(o).strip()] if isinstance(raw, list) else []
                if turn is not None and question:
                    answer = await turn.ask_user(question, options)
            writer.write((json.dumps({"answer": answer}) + "\n").encode("utf-8"))
            await writer.drain()
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    def run(self, agent: AcpAgentConfig, **kwargs: Any) -> None:
        provider = AcpProvider(agent, lambda: self.connection(agent), bridge=self)
        self._submit(provider.run(**kwargs))

    def sign_in_status(self, agent: AcpAgentConfig, timeout: float = 30) -> bool | None:
        """Whether the agent is signed in: from the last turn, else a session/list check.

        session/list creates nothing and answers "Authentication required" when
        signed out. None when it cannot tell (e.g. the agent would not start).
        """
        known = known_sign_in(agent)
        if known is not None:
            return known

        async def check() -> bool | None:
            connection = await self.connection(agent)
            try:
                await connection.transport.request("session/list", {}, timeout=timeout)
            except AcpError as exc:
                if is_auth_error(exc):
                    note_signed_in(agent, False)
                    return False
                return None
            note_signed_in(agent, True)
            return True

        try:
            return self._submit(check(), timeout=timeout + 30)
        except Exception:
            return None

    def close_session(self, agent: AcpAgentConfig, session_id: str) -> None:
        async def close():
            connection = self._connections.get(agent.key)
            if connection and connection.alive and session_id in connection.loaded_sessions:
                with contextlib.suppress(Exception):
                    await connection.transport.request("session/close", {"sessionId": session_id}, timeout=10)
                connection.loaded_sessions.discard(session_id)

        if self._loop is not None:
            with contextlib.suppress(Exception):
                self._submit(close(), timeout=15)

    def stop(self) -> None:
        loop = self._loop
        if loop is None:
            return

        async def shutdown():
            if self._ask_server is not None:
                self._ask_server.close()
                self._ask_server = None
            for connection in list(self._connections.values()):
                with contextlib.suppress(Exception):
                    await connection.transport.stop()
            self._connections.clear()

        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(shutdown(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(5)
        self._loop = None
        self._thread = None
