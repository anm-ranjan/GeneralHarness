"""Run routes: session event WebSocket, message send/queue, approvals,
queue management, and cancellation."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel as _PydanticBaseModel

from web_models import ApprovalResponse, EventEnvelope, EventType, SendMessageRequest

import web_app
import web_helpers
import web_runs
from web_helpers import _emit_session_event, _queue_payload

router = APIRouter()


def _request_attachments(req: SendMessageRequest) -> list:
    return req.attachments or req.images or []


@router.websocket("/api/events")
async def global_events(websocket: WebSocket):
    """Application-level stream of run-state changes across all sessions:
    an initial snapshot of non-idle sessions, then live run_state messages."""
    await web_app._manager.connections.connect_global(websocket)
    try:
        waiting = set(web_app._manager.pending_approval_session_ids())
        sessions = []
        for meta in web_app._store.list_sessions():
            state = "waiting_approval" if meta.id in waiting else meta.status
            if state != "idle":
                sessions.append({"session_id": meta.id, "state": state})
        await websocket.send_json({"type": "run_state_snapshot", "sessions": sessions})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        web_app._manager.connections.disconnect_global(websocket)


@router.websocket("/api/sessions/{session_id}/events")
async def session_events(websocket: WebSocket, session_id: str):
    meta = web_app._store.load_session(session_id)
    if meta is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    await web_app._manager.connections.connect(session_id, websocket)
    try:
        recent = web_app._store.load_recent_events(session_id, count=200)
        total = web_app._store.event_count(session_id)
        loaded_event = EventEnvelope(
            session_id=session_id,
            type=EventType.SESSION_LOADED,
            data={
                "meta": meta.model_dump(mode="json"),
                "events": [e.model_dump(mode="json") for e in recent],
                "event_offset": max(0, total - len(recent)),
                "event_total": total,
            },
        )
        await websocket.send_json(loaded_event.model_dump(mode="json"))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        web_app._manager.connections.disconnect(session_id, websocket)


@router.post("/api/sessions/{session_id}/message")
async def send_message(session_id: str, req: SendMessageRequest):
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")

    workspace_root = web_helpers._workspace_root_for_session(meta)
    req_attachments = _request_attachments(req)

    if web_app._manager.get_active_run(session_id):
        if req.text.strip().startswith("/"):
            raise HTTPException(status_code=409, detail="Slash commands can only run when the agent is idle")
        detail = web_runs._message_block_detail(meta, req.text, workspace_root, len(req_attachments))
        if detail:
            raise HTTPException(status_code=409, detail=detail)
        saved_attachments = web_helpers._save_message_attachments(session_id, req_attachments)
        with web_app._run_lock_for(session_id):
            meta_now = web_app._store.load_session(session_id)
            if meta_now is None:
                raise HTTPException(status_code=404, detail="Session not found")
            if not web_app._manager.get_active_run(session_id):
                web_runs._start_agent_run_locked(session_id, meta_now, req.text, saved_attachments, web_helpers._workspace_root_for_session(meta_now))
                return {"status": "started", "session_id": session_id}
            web_helpers._enqueue_message_locked(session_id, meta_now, req.text, saved_attachments)
        return {"status": "queued", "session_id": session_id}

    if req.text.strip().startswith("/") and not req_attachments and await web_runs._handle_slash_command(session_id, req.text, workspace_root):
        return {"status": "command", "session_id": session_id}

    detail = web_runs._message_block_detail(meta, req.text, workspace_root, len(req_attachments))
    if detail:
        _emit_session_event(session_id, EventType.ERROR, {"text": detail})
        _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "blocked"})
        return {"status": "blocked", "session_id": session_id, "detail": detail}

    try:
        saved_attachments = web_helpers._save_message_attachments(session_id, req_attachments)
    except Exception:
        meta.status = "idle"
        web_app._store.update_session(meta)
        raise

    with web_app._run_lock_for(session_id):
        if web_app._manager.get_active_run(session_id):
            meta_now = web_app._store.load_session(session_id)
            if meta_now is None:
                raise HTTPException(status_code=404, detail="Session not found")
            web_helpers._enqueue_message_locked(session_id, meta_now, req.text, saved_attachments)
            return {"status": "queued", "session_id": session_id}
        web_runs._start_agent_run_locked(session_id, meta, req.text, saved_attachments, workspace_root)
    return {"status": "started", "session_id": session_id}


@router.post("/api/sessions/{session_id}/approval")
async def resolve_approval(session_id: str, req: ApprovalResponse):
    run = web_app._manager.get_active_run(session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No active run")
    ok = run.resolve_approval(req.approval_id, req.approved)
    if not ok:
        raise HTTPException(status_code=400, detail="Approval ID mismatch or expired")
    return {"status": "resolved", "approved": req.approved}


@router.delete("/api/sessions/{session_id}/queue/{message_id}")
def remove_queued_message(session_id: str, message_id: str):
    with web_app._run_lock_for(session_id):
        meta = web_app._store.load_session(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Session not found")
        queue = meta.message_queue or []
        removed = next((item for item in queue if item.id == message_id), None)
        if removed is None:
            raise HTTPException(status_code=404, detail="Queued message not found")
        meta.message_queue = [item for item in queue if item.id != message_id]
        web_app._store.update_session(meta)
        web_helpers._emit_queue_updated(session_id, meta)
    for attachment in removed.attachments or removed.images or []:
        path = attachment.get("path")
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
    return {"status": "removed", "session_id": session_id, **_queue_payload(meta)}


class _QueueReorderRequest(_PydanticBaseModel):
    order: list[str]


@router.post("/api/sessions/{session_id}/queue/reorder")
def reorder_queued_messages(session_id: str, req: _QueueReorderRequest):
    with web_app._run_lock_for(session_id):
        meta = web_app._store.load_session(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Session not found")
        queue = meta.message_queue or []
        by_id = {item.id: item for item in queue}
        if len(req.order) != len(queue) or set(req.order) != set(by_id):
            raise HTTPException(status_code=409, detail="Queue changed; reorder does not match the current queue")
        meta.message_queue = [by_id[message_id] for message_id in req.order]
        web_app._store.update_session(meta)
        web_helpers._emit_queue_updated(session_id, meta)
    return {"status": "reordered", "session_id": session_id, **_queue_payload(meta)}


@router.post("/api/sessions/{session_id}/cancel")
async def cancel_run(session_id: str):
    run = web_app._manager.get_active_run(session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No active run")
    run.cancel_event.set()
    if run._pending_approval_id:
        run.resolve_approval(run._pending_approval_id, False)
    return {"status": "cancelling"}
