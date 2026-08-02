"""Shared web-app helpers: workspace paths, message attachments, context
restoration, and session event emission.

Mutable server state (store, manager, session messages, locks) stays on the
``web_app`` module and is read through it at call time so tests can patch
``web_app._store`` and friends.
"""
from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

import utils
import harness_agent as agent
from web_models import EventEnvelope, EventType, QueuedMessage

import web_app


# ── workspace / path helpers ──────────────────────────────────────

def _task_name_for_session(meta) -> str:
    project = web_app._store.get_project(meta.project_id)
    if not project:
        return ""
    for task in project.tasks:
        if task.id == meta.task_id:
            return task.name
    return ""


def _workspace_root_for_session(meta) -> str:
    override = (getattr(meta, "working_directory", "") or "").strip()
    if override and utils.is_path_allowed(override) and os.path.isdir(override):
        return web_app._resolve_path(override)
    if getattr(meta, "kind", "project") == "chat":
        return web_app._resolve_path(web_app._chat_workspace_dir(meta.id))
    project_root = _project_root_for_session(meta)
    if project_root:
        return project_root
    return utils.ALLOWED_PATHS[0] if utils.ALLOWED_PATHS else os.getcwd()


def _project_root_for_session(meta) -> str:
    project = web_app._store.get_project(meta.project_id)
    if project and project.root and utils.is_path_allowed(project.root):
        return web_app._resolve_path(project.root)
    return ""


def _resolve_chdir_target(meta, raw_path: str) -> str:
    text = (raw_path or "").strip()
    if not text:
        raise ValueError("Usage: /chdir <directory> or /chdir --reset")
    expanded = os.path.expanduser(text)
    if os.path.isabs(expanded):
        target = expanded
    else:
        target = os.path.join(_workspace_root_for_session(meta), expanded)
    resolved = web_app._resolve_path(target)
    if not utils.is_path_allowed(resolved):
        raise ValueError(f"Path is outside allowed paths: {resolved}")
    if not os.path.isdir(resolved):
        raise ValueError(f"Directory does not exist: {resolved}")
    return resolved


def _path_is_inside(path: str, root: str) -> bool:
    try:
        resolved = web_app._resolve_path(os.path.expanduser(path))
        root_resolved = web_app._resolve_path(os.path.expanduser(root))
        return os.path.commonpath([resolved, root_resolved]) == root_resolved
    except ValueError:
        return False


def _codex_external_path_refs(text: str, workspace_root: str) -> list[str]:
    roots = [workspace_root] + list(utils.ALLOWED_PATHS or [])
    refs = []
    quoted = re.findall(r"[\"']((?:/|~)[^\"']+)[\"']", text)
    unquoted = re.findall(r"(?<![\w:/.-])((?:/|~)[^\s,;:)\]}]+)", text)
    for raw in [*quoted, *unquoted]:
        path = raw.rstrip(".,")
        if path.startswith("//"):
            continue
        if not any(_path_is_inside(path, root) for root in roots):
            refs.append(path)
    return list(dict.fromkeys(refs))


# ── message attachments ───────────────────────────────────────────

_IMAGE_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_MAX_ATTACHMENTS_PER_MESSAGE = 4
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def _safe_filename(name: str, fallback: str = "attachment") -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name or fallback).strip("._")
    return clean[:120] or fallback


def _is_supported_inline_image(mime: str) -> bool:
    return mime in _IMAGE_MIME_EXT


def _guess_extension(name: str, mime: str) -> str:
    if mime in _IMAGE_MIME_EXT:
        return _IMAGE_MIME_EXT[mime]
    suffix = Path(name or "").suffix
    if suffix and len(suffix) <= 12:
        return suffix
    return mimetypes.guess_extension(mime or "") or ".bin"


def _decode_attachment_payload(data: str, mime_hint: str = "") -> tuple[bytes, str]:
    mime = mime_hint or "application/octet-stream"
    payload = data
    if data.startswith("data:"):
        header, sep, body = data.partition(",")
        if not sep or ";base64" not in header:
            raise HTTPException(status_code=400, detail="Attachments must be base64 data URLs")
        mime = header[5:].split(";", 1)[0] or mime
        payload = body
    try:
        raw = base64.b64decode(payload, validate=True)
    except binascii.Error:
        raise HTTPException(status_code=400, detail="Invalid base64 attachment data")
    if len(raw) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="Attachment is larger than 10 MB")
    return raw, mime


def _decode_image_payload(data: str) -> tuple[bytes, str]:
    raw, mime = _decode_attachment_payload(data, "image/jpeg")
    if not _is_supported_inline_image(mime):
        raise HTTPException(status_code=415, detail=f"Unsupported image type: {mime}")
    return raw, mime


def _attachment_url(session_id: str, filename: str) -> str:
    return f"/api/sessions/{session_id}/attachments/{filename}"


def _save_message_attachments(session_id: str, attachments: list) -> list[dict]:
    if len(attachments) > _MAX_ATTACHMENTS_PER_MESSAGE:
        raise HTTPException(status_code=400, detail="Maximum 4 attachments per message")
    saved = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    for i, attachment in enumerate(attachments):
        data = attachment.get("data") if isinstance(attachment, dict) else attachment.data
        mime_hint = (
            attachment.get("mime", "")
            if isinstance(attachment, dict)
            else getattr(attachment, "mime", "")
        )
        name_hint = (
            attachment.get("name", "")
            if isinstance(attachment, dict)
            else getattr(attachment, "name", "")
        )
        raw, mime = _decode_attachment_payload(data, mime_hint or "")
        safe_name = _safe_filename(name_hint or "attachment")
        ext = _guess_extension(safe_name, mime)
        stem = Path(safe_name).stem or "attachment"
        filename = f"{stamp}_{i}_{stem}{ext}"
        stored = web_app._store.store_attachment(
            session_id,
            filename,
            raw,
            mime_type=mime,
            role="image" if _is_supported_inline_image(mime) else "attachment",
            display_name=name_hint or safe_name,
        )
        saved.append(stored)
    return saved


def _save_message_images(session_id: str, images: list) -> list[dict]:
    return _save_message_attachments(session_id, images)


def _attachment_data_url(attachment: dict) -> str:
    path = attachment.get("path")
    if path:
        raw = Path(path).read_bytes()
    else:
        session_id = str(attachment.get("session_id") or "")
        stored = web_app._store.read_attachment(session_id, attachment["filename"])
        if stored is None:
            raise OSError("Attachment is missing")
        raw = stored[0]
    return f"data:{attachment['mime']};base64,{base64.b64encode(raw).decode('ascii')}"


def _attachment_label(attachment: dict, include_path: bool = False) -> str:
    name = attachment.get("name") or attachment.get("filename") or "attachment"
    mime = attachment.get("mime") or "application/octet-stream"
    size = attachment.get("size")
    details = [mime]
    if isinstance(size, int):
        details.append(f"{size} bytes")
    if include_path and attachment.get("path"):
        details.append(attachment["path"])
    return f"{name} ({', '.join(details)})"


def _attachment_text(text: str, attachments: list[dict], include_paths: bool) -> str:
    lines = [text.rstrip()] if text.strip() else []
    files = [a for a in attachments if not _is_supported_inline_image(a.get("mime", ""))]
    if files:
        if lines:
            lines.append("")
        lines.append(
            "User attached file(s) available to inspect:"
            if include_paths
            else "User attached file(s):"
        )
        for attachment in files:
            lines.append(f"- {_attachment_label(attachment, include_path=include_paths)}")
    return "\n".join(lines).strip()


def _native_user_content(text: str, attachments: list[dict]) -> str | list:
    if not attachments:
        return text
    prompt_text = _attachment_text(text, attachments, include_paths=True)
    inline_images = [a for a in attachments if _is_supported_inline_image(a.get("mime", ""))]
    if not inline_images:
        return prompt_text or text
    content = []
    if prompt_text:
        content.append({"type": "text", "text": prompt_text})
    for attachment in inline_images:
        try:
            content.append({"type": "image_url", "image_url": {"url": _attachment_data_url(attachment)}})
        except OSError:
            content.append({"type": "text", "text": f"[Image attachment missing: {attachment.get('name', 'image')}]"})
    return content


def _codex_prompt_with_attachments(text: str, attachments: list[dict], workspace_root: str) -> str:
    if not attachments:
        return text
    workspace = Path(workspace_root)
    attach_dir = workspace / ".codex_attachments"
    attach_dir.mkdir(parents=True, exist_ok=True)
    lines = [text.rstrip(), "", "User attached file(s):"] if text.strip() else ["User attached file(s):"]
    for attachment in attachments:
        target = attach_dir / attachment["filename"]
        shutil.copyfile(attachment["path"], target)
        lines.append(f"- ./.codex_attachments/{attachment['filename']} ({attachment.get('mime', 'application/octet-stream')})")
    return "\n".join(lines).strip()


def _codex_prompt_with_images(text: str, images: list[dict], workspace_root: str) -> str:
    return _codex_prompt_with_attachments(text, images, workspace_root)


def _event_attachments(data: dict, session_id: str = "") -> list[dict]:
    attachments = data.get("attachments") or data.get("images") or []
    for attachment in attachments:
        if not isinstance(attachment, dict) or not attachment.get("filename"):
            continue
        owner = session_id or str(attachment.get("session_id") or "")
        if not owner:
            continue
        try:
            attachment["path"] = str(
                web_app._store.materialize_attachment(owner, attachment["filename"])
            )
        except FileNotFoundError:
            attachment.pop("path", None)
    return attachments


def _copy_attachments_to_codex_workspace(session_id: str, workspace_root: str) -> list[str]:
    workspace = Path(workspace_root)
    attach_dir = workspace / ".codex_attachments"
    copied = []
    for event in web_app._store.load_events(session_id):
        if event.type != EventType.USER_MESSAGE:
            continue
        for attachment in _event_attachments(event.data, session_id):
            filename = attachment.get("filename")
            if not filename:
                continue
            try:
                source = web_app._store.materialize_attachment(session_id, filename)
            except FileNotFoundError:
                source = Path(attachment.get("path", ""))
            if not source.is_file():
                continue
            attach_dir.mkdir(parents=True, exist_ok=True)
            target = attach_dir / filename
            if not target.exists():
                shutil.copyfile(source, target)
            copied.append(f"./.codex_attachments/{filename}")
    return copied


def _copy_images_to_codex_workspace(session_id: str, workspace_root: str) -> list[str]:
    return _copy_attachments_to_codex_workspace(session_id, workspace_root)


# ── context restoration ───────────────────────────────────────────

def _restore_session_messages(session_id: str, workspace_root: str, include_codex: bool = False) -> list:
    meta = web_app._store.load_session(session_id)
    if meta and meta.provider == "codex-app-server" and not include_codex:
        return []
    kind = getattr(meta, "kind", "project") if meta else "project"
    messages = agent.build_initial_messages(workspace_root, kind)
    pending_turn: list[dict] = []
    for event in web_app._store.load_events(session_id):
        if event.type == EventType.USER_MESSAGE:
            text = event.data.get("text", "")
            if text.strip().lower() == "/clear":
                messages = agent.build_initial_messages(workspace_root, kind)
                pending_turn = []
            elif not text.strip().startswith("/"):
                pending_turn = [{"role": "user", "content": _native_user_content(text, _event_attachments(event.data, session_id))}]
        elif event.type == EventType.ASSISTANT_MESSAGE:
            markdown = event.data.get("markdown", "")
            if markdown and pending_turn:
                pending_turn.append({"role": "assistant", "content": markdown})
        elif event.type == EventType.RUN_FINISHED:
            if event.data.get("reason") != "interrupted" and any(m.get("role") == "assistant" for m in pending_turn):
                messages.extend(pending_turn)
            pending_turn = []
    return messages


# ── event emission / queueing ─────────────────────────────────────

def _emit_session_event(session_id: str, event_type: EventType, data: dict) -> None:
    event = EventEnvelope(session_id=session_id, type=event_type, data=data)
    web_app._store.append_event(event)
    web_app._manager.emit_event(event)


def _queue_payload(meta) -> dict:
    return {
        "items": [
            {
                "id": item.id,
                "text": item.text,
                "image_count": len(item.images or []),
                "attachment_count": len(item.attachments or item.images or []),
                "created_at": item.created_at.isoformat(),
            }
            for item in (meta.message_queue or [])
        ]
    }


def _emit_queue_updated(session_id: str, meta) -> None:
    _emit_session_event(session_id, EventType.QUEUE_UPDATED, _queue_payload(meta))


def _enqueue_message_locked(session_id: str, meta, text: str, saved_attachments: list[dict]) -> QueuedMessage:
    queued = QueuedMessage(
        text=text,
        images=[a for a in saved_attachments if _is_supported_inline_image(a.get("mime", ""))],
        attachments=saved_attachments,
    )
    meta.message_queue = [*(meta.message_queue or []), queued]
    web_app._store.update_session(meta)
    _emit_queue_updated(session_id, meta)
    return queued
