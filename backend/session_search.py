"""Cross-session full-text search over persisted event streams.

Extracted from web_app.py so the scan logic is unit-testable and the route
stays thin. Uses SessionStore.iter_events so per-session caps stop reading
the event file early instead of materializing the whole stream.
"""
from __future__ import annotations

import json

from web_models import EventType

MAX_HITS_PER_SESSION = 25


def searchable_event_text(event) -> tuple[str, str]:
    """Return (matched_field, text) for the text-bearing portion of an event, or ('', '')."""
    data = event.data or {}
    etype = event.type
    if etype == EventType.USER_MESSAGE:
        return "prompt", str(data.get("text") or "")
    if etype == EventType.ASSISTANT_MESSAGE:
        return "response", str(data.get("markdown") or "")
    if etype == EventType.TOOL_CALL:
        name = str(data.get("name") or "")
        args = data.get("args")
        detail = json.dumps(args, ensure_ascii=False) if args else str(data.get("status_line") or "")
        return "tool", f"{name} {detail}".strip()
    if etype == EventType.TOOL_RESULT:
        return "tool_result", str(data.get("preview") or "")[:2000]
    if etype == EventType.FILE_CHANGE:
        return "file", str(data.get("path") or "")
    return "", ""


def search_snippet(text: str, pos: int, length: int, window: int = 60) -> str:
    start = max(0, pos - window)
    end = min(len(text), pos + length + window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + " ".join(text[start:end].split()) + suffix


def search_sessions(store, query: str, project_id: str = "", limit: int = 100) -> dict:
    query = (query or "").strip()
    if not query:
        return {"query": query, "hits": [], "count": 0, "truncated": False}
    limit = min(max(1, limit), 500)
    needle = query.lower()

    projects = store.load_project_index()
    project_names = {p.id: p.name for p in projects}
    task_names = {t.id: t.name for p in projects for t in p.tasks}

    hits: list[dict] = []
    truncated = False
    for meta in store.list_sessions(project_id or None):
        per_session = 0
        for idx, event in enumerate(store.iter_events(meta.id)):
            field, text = searchable_event_text(event)
            if not text:
                continue
            pos = text.lower().find(needle)
            if pos < 0:
                continue
            hits.append({
                "session_id": meta.id,
                "session_title": meta.title or meta.id[:8],
                "project_id": meta.project_id,
                "project_name": project_names.get(meta.project_id, meta.project_id),
                "task_id": meta.task_id,
                "task_name": task_names.get(meta.task_id, meta.task_id),
                "event_index": idx,
                "type": event.type.value,
                "matched_field": field,
                "snippet": search_snippet(text, pos, len(query)),
                "created_at": event.created_at.isoformat(),
            })
            per_session += 1
            if len(hits) >= limit:
                truncated = True
                break
            if per_session >= MAX_HITS_PER_SESSION:
                break
        if len(hits) >= limit:
            truncated = True
            break

    return {"query": query, "hits": hits, "count": len(hits), "truncated": truncated}
