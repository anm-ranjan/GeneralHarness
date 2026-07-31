"""Project / task / session / chat CRUD, search, export, metrics, events,
and attachment serving."""
from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

import base64

import session_backup
import session_export
import session_search
import utils
from pydantic import BaseModel as _PydanticBaseModel
from session_store import CHATS_PROJECT_ID, CHATS_TASK_ID
from web_models import (
    CreateChatRequest,
    CreateProjectRequest,
    CreateSessionRequest,
    CreateTaskRequest,
    EventType,
    MoveSessionRequest,
    RenameProjectRequest,
    RenameSessionRequest,
    RenameTaskRequest,
    UpdateRunSettingsRequest,
)
from web_ui_adapter import _file_snapshots

import web_app
import web_helpers
import web_runs

router = APIRouter()

_CLAUDE_MODELS = (
    {"id": "sonnet", "display_name": "Sonnet", "description": "Balanced speed and intelligence"},
    {"id": "opus", "display_name": "Opus", "description": "Most capable for complex work"},
    {"id": "haiku", "display_name": "Haiku", "description": "Fastest for lightweight tasks"},
)
_CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_CODEX_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


def _active_session_ids(session_ids: list[str]) -> list[str]:
    return [sid for sid in session_ids if web_app._manager.get_active_run(sid)]


def _session_ids_for_project(project_id: str) -> list[str]:
    projects = web_app._store.load_project_index()
    for project in projects:
        if project.id == project_id:
            ids = []
            for task in project.tasks:
                ids.extend(task.sessions)
            return ids
    return []


def _session_ids_for_task(project_id: str, task_id: str) -> list[str]:
    projects = web_app._store.load_project_index()
    for project in projects:
        if project.id == project_id:
            for task in project.tasks:
                if task.id == task_id:
                    return list(task.sessions)
    return []


@router.get("/api/sessions")
def list_sessions():
    projects = web_app._store.load_project_index()
    sessions = {
        s.id: s.model_dump(mode="json")
        for s in web_app._store.list_sessions()
    }
    return {
        "projects": [p.model_dump(mode="json") for p in projects],
        "sessions": sessions,
    }


@router.get("/api/search")
def search_sessions(q: str, project_id: str = "", limit: int = 100):
    return session_search.search_sessions(web_app._store, q, project_id=project_id, limit=limit)


@router.post("/api/projects")
def create_project(req: CreateProjectRequest):
    root = web_app._resolve_path(req.root) if req.root else ""
    if root and not utils.is_path_allowed(root):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    name = (req.name or "").strip() or (os.path.basename(root) if root else "Project")
    slug_source = name or root or "project"
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", slug_source.strip().lower()).strip("_") or "project"
    proj = web_app._store.ensure_project(project_id=slug, name=name, root=root)
    return proj.model_dump(mode="json")


@router.post("/api/tasks")
def create_task(req: CreateTaskRequest):
    try:
        task = web_app._store.ensure_task(req.project_id, task_id="", name=req.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return task.model_dump(mode="json")


@router.post("/api/sessions")
async def create_session(req: CreateSessionRequest):
    if req.provider not in {"native", "codex-app-server", "claude-agent"}:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")
    if req.provider == "native" and not web_app._native_available():
        raise HTTPException(status_code=400, detail="The Native provider is not available; set MYHARNESS_API_KEY")
    if req.provider == "codex-app-server" and not web_app._codex_app_server_available():
        raise HTTPException(status_code=400, detail="Codex app-server is not available")
    if req.provider == "claude-agent" and not web_app._claude_agent_available():
        raise HTTPException(status_code=400, detail="The Claude provider is not available")
    try:
        meta = web_app._store.create_session(req.project_id, req.task_id, req.title, req.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return meta.model_dump(mode="json")


@router.post("/api/chats")
async def create_chat(req: CreateChatRequest):
    if req.provider not in {"native", "codex-app-server", "claude-agent"}:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")
    if req.provider == "native" and not web_app._native_available():
        raise HTTPException(status_code=400, detail="The Native provider is not available; set MYHARNESS_API_KEY")
    if req.provider == "codex-app-server" and not web_app._codex_app_server_available():
        raise HTTPException(status_code=400, detail="Codex app-server is not available")
    if req.provider == "claude-agent" and not web_app._claude_agent_available():
        raise HTTPException(status_code=400, detail="The Claude provider is not available")
    web_app._store.ensure_chats_bucket()
    try:
        meta = web_app._store.create_session(
            CHATS_PROJECT_ID, CHATS_TASK_ID, req.title, req.provider, kind="chat"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    meta.working_directory = web_app._resolve_path(web_app._chat_workspace_dir(meta.id))
    web_app._store.update_session(meta)
    return meta.model_dump(mode="json")


@router.patch("/api/projects/{project_id}")
def rename_project(project_id: str, req: RenameProjectRequest):
    if project_id == CHATS_PROJECT_ID:
        raise HTTPException(status_code=400, detail="The Chats collection cannot be renamed")
    try:
        proj = web_app._store.rename_project(project_id, req.name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return proj.model_dump(mode="json")


@router.patch("/api/projects/{project_id}/tasks/{task_id}")
def rename_task(project_id: str, task_id: str, req: RenameTaskRequest):
    try:
        task = web_app._store.rename_task(project_id, task_id, req.name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return task.model_dump(mode="json")


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    if project_id == CHATS_PROJECT_ID:
        raise HTTPException(status_code=400, detail="The Chats collection cannot be deleted")
    active = _active_session_ids(_session_ids_for_project(project_id))
    if active:
        raise HTTPException(status_code=409, detail=f"Cannot delete project with active run(s): {', '.join(active)}")
    try:
        removed = web_app._store.delete_project(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    for session_id in removed:
        web_app._session_messages.pop(session_id, None)
        _file_snapshots.pop(session_id, None)
        web_app._discard_run_lock(session_id)
    return {"status": "deleted", "removed_sessions": removed}


@router.delete("/api/projects/{project_id}/tasks/{task_id}")
def delete_task(project_id: str, task_id: str):
    active = _active_session_ids(_session_ids_for_task(project_id, task_id))
    if active:
        raise HTTPException(status_code=409, detail=f"Cannot delete task with active run(s): {', '.join(active)}")
    try:
        removed = web_app._store.delete_task(project_id, task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    for session_id in removed:
        web_app._session_messages.pop(session_id, None)
        _file_snapshots.pop(session_id, None)
        web_app._discard_run_lock(session_id)
    return {"status": "deleted", "removed_sessions": removed}


@router.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    if web_app._manager.get_active_run(session_id):
        raise HTTPException(status_code=409, detail="Cannot delete a session while its agent is running")
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        web_app._store.delete_session(meta.project_id, meta.task_id, session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    web_app._session_messages.pop(session_id, None)
    _file_snapshots.pop(session_id, None)
    web_app._discard_run_lock(session_id)
    return {"status": "deleted"}


@router.patch("/api/sessions/{session_id}")
def rename_session(session_id: str, req: RenameSessionRequest):
    try:
        meta = web_app._store.rename_session(session_id, req.title)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return meta.model_dump(mode="json")


@router.post("/api/sessions/{session_id}/move")
def move_session(session_id: str, req: MoveSessionRequest):
    if web_app._manager.get_active_run(session_id):
        raise HTTPException(status_code=409, detail="Cannot move a session while its agent is running")
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if req.project_id != meta.project_id:
        raise HTTPException(status_code=400, detail="Sessions can only be moved within the same project")
    try:
        moved = web_app._store.move_session(session_id, req.project_id, req.task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return moved.model_dump(mode="json")


@router.get("/api/sessions/{session_id}/attachments/{filename}")
def get_session_attachment(session_id: str, filename: str):
    if web_app._store.load_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    safe_name = os.path.basename(filename)
    path = Path(web_app._DATA_DIR) / "attachments" / session_id / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    disposition = "inline" if media_type.startswith("image/") or media_type.startswith("text/") else "attachment"
    return FileResponse(
        str(path),
        media_type=media_type,
        headers={"Content-Disposition": f'{disposition}; filename="{safe_name}"'},
    )


@router.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    events = web_app._store.load_recent_events(session_id, count=200)
    return {
        "meta": meta.model_dump(mode="json"),
        "events": [e.model_dump(mode="json") for e in events],
    }


@router.get("/api/sessions/{session_id}/model-options")
def get_session_model_options(session_id: str):
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    effective = web_runs._effective_run_settings(meta)

    if meta.provider == "codex-app-server":
        if not web_app._codex_app_server_available():
            raise HTTPException(status_code=409, detail="Codex app-server is not available")
        try:
            result = web_runs._get_codex_runtime().list_models()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not load Codex models: {exc}") from exc
        models = []
        for item in result.get("data") or []:
            if item.get("hidden"):
                continue
            efforts = [
                option.get("reasoningEffort")
                for option in item.get("supportedReasoningEfforts") or []
                if option.get("reasoningEffort")
            ]
            models.append({
                "id": item.get("model") or item.get("id"),
                "display_name": item.get("displayName") or item.get("model") or item.get("id"),
                "description": item.get("description") or "",
                "is_default": bool(item.get("isDefault")),
                "default_effort": item.get("defaultReasoningEffort"),
                "supported_efforts": efforts,
            })
        return {
            "provider": meta.provider,
            "current_model": effective["model"] or next(
                (item["id"] for item in models if item["is_default"]), ""
            ),
            "current_effort": effective["reasoning_effort"],
            "models": models,
        }

    if meta.provider == "claude-agent":
        configured_model = str(effective["model"] or "sonnet")
        current_model = next(
            (item["id"] for item in _CLAUDE_MODELS if item["id"] in configured_model.lower()),
            configured_model,
        )
        models = list(_CLAUDE_MODELS)
        if current_model == configured_model and not any(
            item["id"] == current_model for item in models
        ):
            models.insert(0, {
                "id": configured_model,
                "display_name": configured_model,
                "description": "Configured Claude model",
            })
        return {
            "provider": meta.provider,
            "current_model": current_model,
            "current_effort": effective["reasoning_effort"],
            "models": [
                {**item, "is_default": item["id"] == current_model,
                 "default_effort": "high", "supported_efforts": list(_CLAUDE_EFFORTS)}
                for item in models
            ],
        }

    return {
        "provider": "native",
        "current_model": effective["model"] or "",
        "current_effort": "",
        "models": [{
            "id": effective["model"] or "",
            "display_name": effective["model"] or "Native default",
            "description": "Configured API model",
            "is_default": True,
            "default_effort": "",
            "supported_efforts": [],
        }],
    }


@router.patch("/api/sessions/{session_id}/run-settings")
def update_session_run_settings(session_id: str, req: UpdateRunSettingsRequest):
    if web_app._manager.get_active_run(session_id):
        raise HTTPException(status_code=409, detail="Run settings cannot change while the agent is running")
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")

    model = (req.model or "").strip()
    effort = (req.reasoning_effort or "").strip().lower()
    if not model:
        raise HTTPException(status_code=400, detail="Choose a model")
    allowed_efforts = _CLAUDE_EFFORTS if meta.provider == "claude-agent" else _CODEX_EFFORTS
    if effort and effort not in allowed_efforts:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown thinking level '{effort}'. Choose from: {', '.join(allowed_efforts)}",
        )

    settings = dict(meta.run_settings or {})
    settings["model"] = model
    if effort:
        settings["reasoning_effort"] = effort
    meta.run_settings = settings
    web_app._store.update_session(meta)
    return meta.model_dump(mode="json")


@router.get("/api/sessions/{session_id}/events")
def get_session_events(session_id: str, limit: int = 200, offset: int = 0):
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    limit = min(max(1, limit), 1000)
    offset = max(0, offset)
    events = web_app._store.load_events(session_id, limit=limit, offset=offset)
    return {
        "session_id": session_id,
        "limit": limit,
        "offset": offset,
        "count": len(events),
        "total": web_app._store.event_count(session_id),
        "events": [e.model_dump(mode="json") for e in events],
    }


@router.get("/api/sessions/{session_id}/export")
def export_session(session_id: str, format: str = "md", include_all: bool = False):
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if format not in ("md", "html"):
        raise HTTPException(status_code=400, detail="Unsupported export format")
    events = web_app._store.load_events(session_id)
    project = web_app._store.get_project(meta.project_id)
    project_name = project.name if project else ""
    task_name = web_helpers._task_name_for_session(meta)
    if format == "html":
        body = session_export.render_session_html(meta, events, project_name, task_name, include_all)
        media = "text/html; charset=utf-8"
    else:
        body = session_export.render_session_markdown(meta, events, project_name, task_name, include_all)
        media = "text/markdown; charset=utf-8"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (meta.title or session_id)).strip("_") or session_id
    filename = f"{safe}.{format}"
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/sessions/{session_id}/backup")
def backup_session(session_id: str):
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    project = web_app._store.get_project(meta.project_id)
    project_name = project.name if project else ""
    task_name = web_helpers._task_name_for_session(meta)
    payload = session_backup.export_session_backup(
        web_app._store, meta, project_name, task_name
    )
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (meta.title or session_id)).strip("_") or session_id
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe}.myharness.zip"'},
    )


class _ImportSessionRequest(_PydanticBaseModel):
    data: str
    project_id: str = ""
    task_id: str = ""


@router.post("/api/sessions/import")
def import_session(req: _ImportSessionRequest):
    raw = req.data
    if raw.startswith("data:"):
        _prefix, _sep, raw = raw.partition(",")
    try:
        payload = base64.b64decode(raw, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 backup payload")
    try:
        meta = session_backup.import_session_backup(
            web_app._store, payload, project_id=req.project_id, task_id=req.task_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "imported", "session_id": meta.id, "meta": meta.model_dump(mode="json")}


@router.get("/api/sessions/{session_id}/metrics")
def get_session_metrics(session_id: str):
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    runs: list[dict] = []
    for event in web_app._store.load_events(session_id):
        if event.type == EventType.RUN_METRICS:
            entry = dict(event.data or {})
            entry["ts"] = event.created_at.isoformat()
            runs.append(entry)
    total_elapsed = round(sum(float(r.get("elapsed_s") or 0) for r in runs), 1)
    return {
        "session_id": session_id,
        "runs": runs,
        "total_runs": len(runs),
        "total_elapsed_s": total_elapsed,
        "latest": runs[-1] if runs else None,
    }
