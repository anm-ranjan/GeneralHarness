import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime

_BULLET = "*" if sys.platform == "win32" else "●"
_THINK_RE = re.compile(r"<think>(.*?)</think>\s*", re.DOTALL)

import requests

try:
    import tiktoken
except ImportError:
    tiktoken = None

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
except ImportError:
    Console = None
    Markdown = None
    Panel = None
    Syntax = None
    Table = None

from prompt_toolkit import PromptSession
from prompt_toolkit.filters import in_paste_mode
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

import utils
from tool_defs import READ_ONLY_TOOLS, TOOLS, WRITE_TOOL_NAMES

CONSOLE = Console() if Console is not None and utils.UI_USE_RICH != "false" else None


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def ui_print(message: str = "", style: str = None):
    if CONSOLE is not None:
        CONSOLE.print(message, style=style)
    else:
        print(message)


def ui_rule(title: str = ""):
    if CONSOLE is not None:
        CONSOLE.rule(title)
    else:
        print("-" * 60 if not title else f"{'-' * 20} {title} {'-' * 20}")


def ui_panel(title: str, body: str, style: str = None):
    if CONSOLE is not None and Panel is not None:
        CONSOLE.print(Panel(body, title=title, border_style=style))
    else:
        print(f"\n{title}\n{'-' * len(title)}")
        print(body)


def ui_code(text: str, lexer: str = "text"):
    if CONSOLE is not None and Syntax is not None:
        CONSOLE.print(Syntax(text, lexer, theme="ansi_dark", word_wrap=True))
    else:
        print(text)


def ui_markdown(text: str):
    if CONSOLE is not None and Markdown is not None:
        CONSOLE.print(Markdown(text))
    else:
        print(text)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

_PROMPT_SESSION = None


def _build_prompt_session():
    kb = KeyBindings()

    @kb.add(Keys.Enter, filter=~in_paste_mode)
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @kb.add(Keys.Enter, filter=in_paste_mode)
    def _paste_newline(event):
        event.current_buffer.insert_text("\n")

    @kb.add("escape", Keys.Enter)
    def _newline(event):
        event.current_buffer.insert_text("\n")

    return PromptSession(key_bindings=kb, multiline=True)


def setup_input():
    global _PROMPT_SESSION
    _PROMPT_SESSION = _build_prompt_session()


def prompt_input(prompt_text: str) -> str:
    return _PROMPT_SESSION.prompt(prompt_text)


# ---------------------------------------------------------------------------
# Tool selection
# ---------------------------------------------------------------------------

_WRITE_INTENT_PATTERN = re.compile(
    r"\b(write|create|overwrite|replace|edit|modify|update|patch|append|save|rewrite|refactor|fix|change|add|insert|delete|remove)\b",
    re.IGNORECASE,
)


def needs_write_tools(messages: list) -> bool:
    for msg in reversed(messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            return bool(_WRITE_INTENT_PATTERN.search(msg["content"]))
        if msg.get("role") == "assistant":
            for tc in (msg.get("tool_calls") or []):
                if tc["function"]["name"] in WRITE_TOOL_NAMES:
                    return True
    return False


def select_tools(messages: list) -> list:
    if needs_write_tools(messages):
        return TOOLS
    return READ_ONLY_TOOLS


def choose_model_for_tools(tools: list = None, model: str = None) -> str:
    if model:
        return model
    if not tools:
        return utils.READ_MODEL
    tool_names = {tool.get("function", {}).get("name") for tool in tools}
    if tool_names & WRITE_TOOL_NAMES:
        return utils.WRITE_MODEL
    return utils.READ_MODEL


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

_last_api_metrics: dict = {}
_last_api_failure: str | None = None


def get_last_api_metrics() -> dict:
    return _last_api_metrics.copy()


class _DeltaStreamer:
    """Batches streamed content deltas to the UI, filtering <think> blocks.

    Text inside <think>…</think> is suppressed (run_agent extracts the full
    trace from the assembled message afterwards). Emission stops permanently
    once tool-call deltas appear so commentary preceding tool calls does not
    stream into a bubble the transcript never persists.
    """

    def __init__(self, ui, interval: float = 0.08):
        self._ui = ui
        self._interval = interval
        self._buffer = ""
        self._out = ""
        self._in_think = False
        self._last_flush = 0.0
        self._suppressed = False

    def suppress(self) -> None:
        self._suppressed = True
        self._out = ""
        self._buffer = ""

    def feed(self, text: str) -> None:
        if self._suppressed or not text:
            return
        self._buffer += text
        self._drain()
        import time
        if self._out and (time.monotonic() - self._last_flush) >= self._interval:
            self._flush()

    def _drain(self) -> None:
        while self._buffer:
            tag = "</think>" if self._in_think else "<think>"
            idx = self._buffer.find(tag)
            if idx >= 0:
                if not self._in_think:
                    self._out += self._buffer[:idx]
                self._buffer = self._buffer[idx + len(tag):]
                self._in_think = not self._in_think
                continue
            # Hold back any suffix that could be the start of a tag split
            # across two deltas.
            keep = 0
            for probe in ("<think>", "</think>"):
                for n in range(min(len(probe) - 1, len(self._buffer)), 0, -1):
                    if self._buffer.endswith(probe[:n]):
                        keep = max(keep, n)
                        break
            emit_len = len(self._buffer) - keep
            if emit_len > 0:
                chunk = self._buffer[:emit_len]
                if not self._in_think:
                    self._out += chunk
                self._buffer = self._buffer[emit_len:]
            break

    def _flush(self) -> None:
        import time
        if self._out:
            try:
                self._ui.show_assistant_delta(self._out)
            except Exception:
                self._suppressed = True
            self._out = ""
        self._last_flush = time.monotonic()

    def close(self) -> None:
        if self._suppressed:
            return
        self._drain()
        # A held-back partial tag prefix that never completed is real text.
        if self._buffer and not self._in_think:
            self._out += self._buffer
        self._buffer = ""
        self._flush()


def _assemble_streamed_response(lines, streamer: "_DeltaStreamer | None" = None):
    """Fold OpenAI-style SSE chunks into a non-streaming response shape."""
    content_parts: list[str] = []
    tool_calls_acc: dict[int, dict] = {}
    finish_reason = None
    usage: dict = {}
    model_name = None
    for raw in lines:
        if not raw:
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not raw.startswith("data:"):
            continue
        data_str = raw[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        model_name = model_name or chunk.get("model")
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        finish_reason = choice.get("finish_reason") or finish_reason
        delta = choice.get("delta") or {}
        for tc in delta.get("tool_calls") or []:
            if streamer:
                streamer.suppress()
            idx = tc.get("index", 0)
            acc = tool_calls_acc.setdefault(
                idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            if tc.get("id"):
                acc["id"] = acc["id"] or tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                acc["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                acc["function"]["arguments"] += fn["arguments"]
        piece = delta.get("content")
        if piece:
            content_parts.append(piece)
            if streamer:
                streamer.feed(piece)
    if streamer:
        streamer.close()
    message = {"role": "assistant", "content": "".join(content_parts)}
    tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message, finish_reason, usage, model_name


_STREAM_UNSUPPORTED = object()


def _call_api_streaming(payload: dict, ui, cancel_event, payload_size: int):
    """Streaming variant of call_api. Returns _STREAM_UNSUPPORTED when the
    provider rejects the streaming request so the caller can retry plainly."""
    global _last_api_metrics, _last_api_failure
    import time
    stream_payload = {**payload, "stream": True}
    t0 = time.monotonic()
    try:
        response = requests.post(
            f"{utils.BASE_URL}/chat/completions",
            headers=utils.api_headers(),
            json=stream_payload,
            timeout=utils.NATIVE_API_TIMEOUT,
            stream=True,
        )
    except requests.exceptions.Timeout:
        _last_api_failure = "timeout"
        msg = f"API request timed out after 120s (payload {payload_size // 1024}KB). Try again."
        if ui:
            ui.show_error(msg)
        return None
    except requests.exceptions.ConnectionError as e:
        _last_api_failure = "connection"
        if ui:
            ui.show_error(f"API connection failed: {e}")
        return None
    except requests.exceptions.RequestException as e:
        _last_api_failure = "request"
        if ui:
            ui.show_error(f"API request failed: {e}")
        return None

    if response.status_code != 200:
        response.close()
        return _STREAM_UNSUPPORTED

    streamer = _DeltaStreamer(ui)

    def cancellable_lines():
        # Requests otherwise decodes text/event-stream as ISO-8859-1 when the
        # provider omits a charset. Keep bytes here; the SSE assembler decodes
        # them explicitly as UTF-8.
        for line in response.iter_lines(decode_unicode=False):
            if cancel_event is not None and cancel_event.is_set():
                return
            yield line

    try:
        message, finish_reason, usage, model_name = _assemble_streamed_response(
            cancellable_lines(), streamer
        )
    except requests.exceptions.RequestException as e:
        _last_api_failure = "request"
        if ui:
            ui.show_error(f"API stream failed: {e}")
        return None
    finally:
        response.close()

    if cancel_event is not None and cancel_event.is_set():
        _last_api_failure = "cancelled"
        return None

    elapsed = time.monotonic() - t0
    completion_tokens = usage.get("completion_tokens", 0) or max(1, len(message.get("content", "")) // 4)
    prompt_tokens = usage.get("prompt_tokens", 0)
    total_tokens = usage.get("total_tokens", 0) or (prompt_tokens + completion_tokens)
    tps = completion_tokens / elapsed if elapsed > 0 and completion_tokens > 0 else 0
    _last_api_metrics = {
        "elapsed": round(elapsed, 1),
        "tps": round(tps, 1),
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "total_tokens": total_tokens,
    }
    if ui and hasattr(ui, "show_api_metrics"):
        ui.show_api_metrics(_last_api_metrics)

    return {
        "choices": [{"message": message, "finish_reason": finish_reason or "stop"}],
        "usage": usage,
        "model": model_name or payload.get("model"),
    }


def call_api(messages: list, tools: list = None, model: str = None, ui=None, cancel_event=None) -> dict:
    global _last_api_metrics, _last_api_failure
    _last_api_failure = None
    selected_model = choose_model_for_tools(tools, model)
    payload = {"model": selected_model, "messages": messages, "max_tokens": 65000, "temperature": 0.3}
    provider_preferences = utils.build_openrouter_provider_preferences()
    if provider_preferences:
        payload["provider"] = provider_preferences
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    import time
    payload_size = len(json.dumps(payload, ensure_ascii=False))

    if utils.API_STREAMING and ui is not None and hasattr(ui, "show_assistant_delta"):
        result = _call_api_streaming(payload, ui, cancel_event, payload_size)
        if result is not _STREAM_UNSUPPORTED:
            return result
        # Streaming rejected by the provider — retry as a plain request.

    t0 = time.monotonic()
    try:
        response = requests.post(
            f"{utils.BASE_URL}/chat/completions",
            headers=utils.api_headers(),
            json=payload,
            timeout=utils.NATIVE_API_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        _last_api_failure = "timeout"
        msg = f"API request timed out after 120s (payload {payload_size // 1024}KB). Try again."
        if ui:
            ui.show_error(msg)
        else:
            ui_print(msg, style="red")
        return None
    except requests.exceptions.ConnectionError as e:
        _last_api_failure = "connection"
        msg = f"API connection failed: {e}"
        if ui:
            ui.show_error(msg)
        else:
            ui_print(msg, style="red")
        return None
    except requests.exceptions.RequestException as e:
        _last_api_failure = "request"
        msg = f"API request failed: {e}"
        if ui:
            ui.show_error(msg)
        else:
            ui_print(msg, style="red")
        return None
    elapsed = time.monotonic() - t0

    if response.status_code != 200:
        _last_api_failure = "status"
        msg = f"API Error ({response.status_code}), payload {payload_size // 1024}KB: {response.text[:300]}"
        if ui:
            ui.show_error(msg)
        else:
            ui_print(msg, style="red")
        return None

    data = response.json()
    usage = data.get("usage", {})
    completion_tokens = usage.get("completion_tokens", 0)
    prompt_tokens = usage.get("prompt_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)
    tps = completion_tokens / elapsed if elapsed > 0 and completion_tokens > 0 else 0

    _last_api_metrics = {
        "elapsed": round(elapsed, 1),
        "tps": round(tps, 1),
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "total_tokens": total_tokens,
    }

    if ui and hasattr(ui, "show_api_metrics"):
        ui.show_api_metrics(_last_api_metrics)

    return data


# ---------------------------------------------------------------------------
# Context management
# ---------------------------------------------------------------------------

_ENCODING = None
_ENCODING_LOADED = False

# Per-message token counts, keyed by a digest of the message's serialization.
# History is append-only between compactions, so re-estimating a long context
# re-encodes only the messages added since the last call.
_TOKEN_COUNT_CACHE: dict[bytes, int] = {}
_TOKEN_COUNT_CACHE_MAX = 8192


def _get_encoding():
    """Load the BPE encoding once; get_encoding is too slow to call per turn."""
    global _ENCODING, _ENCODING_LOADED
    if not _ENCODING_LOADED:
        _ENCODING_LOADED = True
        if tiktoken is not None:
            try:
                _ENCODING = tiktoken.get_encoding("cl100k_base")
            except Exception:
                _ENCODING = None
    return _ENCODING


def _count_tokens(text: str) -> int:
    encoding = _get_encoding()
    if encoding is not None:
        try:
            return len(encoding.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def estimate_tokens(messages: list) -> int:
    """Approximate token count for a message list.

    Counts each message separately and caches the result, so repeated calls
    over a growing history cost one encode per new message rather than a full
    re-encode of the whole context. The per-message split omits the few tokens
    of JSON list punctuation the old whole-blob encode included; the value is
    an estimate used for usage display and the compaction threshold.
    """
    total = 0
    for message in messages:
        blob = json.dumps(message, ensure_ascii=False, default=str)
        key = hashlib.blake2b(blob.encode("utf-8", errors="replace"), digest_size=16).digest()
        count = _TOKEN_COUNT_CACHE.get(key)
        if count is None:
            count = _count_tokens(blob)
            if len(_TOKEN_COUNT_CACHE) >= _TOKEN_COUNT_CACHE_MAX:
                _TOKEN_COUNT_CACHE.clear()
            _TOKEN_COUNT_CACHE[key] = count
        total += count
    return max(1, total)


def format_context_usage(messages: list) -> str:
    token_count = estimate_tokens(messages)
    if utils.CONTEXT_LIMIT_TOKENS <= 0:
        return f"{token_count} estimated tokens"
    usage_percent = min(999.9, (token_count / utils.CONTEXT_LIMIT_TOKENS) * 100)
    return f"{usage_percent:.1f}% context ({token_count}/{utils.CONTEXT_LIMIT_TOKENS} estimated tokens)"


def is_memory_summary_message(message: dict) -> bool:
    return (
        message.get("role") == "system"
        and isinstance(message.get("content"), str)
        and message["content"].startswith(utils.MEMORY_SUMMARY_MARKER)
    )


def split_history_for_compaction(messages: list) -> tuple:
    system_message = messages[0]
    body = messages[1:]
    existing_memory = []
    normal_body = []

    for message in body:
        if is_memory_summary_message(message):
            existing_memory.append(message)
        else:
            normal_body.append(message)

    keep_count = min(utils.KEEP_RECENT_MESSAGES, len(normal_body))
    if keep_count == 0:
        return system_message, existing_memory, normal_body, []

    old_history = normal_body[:-keep_count]
    recent_history = normal_body[-keep_count:]
    while recent_history and recent_history[0].get("role") == "tool":
        old_history.append(recent_history.pop(0))
    return system_message, existing_memory, old_history, recent_history


def strip_image_blocks_for_summary(messages: list) -> list:
    cleaned = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            omitted = 0
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    omitted += 1
                else:
                    parts.append(part)
            if omitted:
                parts.append({"type": "text", "text": f"[{omitted} image attachment(s) omitted during compaction]"})
            cleaned.append({**message, "content": parts})
        else:
            cleaned.append(message)
    return cleaned


def summarize_history(existing_memory: list, old_history: list) -> str:
    summary_prompt = [
        {
            "role": "system",
            "content": (
                "You compact chat history for a coding agent session. Retain user goals, preferences, "
                "important facts, files modified, files/directories discussed, tool calls, compacted tool "
                "outputs, commands run, exit codes, test results, decisions, and open tasks. Drop chatter. "
                "Do not invent information."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"existing_memory": existing_memory, "history_to_compact": strip_image_blocks_for_summary(old_history)},
                ensure_ascii=False,
            ),
        },
    ]
    result = call_api(summary_prompt, tools=None, model=utils.SUMMARY_MODEL)
    if result is None:
        return "Compaction failed; previous detailed history was omitted to stay within context limits."
    try:
        return result["choices"][0]["message"].get("content", "").strip()
    except (KeyError, IndexError, TypeError):
        return "Compaction failed; previous detailed history was omitted to stay within context limits."


def _bounded_ledger_text(value, limit: int = 400) -> str:
    text = _text_from_content(value).strip() if not isinstance(value, str) else value.strip()
    return text if len(text) <= limit else f"{text[:limit]}…"


def _text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def _ledger_tool_entry(name: str, arguments: dict, result: str) -> dict:
    ok = not result.lstrip().startswith("ERROR")
    entry = {"name": name, "ok": ok}
    if name == "shell_run":
        exit_match = re.search(r"^Exit code:\s*(-?\d+)", result, re.MULTILINE)
        entry.update({
            "command": arguments.get("command", ""),
            "working_directory": arguments.get("working_directory", ""),
            "exit_code": int(exit_match.group(1)) if exit_match else None,
            "evidence": _bounded_ledger_text(result),
        })
    elif name in {"shell_check", "shell_kill"}:
        entry.update({
            "job_id": arguments.get("job_id", ""),
            "evidence": _bounded_ledger_text(result),
        })
    elif name == "plan_update":
        entry.update({
            "items": arguments.get("items", []),
            "evidence": _bounded_ledger_text(result),
        })
    elif name in WRITE_TOOL_NAMES:
        content = arguments.get("content", arguments.get("new_text", arguments.get("patch_text", "")))
        digest = (
            arguments.get("_content_sha256")
            or arguments.get("_new_text_sha256")
            or arguments.get("_patch_text_sha256")
            or hashlib.sha256(str(content).encode("utf-8")).hexdigest()
        )
        entry.update({
            "path": arguments.get("file_path", ""),
            "operation": name,
            "content_sha256": digest,
            "evidence": _bounded_ledger_text(result),
        })
    elif name == "image_read":
        entry.update({
            "path": arguments.get("file_path", ""),
            "evidence": _bounded_ledger_text(result.split(utils.LOCAL_IMAGE_RESULT_MARKER, 1)[0]),
        })
    elif name == "file_read":
        entry.update({
            "path": arguments.get("file_path", ""),
            "read_mode": arguments.get("read_mode", "full"),
            "lines": arguments.get("lines"),
            "offset": arguments.get("offset"),
            "query": arguments.get("query", ""),
            "evidence": _bounded_ledger_text(result),
        })
    elif name in {"file_list", "file_search", "content_search"}:
        entry.update({
            "parameters": {key: value for key, value in arguments.items() if not key.startswith("_")},
            "matches": _bounded_ledger_text(result),
        })
    else:
        entry.update({
            "parameters": {key: value for key, value in arguments.items() if not key.startswith("_")},
            "knowledge": _bounded_ledger_text(result),
        })
    return entry


def build_deterministic_context_ledger(messages: list) -> dict:
    """Build bounded, model-free recovery state from completed local history."""
    turns = []
    active = None
    pending_tools: dict[str, tuple[str, dict]] = {}
    for message in messages:
        role = message.get("role")
        if is_memory_summary_message(message):
            raw = message.get("content", "")[len(utils.MEMORY_SUMMARY_MARKER):].strip()
            try:
                prior = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                prior = {}
            if prior.get("schema") == "myharness-context-ledger-v1":
                turns.extend(prior.get("completed_turns") or [])
        elif role == "user":
            if active:
                turns.append(active)
            active = {"user_goal": _bounded_ledger_text(message.get("content")), "tools": [], "outcome": ""}
            pending_tools = {}
        elif role == "assistant" and active is not None:
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                pending_tools[str(call.get("id") or "")] = (str(function.get("name") or ""), arguments)
            content = _bounded_ledger_text(message.get("content"))
            if content:
                active["outcome"] = content
        elif role == "tool" and active is not None:
            name, arguments = pending_tools.pop(str(message.get("tool_call_id") or ""), ("unknown", {}))
            active["tools"].append(_ledger_tool_entry(name, arguments, str(message.get("content") or "")))
    if active:
        turns.append(active)
    return {"schema": "myharness-context-ledger-v1", "completed_turns": turns}


def deterministic_restore_payload(messages: list) -> str:
    ledger = build_deterministic_context_ledger(messages)
    return (
        "[MYHARNESS COMPLETED CONTEXT RESTORE]\n"
        "Treat the JSON below as the complete prior state. It contains only completed turns and real local tool "
        "results. Do not repeat completed work. Reply only with CONTEXT_RESTORED.\n"
        f"{json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
        "[END MYHARNESS COMPLETED CONTEXT RESTORE]"
    )


def compact_history_if_needed(messages: list, ui=None, deterministic: bool = False) -> list:
    token_count = estimate_tokens(messages)
    if token_count <= utils.COMPACT_THRESHOLD_TOKENS:
        return messages

    system_message, existing_memory, old_history, recent_history = split_history_for_compaction(messages)
    if not old_history:
        return messages

    if ui:
        ui.show_compaction(token_count, 0)
    else:
        ui_print(f"\nCompacting session history ({token_count} estimated tokens)...", style="yellow")
    summary = (
        json.dumps(build_deterministic_context_ledger([system_message, *existing_memory, *old_history]),
                   ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if deterministic
        else summarize_history(existing_memory, old_history)
    )
    compacted = [
        system_message,
        {"role": "system", "content": f"{utils.MEMORY_SUMMARY_MARKER}\n{summary}"},
    ]
    compacted.extend(recent_history)
    after_tokens = estimate_tokens(compacted)
    if ui:
        ui.show_compaction(token_count, after_tokens)
    else:
        ui_print(f"Compacted to {after_tokens} estimated tokens.", style="green")
    return compacted


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _platform_info() -> str:
    import platform
    return f"{platform.system()} {platform.release()}"


def _gather_context_fragment() -> str:
    return (
        "EFFICIENT DISCOVERY: When you need broad orientation across the project — tracing a feature "
        "through backend, frontend, and tests, finding callers/imports, or locating related tests — "
        "prefer a single gather_context call that batches several independent read-only jobs (search, "
        "test_discovery, read_slices, inventory) over many sequential search/list/read turns. It runs "
        "the jobs in parallel and returns one compact evidence packet grouped by job. Do NOT use it for "
        "a single known file or a small targeted edit; gather_context never edits files. "
    )


def _workspace_fragment(workspace_root: str | None = None) -> str:
    if not workspace_root:
        return ""
    return (
        f"Current project workspace root: {workspace_root}. "
        "Treat this as your current working directory for this session. "
        "When the user refers to this project, the repo, the workspace, relative paths, or the current directory, "
        "resolve them under this workspace root. Use this path as the default directory for file_list, file_search, "
        "content_search, and as shell_run working_directory unless the user explicitly asks for another allowed path. "
    )


def _web_output_fragment() -> str:
    return (
        "WEB UI OUTPUT: When asked to display, show, or plot an image/figure, do not rely on GUI windows, "
        "matplotlib show(), open, or OS image viewers. Save the image file under the current workspace root "
        "using a browser-compatible format such as PNG, then include it in the final response as Markdown image "
        "syntax with the absolute path, for example: ![description](/absolute/path/to/plot.png). "
        "The web client will render allowed local image paths inline. "
    )


def _native_execution_fragment() -> str:
    """Describe the exact interpreter and shell used by native shell_run calls."""
    # Keep the configured path human-readable in the prompt. JSON encoding would
    # double Windows separators, obscuring the exact command the model should use.
    interpreter = f'"{utils.PYTHON_INTERPRETER}"'
    if sys.platform == "win32":
        shell_guidance = (
            "shell_run uses Windows cmd.exe. Join dependent commands with && (or separate independent "
            "commands with &); never use the POSIX semicolon separator, because cmd.exe passes it through "
            "as part of an argument. "
        )
    else:
        shell_guidance = "shell_run uses the platform POSIX shell. "
    return (
        "PYTHON AND SHELL: If you run Python through shell_run, always use this exact configured interpreter "
        f"path instead of bare python, python3, py, or interpreter discovery: {interpreter}. Quote the path "
        "when the shell requires it. "
        + shell_guidance
        + "Do not use shell redirection, an inline Python -c program, PowerShell file-writing commands, or "
        "another shell workaround to create or edit source files. For a non-trivial or multi-line script, "
        "create it with file_write or apply_patch, edit it with file_replace or apply_patch, and then execute "
        "the saved script with the configured interpreter. A compressed tool argument marked as a placeholder "
        "is not literal file content and must never be supplied as file_replace old_text; use apply_patch or "
        "read the relevant file range to recover exact text. "
    )


def _plan_fragment() -> str:
    return (
        "PLAN DISCIPLINE: For multi-step work, publish a short checklist with plan_update. Once published, "
        "it is a live status display for the user, not a one-time announcement: mark each step 'completed' "
        "in the call right after you finish it, keep exactly one step 'in_progress', and update it one last "
        "time before your final reply so nothing is left showing as unfinished. "
    )


def _clarification_fragment() -> str:
    return (
        "AMBIGUITY: When the request could reasonably mean two different things and the readings would lead "
        "to materially different work, call ask_user with one concrete question and wait for the answer "
        "rather than guessing. Ask again for each follow-up question so each one can build on the last "
        "answer. Do not use it for anything you can settle by reading the code, and do not use it to ask "
        "permission to continue. "
    )


def _build_managed_system_content(workspace_root: str | None = None) -> str:
    return (
        "You are a pragmatic coding agent with access to file listing, filename search, content search, file read, web request, "
        "file write, exact replacement, patch, shell, and reusable Harness skills. Inspect before editing. Prefer "
        "minimal changes. Use file_list to inspect project shape, content_search to find text or symbols, "
        "file_replace for small precise edits, apply_patch for multi-file edits, "
        "and shell_run to verify with tests or scripts. Never run destructive commands. "
        "file_read supports ALL file types including PDF, DOCX, and text files. "
        "CRITICAL: NEVER guess or fabricate file names, directory contents, or file contents. "
        "You MUST call file_list, file_search, or file_read to see what actually exists on disk. "
        "If the user asks to list files, search, or read — ALWAYS use the corresponding tool. "
        "Never answer from memory about what files exist. "
        "NEVER use shell_run to read, search, or list files. Do NOT use cat, type, Get-Content, "
        "dir, ls, find, grep, or Select-String via shell. Use the dedicated tools instead: "
        "file_read for reading, content_search for searching, file_list for listing. "
        "shell_run is ONLY for running scripts, tests, builds, and commands that have no tool equivalent. "
        + _gather_context_fragment()
        + _plan_fragment()
        + _clarification_fragment()
        + utils.skill_registry.prompt_fragment()
        + f"You can only access these directories: {', '.join(utils.ALLOWED_PATHS)}. "
        + _workspace_fragment(workspace_root)
        + _web_output_fragment()
        + _native_execution_fragment()
        + f"OS: {_platform_info()}. "
        + "Always search before reading if you don't know the exact path. After edits, verify when feasible. "
        + "FILE SIZE RULE: Before reading any file, check its size — use file_list on its parent "
        + "directory. If the file is over 5KB, NEVER read it in full. Use file_read with "
        + "read_mode='search', 'head', 'range', or 'tail' instead. For cross-file searches, "
        + "use content_search to find the file and line, then file_read with 'range' for context."
    )


def _build_native_system_content(workspace_root: str | None = None) -> str:
    return (
        "You are a pragmatic coding agent. You have shell_run, file_write, file_replace, "
        "apply_patch, and reusable Harness skills available. "
        f"OS: {_platform_info()}. "
        "PREFERRED: ALWAYS use shell_run for reading, searching, and listing files — its output "
        "stays compact in history. "
        "Choose commands that are valid for the current OS; do not assume GNU-specific flags or utilities. "
        "Use whatever shell approach is most efficient for the task. "
        "FALLBACK: If you are unfamiliar with shell commands or they fail, you also have "
        "file_read (with read_mode='search' for targeted lookups), content_search (ripgrep-backed "
        "text search), file_list, and file_search available as alternative tools. "
        "FILE SIZE RULE: Before reading any file, check its size first. If using shell_run, get "
        "the file size with an OS-appropriate command before reading. If using file_read, check via "
        "file_list. Files over 5KB: NEVER read in full — use partial reads, grep, or file_read with "
        "read_mode='search'/'head'/'range'/'tail'. "
        "Prefer minimal changes. Never run destructive commands (rm -rf, format, etc.). "
        "CRITICAL: NEVER guess or fabricate file names, directory contents, or file contents. "
        "Always inspect before editing. "
        + _gather_context_fragment()
        + _plan_fragment()
        + _clarification_fragment()
        + utils.skill_registry.prompt_fragment()
        + f"You can only access these directories: {', '.join(utils.ALLOWED_PATHS)}. "
        + _workspace_fragment(workspace_root)
        + _web_output_fragment()
        + _native_execution_fragment()
        + "After edits, verify when feasible. "
        "EFFICIENCY: Minimize iterations. Prefer file_list over repeated file_search calls to "
        "discover files. IMPORTANT: file_write initially sends the entire file content, and MyHarness may "
        "replace a successful large write in later model history with an explicitly marked non-literal "
        "placeholder. ALWAYS prefer file_replace or apply_patch for edits. To create a "
        "modified copy, use shell_run to copy the file then file_replace to change the specific "
        "lines. Only use file_write for small new files that do not already exist. "
        "After completing the task, respond immediately — do not add extra verification "
        "iterations unless the user asked for verification."
    )


def _build_chat_system_content(workspace_root: str | None = None) -> str:
    """Lightweight prompt for general-purpose chats.

    Deliberately free of the coding-agent workflow bindings (gather-context,
    allowed-path recitations, the strict inspect-before-edit
    file-tooling contract). Tools remain available if a chat genuinely needs
    them, and file work stays inside the chat's own scratch workspace.
    """
    workspace_line = (
        f"You have a private scratch workspace at {workspace_root}. "
        "If you need to create or run files, keep them there; treat it as the current directory "
        "and resolve relative paths under it. "
        if workspace_root
        else ""
    )
    return (
        "You are a helpful, general-purpose assistant. Answer clearly and directly. "
        "You can hold ordinary conversations and help with a wide range of topics. "
        "You also have optional tools — file_read, file_list, file_search, content_search, "
        "web_fetch, file_write, file_replace, apply_patch, and shell_run — but only reach for "
        "them when the task actually calls for reading, writing, running, or fetching something. "
        "Most questions can be answered directly without any tool call. "
        + workspace_line
        + _web_output_fragment()
        + _native_execution_fragment()
        + f"OS: {_platform_info()}. "
        + "Never run destructive commands (rm -rf, format, etc.). "
        "When you do use a tool, use its real result — never fabricate file contents or command output."
    )


def build_system_message(workspace_root: str | None = None, kind: str = "project") -> dict:
    if kind == "chat":
        content = _build_chat_system_content(workspace_root)
    elif utils.MANAGED_TOOLS:
        content = _build_managed_system_content(workspace_root)
    else:
        content = _build_native_system_content(workspace_root)
    return {"role": "system", "content": content}


def build_initial_messages(workspace_root: str | None = None, kind: str = "project") -> list:
    return [build_system_message(workspace_root, kind)]


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------

def _run_setting(ui, key: str, default):
    """Per-run setting snapshotted onto the UI adapter, falling back to the
    process-wide default for UIs without per-session settings (CLI/TUI)."""
    settings = getattr(ui, "run_settings", None) or {}
    value = settings.get(key)
    return default if value is None else value


def approval_required(tool_name: str, ui=None) -> bool:
    mode = _run_setting(ui, "approval_mode", utils.APPROVAL_MODE)
    if mode in ("never", "auto_approve", "off", "false", "0"):
        return False
    if mode in ("shell_only", "ask_shell_only"):
        return tool_name == "shell_run"
    return tool_name in {"file_write", "file_replace", "apply_patch", "shell_run"}


def request_approval(tool_name: str, arguments: dict, ui=None) -> bool:
    if not approval_required(tool_name, ui=ui):
        return True

    args_json = json.dumps(arguments, indent=2, ensure_ascii=False)
    diff_preview = utils.build_tool_diff_preview(tool_name, arguments)

    if ui:
        return ui.request_approval(tool_name, args_json, diff_preview or None)

    ui_panel("Approval required", f"Tool: {tool_name}", style="yellow")
    ui_code(utils.truncate_output(args_json, 4000), "json")
    if diff_preview:
        ui_panel("Proposed diff", "Review the changes below before approving.", style="cyan")
        ui_code(utils.truncate_output(diff_preview, utils.MAX_TOOL_OUTPUT), "diff")
    answer = input("Approve this tool call? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _ask_user(arguments: dict, ui) -> str:
    """Put one clarifying question to the user and return their answer.

    A UI without a question surface (or a user who lets it time out) yields no
    answer; the model is told to continue on its own judgement rather than
    being left to guess what the silence meant.
    """
    question = str(arguments.get("question", "")).strip()
    if not question:
        return "ERROR: ask_user requires a question."
    options = arguments.get("options") or []
    if not isinstance(options, list):
        return "ERROR: ask_user options must be a list of strings."
    ask = getattr(ui, "ask_user_question", None) if ui else None
    if not callable(ask):
        return (
            "No answer: this interface cannot ask the user questions. "
            "Proceed on your best judgement and state the assumption you made."
        )
    answer = ask(question, [str(option) for option in options], True)
    if answer is None or not str(answer).strip():
        return (
            "No answer: the user did not respond. "
            "Proceed on your best judgement and state the assumption you made."
        )
    return f"The user answered: {answer}"


def execute_tool(name: str, arguments: dict, ui=None, cancel_event=None) -> str:
    err = utils._check_required_args(name, arguments)
    if err:
        return err
    if name in {"file_search", "file_list", "content_search", "file_read", "image_read", "web_fetch", "web_request", "gather_context", "skill_list", "skill_read"}:
        return utils.execute_read_only_tool(name, arguments, cancel_event)
    if name == "shell_check":
        return utils.tool_shell_check(job_id=arguments["job_id"], tail_lines=arguments.get("tail_lines", 200))
    if name == "plan_update":
        items = arguments.get("items", [])
        result = utils.tool_plan_update(items)
        if ui and not result.startswith("ERROR") and hasattr(ui, "show_plan_update"):
            try:
                ui.show_plan_update(utils.normalize_plan_items(items))
            except ValueError:
                pass
        return result
    if name == "ask_user":
        return _ask_user(arguments, ui)
    if not request_approval(name, arguments, ui=ui):
        return "ERROR: Tool call denied by user."
    try:
        if name == "file_write":
            result = utils.tool_file_write(
                file_path=arguments["file_path"],
                content=arguments["content"],
                overwrite=arguments.get("overwrite", False),
            )
            utils._invalidate_cache()
            return result
        if name == "file_replace":
            result = utils.tool_file_replace(
                file_path=arguments["file_path"],
                old_text=arguments["old_text"],
                new_text=arguments["new_text"],
                replace_all=arguments.get("replace_all", False),
            )
            utils._invalidate_cache()
            return result
        if name == "apply_patch":
            result = utils.tool_apply_patch(patch_text=arguments["patch_text"])
            utils._invalidate_cache()
            return result
        if name == "shell_run":
            result = utils.tool_shell_run(
                command=arguments["command"],
                working_directory=arguments["working_directory"],
                timeout=arguments.get("timeout", utils.DEFAULT_SHELL_TIMEOUT),
                background=arguments.get("background", False),
                session_id=getattr(ui, "session_id", None),
            )
            utils._invalidate_cache()
            return result
        if name == "shell_kill":
            return utils.tool_shell_kill(job_id=arguments["job_id"])
    except Exception as e:
        return f"ERROR: Tool execution failed: {e}"
    return f"ERROR: Unknown tool '{name}'"


# ---------------------------------------------------------------------------
# File-change events
# ---------------------------------------------------------------------------

def _snapshot_before_write(ui, func_name: str, func_args: dict) -> None:
    if func_name in ("file_write", "file_replace"):
        path = func_args.get("file_path", "")
        if path:
            ui.snapshot_file_before_write(path)
    elif func_name == "apply_patch":
        for line in (func_args.get("patch_text", "") or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("*** Update File:") or stripped.startswith("*** Delete File:"):
                path = stripped.split(":", 1)[1].strip()
                if path:
                    ui.snapshot_file_before_write(path)


def _emit_file_changes(ui, func_name: str, func_args: dict, tool_result: str) -> None:
    if func_name == "file_write":
        path = func_args.get("file_path", "")
        action = "modified" if func_args.get("overwrite") else "created"
        ui.show_file_change(path, action, func_name)
    elif func_name == "file_replace":
        path = func_args.get("file_path", "")
        ui.show_file_change(path, "modified", func_name)
    elif func_name == "apply_patch":
        for line in tool_result.splitlines():
            line = line.strip()
            if line.startswith("OK: Updated "):
                path = line[len("OK: Updated "):].rstrip(".")
                ui.show_file_change(path, "modified", func_name)
            elif line.startswith("OK: Added "):
                path = line[len("OK: Added "):].rstrip(".")
                ui.show_file_change(path, "created", func_name)
            elif line.startswith("OK: Deleted "):
                path = line[len("OK: Deleted "):].rstrip(".")
                ui.show_file_change(path, "deleted", func_name)


_BROWSER_IMAGE_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _snapshot_shell_images(arguments: dict) -> dict[str, tuple[int, int]]:
    directory = arguments.get("working_directory", "")
    try:
        resolved = os.path.normpath(os.path.abspath(directory))
        if not os.path.isdir(resolved) or not utils.is_path_allowed(resolved):
            return {}
        snapshot = {}
        for entry in os.scandir(resolved):
            extension = os.path.splitext(entry.name)[1].lower()
            if extension not in _BROWSER_IMAGE_TYPES or not entry.is_file(follow_symlinks=False):
                continue
            stat = entry.stat(follow_symlinks=False)
            snapshot[entry.path] = (stat.st_mtime_ns, stat.st_size)
        return snapshot
    except OSError:
        return {}


def _changed_shell_images(arguments: dict, before: dict[str, tuple[int, int]]) -> list[tuple[str, str]]:
    after = _snapshot_shell_images(arguments)
    changed = []
    for path, signature in after.items():
        if before.get(path) != signature:
            extension = os.path.splitext(path)[1].lower()
            changed.append((path, _BROWSER_IMAGE_TYPES[extension]))
    return sorted(changed)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _sanitize_for_json(msg: dict) -> dict:
    cleaned = {}
    for key in ("role", "content", "tool_calls", "tool_call_id", "name"):
        if key in msg:
            cleaned[key] = msg[key]
    if isinstance(cleaned.get("content"), list):
        cleaned["content"] = strip_image_blocks_for_summary([cleaned])[0]["content"]
    if cleaned.get("tool_calls"):
        cleaned["tool_calls"] = [
            {
                "id": tc.get("id"),
                "type": "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"]
                    if isinstance(tc["function"]["arguments"], str)
                    else json.dumps(tc["function"]["arguments"], ensure_ascii=False),
                },
            }
            for tc in cleaned["tool_calls"]
        ]
    return cleaned


def log_interaction(system_msg: dict, turn_messages: list, model: str, ui=None):
    if not utils.LOG_ENABLED or not turn_messages:
        return
    try:
        os.makedirs(utils.LOG_DIR, exist_ok=True)
        filename = f"Agent_LOG_{datetime.now().strftime('%d_%m_%Y')}.json"
        filepath = os.path.join(utils.LOG_DIR, filename)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": utils.SESSION_ID,
            "model": model,
            "messages": [_sanitize_for_json(system_msg)]
            + [_sanitize_for_json(m) for m in turn_messages],
            "feedback": None,
        }

        if os.path.isfile(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                log_data = json.load(f)
        else:
            log_data = []

        log_data.append(entry)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        msg = f"Warning: could not write interaction log: {e}"
        if ui:
            ui.show_status(msg, style="yellow")
        else:
            ui_print(msg, style="yellow")


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def _tool_status_line(func_name: str, func_args: dict) -> str:
    path = func_args.get("file_path") or func_args.get("directory") or ""
    if path:
        path = os.path.basename(path.rstrip("/\\")) or path
    if func_name == "file_read":
        mode = func_args.get("read_mode", "full")
        return f"Read {path}" if mode == "full" else f"Read {path} ({mode})"
    if func_name == "image_read":
        return f"Inspect image {path}"
    if func_name == "file_write":
        return f"Write {path}"
    if func_name == "file_replace":
        return f"Update {path}"
    if func_name == "apply_patch":
        return "Apply patch"
    if func_name == "file_list":
        return f"List {path}"
    if func_name == "file_search":
        pattern = func_args.get("pattern", "")
        return f"Search {path} for '{pattern}'"
    if func_name == "content_search":
        query = func_args.get("query", "")
        if len(query) > 40:
            query = query[:37] + "..."
        return f"Grep {path} for '{query}'"
    if func_name == "shell_run":
        cmd = func_args.get("command", "")
        if len(cmd) > 60:
            cmd = cmd[:57] + "..."
        prefix = "Run (background)" if func_args.get("background") else "Run"
        return f"{prefix}: {cmd}"
    if func_name == "shell_check":
        return f"Check background job {func_args.get('job_id', '')}"
    if func_name == "shell_kill":
        return f"Stop background job {func_args.get('job_id', '')}"
    if func_name == "plan_update":
        n = len(func_args.get("items") or [])
        return f"Plan: {n} step{'s' if n != 1 else ''}"
    if func_name == "ask_user":
        return f"Ask: {func_args.get('question', '')}"
    if func_name in {"web_fetch", "web_request"}:
        return f"Fetch {utils.redact_web_url(func_args.get('url', ''))}"
    if func_name == "skill_list":
        return "Skills: list installed skills"
    if func_name == "skill_read":
        return f"Skill: {func_args.get('name', '')}"
    return func_name


def run_agent(
    user_query: str,
    messages: list,
    max_iterations: int | None = None,
    ui=None,
    cancel_event=None,
    user_content=None,
    display_images=None,
    api_caller=None,
    tools_enabled: bool = True,
):
    if max_iterations is None:
        max_iterations = int(_run_setting(ui, "max_iterations", utils.MAX_AGENT_ITERATIONS))
    verbose_tools = bool(_run_setting(ui, "verbose_tools", utils.UI_VERBOSE_TOOLS))
    if ui:
        ui.show_user_message(user_query, display_images)
    else:
        ui_panel("User", user_query, style="blue")

    user_msg = {"role": "user", "content": user_content if user_content is not None else user_query}
    turn_start_index = len(messages)
    messages.append(user_msg)
    turn_messages = [user_msg]
    turn_model = None

    tool_call_counts: dict[str, int] = {}
    MAX_REPEAT_TOOL_CALLS = 3
    total_tool_calls = 0
    checkpoint_injected = False
    interrupted = False
    published_plan: list[dict] = []
    tool_calls_since_plan_update = 0
    plan_final_nudge_used = False
    if api_caller is not None:
        model_call = api_caller
    else:
        def model_call(msgs, tools=None, ui=None):
            return call_api(msgs, tools=tools, ui=ui, cancel_event=cancel_event)

    for iteration in range(max_iterations):
        if cancel_event and cancel_event.is_set():
            interrupted = True
            if ui:
                ui.show_status("[Interrupted]")
            break
        if ui:
            ui.show_iteration(iteration + 1)
        elif verbose_tools:
            ui_print(f"Iteration {iteration + 1}", style="dim")

        active_tools = select_tools(messages) if tools_enabled else []
        result = model_call(messages, tools=active_tools, ui=ui)
        if cancel_event and cancel_event.is_set():
            interrupted = True
            if ui:
                ui.show_status("[Interrupted]")
            break
        if result is None:
            if _last_api_failure == "timeout":
                if ui:
                    ui.show_agent_finished("api timeout")
                break
            if iteration > 0:
                retry_msg = "Compressing context and retrying..."
                if ui:
                    ui.show_status(retry_msg)
                else:
                    ui_print(retry_msg, style="yellow")
                utils._aggressive_compress_tool_results(messages, turn_start_index)
                result = model_call(messages, tools=active_tools, ui=ui)
                if _last_api_failure == "timeout":
                    if ui:
                        ui.show_agent_finished("api timeout")
                    break
            if result is None:
                if ui:
                    ui.show_error("Failed to get response from API.")
                else:
                    ui_print("Failed to get response from API.", style="red")
                break

        choice = result["choices"][0]
        assistant_msg = choice["message"]
        finish_reason = choice.get("finish_reason", "")
        turn_model = turn_model or result.get("model", utils.MODEL)
        if isinstance(assistant_msg.get("content"), str):
            think_match = _THINK_RE.search(assistant_msg["content"])
            if think_match:
                think_text = think_match.group(1).strip()
                assistant_msg["content"] = _THINK_RE.sub("", assistant_msg["content"]).strip()
                if think_text and verbose_tools:
                    if ui:
                        ui.show_thinking(think_text)
                    else:
                        ui_panel("Thinking", "", style="dim")
                        ui_print(think_text, style="dim")
        messages.append(assistant_msg)
        turn_messages.append(assistant_msg)

        if assistant_msg.get("tool_calls"):
            used_write_tool = False
            for tool_call in assistant_msg["tool_calls"]:
                if cancel_event and cancel_event.is_set():
                    interrupted = True
                    if ui:
                        ui.show_status("[Interrupted]")
                    break
                func_name = tool_call["function"]["name"]
                try:
                    func_args = json.loads(tool_call["function"]["arguments"])
                except (json.JSONDecodeError, TypeError):
                    func_args = {}

                if func_name in ("file_write", "file_replace", "apply_patch"):
                    sig_parts = [func_name, func_args.get("file_path", "")]
                    if func_name == "file_write":
                        sig_parts.append(hashlib.md5(func_args.get("content", "").encode()).hexdigest()[:8])
                    elif func_name == "file_replace":
                        sig_parts.append(hashlib.md5(func_args.get("old_text", "").encode()).hexdigest()[:8])
                        sig_parts.append(hashlib.md5(func_args.get("new_text", "").encode()).hexdigest()[:8])
                    elif func_name == "apply_patch":
                        sig_parts.append(hashlib.md5(func_args.get("patch_text", "").encode()).hexdigest()[:8])
                    call_sig = ":".join(sig_parts)
                    tool_call_counts[call_sig] = tool_call_counts.get(call_sig, 0) + 1
                elif func_name == "file_search":
                    sig_parts = [func_name, func_args.get("directory", ""), func_args.get("pattern", "")]
                    call_sig = ":".join(sig_parts)
                    tool_call_counts[call_sig] = tool_call_counts.get(call_sig, 0) + 1
                elif func_name in {"web_fetch", "web_request"}:
                    sig_parts = [func_name, func_args.get("url", "")]
                    call_sig = ":".join(sig_parts)
                    tool_call_counts[call_sig] = tool_call_counts.get(call_sig, 0) + 1
                elif func_name == "file_read":
                    sig_parts = [
                        func_name,
                        func_args.get("file_path", ""),
                        func_args.get("read_mode", "full"),
                        str(func_args.get("lines", "")),
                        str(func_args.get("offset", "")),
                        func_args.get("query", ""),
                    ]
                    call_sig = ":".join(sig_parts)
                    tool_call_counts[call_sig] = tool_call_counts.get(call_sig, 0) + 1
                elif func_name == "gather_context":
                    signature_payload = json.dumps(func_args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    call_sig = f"{func_name}:{hashlib.md5(signature_payload.encode()).hexdigest()[:12]}"
                    tool_call_counts[call_sig] = tool_call_counts.get(call_sig, 0) + 1
                else:
                    call_sig = None

                if call_sig and func_name == "file_search" and tool_call_counts[call_sig] >= MAX_REPEAT_TOOL_CALLS:
                    nudge_msg = (
                        f"WARNING: You have called file_search with the same directory and pattern "
                        f"{tool_call_counts[call_sig]} times. The results will not change. "
                        f"Use what you already have, try a different pattern, or use shell_run with grep/find instead."
                    )
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": nudge_msg,
                    }
                    messages.append(tool_msg)
                    turn_messages.append(tool_msg)
                    if ui:
                        ui.show_status(f"Nudge: repeated file_search ({tool_call_counts[call_sig]}x)")
                    continue

                if call_sig and func_name in {"web_fetch", "web_request"} and tool_call_counts[call_sig] >= MAX_REPEAT_TOOL_CALLS:
                    nudge_msg = (
                        f"WARNING: You have fetched the same URL "
                        f"{tool_call_counts[call_sig]} times. The response will not change. "
                        f"Use the result you already received, or try a different URL."
                    )
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": nudge_msg,
                    }
                    messages.append(tool_msg)
                    turn_messages.append(tool_msg)
                    if ui:
                        ui.show_status(f"Nudge: repeated web fetch ({tool_call_counts[call_sig]}x)")
                    continue

                if call_sig and func_name == "file_read" and tool_call_counts[call_sig] >= MAX_REPEAT_TOOL_CALLS:
                    nudge_msg = (
                        f"WARNING: You have called file_read with identical arguments "
                        f"{tool_call_counts[call_sig]} times. The file content has not changed. "
                        f"Use the result you already have from a previous call."
                    )
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": nudge_msg,
                    }
                    messages.append(tool_msg)
                    turn_messages.append(tool_msg)
                    if ui:
                        ui.show_status(f"Nudge: repeated file_read ({tool_call_counts[call_sig]}x)")
                    continue

                if call_sig and func_name == "gather_context" and tool_call_counts[call_sig] >= MAX_REPEAT_TOOL_CALLS:
                    nudge_msg = (
                        f"WARNING: You have called gather_context with identical jobs and budget "
                        f"{tool_call_counts[call_sig]} times. The broad context packet will not improve. "
                        "Use the evidence you already have, narrow to a targeted content_search or read_slices job, "
                        "or if one known large file is the missing context, call file_read once with read_mode='full' "
                        "for that file."
                    )
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": nudge_msg,
                    }
                    messages.append(tool_msg)
                    turn_messages.append(tool_msg)
                    if ui:
                        ui.show_status(f"Nudge: repeated gather_context ({tool_call_counts[call_sig]}x)")
                    continue

                if call_sig and tool_call_counts[call_sig] > MAX_REPEAT_TOOL_CALLS:
                    loop_msg = f"Loop detected: {func_name} called {tool_call_counts[call_sig]} times on the same target. Stopping."
                    if ui:
                        ui.show_error(loop_msg)
                    else:
                        ui_print(loop_msg, style="red")
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"ERROR: {loop_msg} Do NOT call this tool again. Summarize what you have done and stop.",
                    }
                    messages.append(tool_msg)
                    turn_messages.append(tool_msg)
                    continue

                status_line = _tool_status_line(func_name, func_args)

                if ui:
                    display_args = func_args
                    if func_name in {"web_fetch", "web_request"}:
                        display_args = {**func_args, "url": utils.redact_web_url(func_args.get("url", ""))}
                    ui.show_tool_call(func_name, display_args, status_line, verbose_tools)
                elif verbose_tools:
                    ui_panel("Tool call", func_name, style="magenta")
                    ui_code(json.dumps(func_args, indent=2, ensure_ascii=False), "json")
                else:
                    ui_print(f"  {_BULLET} {status_line}", style="dim")

                if ui and hasattr(ui, 'snapshot_file_before_write'):
                    _snapshot_before_write(ui, func_name, func_args)

                shell_images_before = (
                    _snapshot_shell_images(func_args)
                    if func_name == "shell_run" and ui and hasattr(ui, "show_generated_artifact")
                    else {}
                )

                try:
                    tool_result = execute_tool(func_name, func_args, ui=ui, cancel_event=cancel_event)
                except Exception as e:
                    tool_result = f"ERROR: Tool '{func_name}' raised: {e}"

                if (
                    func_name == "shell_run"
                    and ui
                    and hasattr(ui, "show_generated_artifact")
                    and tool_result.startswith("Exit code: 0")
                ):
                    generated_images = _changed_shell_images(func_args, shell_images_before)
                    if generated_images:
                        for artifact_path, media_type in generated_images:
                            ui.show_generated_artifact(artifact_path, media_type)
                        artifact_lines = "\n".join(path for path, _media_type in generated_images)
                        tool_result += f"\n\nGenerated image artifact(s):\n{artifact_lines}"

                if ui:
                    result_preview = tool_result[:600] + ("..." if len(tool_result) > 600 else "")
                    ui.show_tool_result(func_name, result_preview, verbose_tools)
                elif verbose_tools:
                    result_preview = tool_result[:600] + ("..." if len(tool_result) > 600 else "")
                    ui_panel("Tool result", result_preview, style="green")

                if func_name in WRITE_TOOL_NAMES:
                    used_write_tool = True
                    if ui and not tool_result.startswith("ERROR"):
                        _emit_file_changes(ui, func_name, func_args, tool_result)

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": utils.slim_tool_result(tool_result),
                }
                messages.append(tool_msg)
                turn_messages.append(tool_msg)
                total_tool_calls += 1

                if func_name == "plan_update" and not tool_result.startswith("ERROR"):
                    try:
                        published_plan = utils.normalize_plan_items(func_args.get("items", []))
                    except ValueError:
                        published_plan = []
                    tool_calls_since_plan_update = 0
                else:
                    tool_calls_since_plan_update += 1

                if func_name == "file_write" and not tool_result.startswith("ERROR"):
                    utils.compress_file_write_args(assistant_msg, tool_call["id"])

            if cancel_event and cancel_event.is_set():
                interrupted = True
                break

            if (
                published_plan
                and tool_calls_since_plan_update >= utils.PLAN_REMINDER_AFTER_TOOL_CALLS
            ):
                reminder = utils.plan_reminder_message(published_plan)
                tool_calls_since_plan_update = 0
                if reminder is None:
                    published_plan = []
                else:
                    reminder_msg = {"role": "user", "content": reminder}
                    messages.append(reminder_msg)
                    turn_messages.append(reminder_msg)

            if (
                not checkpoint_injected
                and total_tool_calls >= utils.TOOL_CALL_CHECKPOINT
                and not used_write_tool
            ):
                checkpoint_injected = True
                checkpoint_msg = {
                    "role": "user",
                    "content": (
                        "[SYSTEM CHECKPOINT] You have made {n} tool calls. Pause briefly and decide whether you "
                        "already have enough information to answer or make the requested change. If yes, finish now. "
                        "If no, continue with the smallest next tool call needed; avoid rereading the same files or "
                        "repeating failed approaches."
                    ).format(n=total_tool_calls),
                }
                messages.append(checkpoint_msg)
                turn_messages.append(checkpoint_msg)
                status_text = f"Checkpoint: {total_tool_calls} tool calls — nudging toward completion"
                if ui:
                    ui.show_status(status_text)
                elif verbose_tools:
                    ui_print(f"  {_BULLET} {status_text}", style="yellow")
        elif assistant_msg.get("content"):
            # The plan is most often left stale right at the end of a turn: the
            # work is done but the last steps were never marked completed. Give
            # the model one chance to reconcile before the answer is shown.
            if published_plan and not plan_final_nudge_used:
                plan_final_nudge_used = True
                reminder = utils.plan_reminder_message(published_plan)
                if reminder:
                    reminder_msg = {
                        "role": "user",
                        "content": (
                            reminder
                            + "\nYou are about to finish this turn, so bring the plan up to date first, "
                            "then repeat your reply."
                        ),
                    }
                    messages.append(reminder_msg)
                    turn_messages.append(reminder_msg)
                    tool_calls_since_plan_update = 0
                    continue
                published_plan = []
            if ui:
                ui.show_assistant_markdown(assistant_msg["content"])
            else:
                ui_panel("Assistant", "", style="green")
                ui_markdown(assistant_msg["content"])
            break
        elif finish_reason == "stop":
            if ui:
                ui.show_agent_finished("no content returned")
            else:
                ui_print("Agent finished (no content returned).", style="dim")
            break
    else:
        if ui:
            ui.show_agent_finished("max iterations reached")
        else:
            ui_print("Max iterations reached.", style="yellow")

    if interrupted and ui:
        ui.show_agent_finished("interrupted")

    log_interaction(messages[0], turn_messages, turn_model or utils.MODEL, ui=ui)
    if interrupted:
        del messages[turn_start_index:]
    else:
        utils.compress_turn_tool_results(messages, turn_start_index)
    return messages


def compact_agent_history_if_needed(
    messages: list, ui=None, workspace_root: str | None = None, deterministic: bool = False
) -> list:
    compacted = compact_history_if_needed(messages, ui=ui, deterministic=deterministic)
    if compacted and compacted[0].get("role") == "system":
        compacted[0] = build_system_message(workspace_root)
    return compacted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_cli_args():
    parser = argparse.ArgumentParser(description="IPA LLM Coding Agent")
    parser.add_argument(
        "--auto-approve", action="store_true",
        help="Skip approval prompts for all tool calls (overrides config approval_mode).",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable tool-result caching for this session.",
    )
    parser.add_argument(
        "--tui", action="store_true",
        help="Launch the Textual TUI instead of the plain CLI.",
    )
    parser.add_argument(
        "--backend-url",
        default="",
        help="Run CLI as a remote client for an existing MyHarness backend.",
    )
    parser.add_argument(
        "--session",
        default="",
        help="Initial backend session ID for remote CLI mode.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Optional one-shot prompt for remote CLI mode.",
    )
    return parser.parse_args()


def _run_cli(cli_args):
    from cli_ui import CliUI

    ui = CliUI()
    setup_input()

    ui_rule(f"{utils.APP_NAME} Agent")
    if CONSOLE is not None and Table is not None:
        table = Table(show_header=False, box=None)
        table.add_column("Setting", style="cyan")
        table.add_column("Value")
        table.add_row("Config", utils.CONFIG_PATH)
        table.add_row("Read model", utils.READ_MODEL)
        table.add_row("Write model", utils.WRITE_MODEL)
        table.add_row("Summary model", utils.SUMMARY_MODEL)
        table.add_row("Allowed paths", ", ".join(utils.ALLOWED_PATHS))
        table.add_row("Approval mode", utils.APPROVAL_MODE)
        table.add_row("Tool cache", "enabled" if utils.CACHE_ENABLED else "disabled")
        table.add_row("Input", "Alt+Enter for newline")
        table.add_row("Ripgrep", utils.get_rg_path() or "not found; using Python fallback")
        table.add_row("Compaction threshold", f"{utils.COMPACT_THRESHOLD_TOKENS} estimated tokens")
        table.add_row("Context limit", f"{utils.CONTEXT_LIMIT_TOKENS} estimated tokens")
        table.add_row("Recent messages kept", str(utils.KEEP_RECENT_MESSAGES))
        table.add_row("Rich UI", "enabled")
        CONSOLE.print(table)
    else:
        ui_print("Coding Agent")
        ui_print(f"Config: {utils.CONFIG_PATH}")
        ui_print(f"Read model: {utils.READ_MODEL}")
        ui_print(f"Write model: {utils.WRITE_MODEL}")
        ui_print(f"Summary model: {utils.SUMMARY_MODEL}")
        ui_print(f"Allowed paths: {utils.ALLOWED_PATHS}")
        ui_print(f"Approval mode: {utils.APPROVAL_MODE}")
        ui_print(f"Tool cache: {'enabled' if utils.CACHE_ENABLED else 'disabled'}")
        ui_print("Input: Alt+Enter for newline")
        ui_print(f"Ripgrep: {utils.get_rg_path() or 'not found; using Python fallback'}")
        ui_print(f"Compaction threshold: {utils.COMPACT_THRESHOLD_TOKENS} estimated tokens")
        ui_print(f"Context limit: {utils.CONTEXT_LIMIT_TOKENS} estimated tokens")
        ui_print(f"Recent messages kept on compaction: {utils.KEEP_RECENT_MESSAGES}")
    ui_rule()

    messages = build_initial_messages()

    while True:
        try:
            user_input = ui.prompt_user_input(
                f"\nYou [{format_context_usage(messages)}]: "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            ui.show_status("\nGoodbye!", style="dim")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            ui.show_status("Goodbye!", style="dim")
            break
        if user_input.strip().lower() == "/clear":
            messages = build_initial_messages()
            ui.show_status("Context cleared.", style="green")
            continue
        if user_input.strip().lower() == "/skills" or user_input.strip().lower().startswith("/skills "):
            skill_name = user_input.strip()[len("/skills"):].strip()
            try:
                output = (
                    utils.skill_registry.read_skill(skill_name)
                    if skill_name and skill_name.lower() != "list"
                    else utils.skill_registry.catalog_text()
                )
            except (OSError, UnicodeError, ValueError) as exc:
                output = f"ERROR: {exc}"
            ui.show_status(output)
            continue

        messages = run_agent(user_input, messages, ui=ui)
        messages = compact_agent_history_if_needed(messages, ui=ui)
        ui.show_context_usage(format_context_usage(messages))


def main():
    cli_args = parse_cli_args()

    if cli_args.auto_approve:
        utils.APPROVAL_MODE = "auto_approve"
    if cli_args.no_cache:
        utils.CACHE_ENABLED = False

    if cli_args.backend_url:
        if cli_args.tui:
            print("--backend-url is currently supported for --cli mode, not --tui.")
            sys.exit(2)
        from remote_cli import run_remote_cli

        sys.exit(run_remote_cli(cli_args))

    if cli_args.tui:
        try:
            from tui_app import IpaAgentApp
        except ImportError:
            import sys
            print("Textual is not installed. Install with: pip install 'textual>=0.79'")
            sys.exit(1)
        app = IpaAgentApp()
        app.run()
    else:
        _run_cli(cli_args)


if __name__ == "__main__":
    main()
