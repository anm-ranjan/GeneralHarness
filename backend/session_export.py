"""Render a persisted session event stream as a portable Markdown (or HTML) document.

The renderer walks the same event log the live transcript replays and honors the same
turn-completion semantics as ``web_app._restore_session_messages``:

* ``/clear`` user messages reset the document with a divider.
* interrupted turns are excluded by default (pass ``include_all=True`` to keep them).

It is intentionally free of any web_app/runtime imports so it stays unit-testable in isolation.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Iterable


_MAX_TOOL_OUTPUT_CHARS = 4000


def _event_field(event: Any, name: str, default: Any = None) -> Any:
    """Read a field from either an EventEnvelope-like object or a plain dict."""
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def _event_type(event: Any) -> str:
    raw = _event_field(event, "type")
    return getattr(raw, "value", raw) or ""


def _ts_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _truncate(text: str, limit: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated {len(text) - limit} chars]"


def _render_tool_block(call: dict) -> str:
    name = call.get("name") or "tool"
    args = call.get("args")
    summary = f"🔧 {name}"
    lines = [f"<details>\n<summary>{html.escape(summary)}</summary>\n"]
    if args:
        import json

        try:
            pretty = json.dumps(args, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            pretty = str(args)
        lines.append("**Arguments**\n")
        lines.append("```json\n" + _truncate(pretty) + "\n```\n")
    result = call.get("result")
    if result:
        status = "ok" if call.get("ok", True) else "error"
        lines.append(f"**Result ({status})**\n")
        lines.append("```\n" + _truncate(result) + "\n```\n")
    lines.append("</details>\n")
    return "\n".join(lines)


def iter_turns(events: Iterable[Any]) -> list[dict]:
    """Group a flat event stream into completed turns.

    Each turn is ``{"user": str, "attachments": int, "blocks": [...], "reason": str|None,
    "cleared_before": bool}``. ``blocks`` preserve order and are dicts tagged by ``kind``
    (``assistant`` / ``tool`` / ``error``).
    """
    turns: list[dict] = []
    active: dict | None = None
    pending_tools: dict[str, dict] = {}
    cleared_pending = False

    def flush(reason: str | None) -> None:
        nonlocal active, pending_tools
        if active is not None:
            active["reason"] = reason
            turns.append(active)
        active = None
        pending_tools = {}

    for event in events:
        etype = _event_type(event)
        data = _event_field(event, "data") or {}
        if etype == "user_message":
            text = str(data.get("text") or "")
            stripped = text.strip()
            if stripped.lower() == "/clear":
                flush(None)
                cleared_pending = True
                continue
            if stripped.startswith("/"):
                continue
            flush(None)
            attachments = data.get("attachments") or data.get("images") or []
            active = {
                "user": text,
                "images": len(data.get("images") or []),
                "attachments": len(attachments),
                "blocks": [],
                "reason": None,
                "cleared_before": cleared_pending,
            }
            cleared_pending = False
        elif active is None:
            continue
        elif etype == "assistant_message":
            markdown = str(data.get("markdown") or "")
            if markdown:
                active["blocks"].append({"kind": "assistant", "text": markdown})
        elif etype == "tool_call":
            call_id = str(data.get("call_id") or "")
            entry = {
                "kind": "tool",
                "name": data.get("name") or "tool",
                "args": data.get("args"),
                "result": "",
                "ok": True,
            }
            active["blocks"].append(entry)
            if call_id:
                pending_tools[call_id] = entry
        elif etype == "tool_result":
            call_id = str(data.get("call_id") or "")
            entry = pending_tools.pop(call_id, None)
            if entry is not None:
                entry["result"] = str(data.get("preview") or "")
                entry["ok"] = bool(data.get("ok", True))
        elif etype == "error":
            active["blocks"].append({"kind": "error", "text": str(data.get("text") or "")})
        elif etype == "run_finished":
            flush(str(data.get("reason") or "completed"))

    flush(None)
    return turns


def render_session_markdown(
    meta: Any,
    events: Iterable[Any],
    project_name: str = "",
    task_name: str = "",
    include_all: bool = False,
) -> str:
    """Render a session to a Markdown document string."""
    title = _event_field(meta, "title") or _event_field(meta, "id") or "Session"
    provider = _event_field(meta, "provider") or "native"
    created = _ts_iso(_event_field(meta, "created_at"))

    out: list[str] = []
    out.append(f"# {title}\n")
    meta_lines = []
    if project_name:
        meta_lines.append(f"- **Project:** {project_name}")
    if task_name:
        meta_lines.append(f"- **Task:** {task_name}")
    working_directory = _event_field(meta, "working_directory") or ""
    if working_directory:
        meta_lines.append(f"- **Working directory:** {working_directory}")
    meta_lines.append(f"- **Provider:** {provider}")
    if created:
        meta_lines.append(f"- **Created:** {created}")
    meta_lines.append(f"- **Exported:** {datetime.now().astimezone().isoformat()}")
    out.append("\n".join(meta_lines) + "\n")

    turns = iter_turns(events)
    rendered_any = False
    for turn in turns:
        interrupted = turn.get("reason") == "interrupted"
        if interrupted and not include_all:
            continue
        if turn.get("cleared_before") and rendered_any:
            out.append("\n---\n\n_Context cleared._\n")
        rendered_any = True

        out.append("\n## 🧑 User\n")
        user_text = turn["user"].strip() or "_(empty prompt)_"
        out.append("\n".join(f"> {line}" if line else ">" for line in user_text.splitlines()))
        if turn.get("attachments"):
            out.append(f"\n> _({turn['attachments']} attachment(s))_")
        if interrupted:
            out.append("\n> _⚠️ This turn was interrupted._")
        out.append("")

        for block in turn["blocks"]:
            if block["kind"] == "assistant":
                out.append("\n## 🤖 Assistant\n")
                out.append(block["text"])
            elif block["kind"] == "tool":
                out.append("\n" + _render_tool_block(block))
            elif block["kind"] == "error":
                out.append(f"\n> **Error:** {block['text']}\n")

    if not rendered_any:
        out.append("\n_No completed turns to export._\n")

    return "\n".join(out).rstrip() + "\n"


def render_session_html(
    meta: Any,
    events: Iterable[Any],
    project_name: str = "",
    task_name: str = "",
    include_all: bool = False,
) -> str:
    """Wrap the Markdown export in a minimal standalone HTML page.

    The body is the raw Markdown inside a <pre> block — deliberately dependency-free. Consumers
    that want rich rendering should request ``format=md`` and run it through their own renderer.
    """
    title = str(_event_field(meta, "title") or _event_field(meta, "id") or "Session")
    markdown = render_session_markdown(meta, events, project_name, task_name, include_all)
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(title)}</title>\n"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;"
        "max-width:900px;margin:0 auto;padding:32px;line-height:1.5;color:#17232b;background:#f5f7f8;}"
        "pre{white-space:pre-wrap;word-wrap:break-word;background:#fff;border:1px solid #c8d2d8;"
        "border-radius:8px;padding:20px;font-family:'SFMono-Regular',Consolas,monospace;font-size:13px;}"
        "</style>\n</head>\n<body>\n<pre>"
        + html.escape(markdown)
        + "</pre>\n</body>\n</html>\n"
    )
