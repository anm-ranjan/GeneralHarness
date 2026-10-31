"""Run routes: session event WebSocket, message send/queue, approvals,
queue management, and cancellation."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel as _PydanticBaseModel

from web_models import (
    ApprovalResponse, EventEnvelope, EventType, QuestionResponse, SendMessageRequest,
)

import web_app
import web_helpers
import web_runs
from web_helpers import _emit_session_event, _queue_payload

router = APIRouter()


class _StashRequest(_PydanticBaseModel):
    text: str


@router.post("/api/sessions/{session_id}/stash")
def stash_prompt(session_id: str, req: _StashRequest):
    from web_models import QueuedMessage
    with web_app._run_lock_for(session_id):
        meta = web_app._store.load_session(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if not req.text.strip() or len(req.text) > 200_000:
            raise HTTPException(status_code=400, detail="Stash a nonempty prompt up to 200,000 characters")
        if not any(item.text == req.text for item in meta.prompt_stash):
            meta.prompt_stash.append(QueuedMessage(text=req.text))
        web_app._store.update_session(meta)
        return meta.model_dump(mode="json")


@router.delete("/api/sessions/{session_id}/stash/{prompt_id}")
def remove_stashed_prompt(session_id: str, prompt_id: str):
    with web_app._run_lock_for(session_id):
        meta = web_app._store.load_session(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Session not found")
        meta.prompt_stash = [item for item in meta.prompt_stash if item.id != prompt_id]
        web_app._store.update_session(meta)
        return meta.model_dump(mode="json")


def _request_attachments(req: SendMessageRequest) -> list:
    return req.attachments or req.images or []


@router.get("/api/sessions/{session_id}/agents")
def list_agents(session_id: str):
    """Native child agents of the active run. Codex and Claude report theirs
    through events only, so they are not listed here."""
    run = web_app._manager.get_active_run(session_id)
    if run is None or run.orchestrator is None:
        return {"agents": []}
    return {"agents": run.orchestrator.list_agents()}


@router.post("/api/sessions/{session_id}/agents/{agent_path:path}/interrupt")
def interrupt_agent(session_id: str, agent_path: str):
    """Stop one native child agent (and its descendants). The root agent is
    protected; stop the run itself to end that."""
    run = web_app._manager.get_active_run(session_id)
    if run is None or run.orchestrator is None:
        raise HTTPException(status_code=404, detail="No native orchestration run")
    target = agent_path if agent_path.startswith("/") else f"/{agent_path}"
    result = run.orchestrator.interrupt_agent("/root", target)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.websocket("/api/events")
async def global_events(websocket: WebSocket):
    """Application-level stream of run-state changes across all sessions:
    an initial snapshot of non-idle sessions, then live run_state messages."""
    await web_app._manager.connections.connect_global(websocket)
    try:
        waiting = set(web_app._manager.pending_approval_session_ids())
        asking = set(web_app._manager.pending_question_session_ids())
        sessions = []
        for meta in web_app._store.list_sessions():
            if meta.id in waiting:
                state = "waiting_approval"
            elif meta.id in asking:
                state = "waiting_input"
            else:
                state = meta.status
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
        recent = web_app._store.load_recent_turn_events(session_id, count=200)
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

    if req.mode not in {"queue", "steer"}:
        raise HTTPException(status_code=400, detail="Mode must be queue or steer")

    workspace_root = web_helpers._workspace_root_for_session(meta)
    req_attachments = _request_attachments(req)

    if req.mode == "steer":
        if not req.text.strip() or req.text.lstrip().startswith("/") or req_attachments:
            raise HTTPException(status_code=400, detail="Steering accepts a text prompt without attachments or slash commands")
        with web_app._run_lock_for(session_id):
            run = web_app._manager.get_active_run(session_id)
            if run is None or not run.steering_open or run.cancel_event.is_set():
                raise HTTPException(status_code=409, detail="This run has finished or is closing. Send the prompt as a new message.")
            meta_now = web_app._store.load_session(session_id)
            from web_models import QueuedMessage
            meta_now.steering_queue.append(QueuedMessage(text=req.text))
            web_app._store.update_session(meta_now)
            web_helpers._emit_queue_updated(session_id, meta_now)
            _emit_session_event(session_id, EventType.STATUS, {"text": "Steering saved; it will be applied at the next tool or model boundary."})
        return {"status": "steering", "session_id": session_id}

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


@router.post("/api/sessions/{session_id}/question")
async def answer_question(session_id: str, req: QuestionResponse):
    run = web_app._manager.get_active_run(session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No active run")
    answer = req.answer.strip()
    if not answer:
        raise HTTPException(status_code=422, detail="Question answer cannot be blank")
    ok = run.answer_question(req.question_id, answer)
    if not ok:
        raise HTTPException(status_code=400, detail="Question ID mismatch or expired")
    return {"status": "answered"}


@router.post("/api/sessions/{session_id}/queue/{message_id}/steer")
def steer_queued_message(session_id: str, message_id: str):
    with web_app._run_lock_for(session_id):
        meta = web_app._store.load_session(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Session not found")
        item = next((item for item in meta.message_queue if item.id == message_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Queued message not found")
        if not item.text.strip() or item.text.lstrip().startswith("/") or item.attachments or item.images:
            raise HTTPException(status_code=400, detail="Steering accepts a text prompt without attachments or slash commands")
        run = web_app._manager.get_active_run(session_id)
        if run is None or not run.steering_open or run.cancel_event.is_set():
            raise HTTPException(status_code=409, detail="This run has finished or is closing. The message remains queued.")
        meta.message_queue = [queued for queued in meta.message_queue if queued.id != message_id]
        meta.steering_queue.append(item)
        web_app._store.update_session(meta)
        web_helpers._emit_queue_updated(session_id, meta)
        _emit_session_event(session_id, EventType.STATUS, {"text": "Steering saved; it will be applied at the next tool or model boundary."})
    return {"status": "steering", "session_id": session_id, **_queue_payload(meta)}


class _QueuedMessageEdit(_PydanticBaseModel):
    text: str
    expected_text: str


@router.patch("/api/sessions/{session_id}/queue/{message_id}")
@router.patch("/api/sessions/{session_id}/steering/{message_id}")
def edit_pending_message(session_id: str, message_id: str, req: _QueuedMessageEdit):
    if len(req.text) > 200_000 or req.text.lstrip().startswith("/"):
        raise HTTPException(status_code=400, detail="Use a message up to 200,000 characters, without slash commands")
    with web_app._run_lock_for(session_id):
        meta = web_app._store.load_session(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Session not found")
        run = web_app._manager.get_active_run(session_id)
        if message_id in getattr(run, "steering_in_flight", set()):
            raise HTTPException(status_code=409, detail="This steering message is being delivered and can no longer be edited")
        item = next((q for q in [*meta.message_queue, *meta.steering_queue] if q.id == message_id), None)
        if item is None:
            raise HTTPException(status_code=409, detail="This message has already been delivered or removed")
        if not req.text.strip() and not (item.attachments or item.images):
            raise HTTPException(status_code=400, detail="A message needs text or an attachment")
        if item.text != req.expected_text:
            raise HTTPException(status_code=409, detail="This message changed in another window. Reopen it to edit the latest version")
        item.text = req.text
        web_app._store.update_session(meta)
        web_helpers._emit_queue_updated(session_id, meta)
        return {"status": "updated", **_queue_payload(meta)}


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
        filename = attachment.get("filename")
        if filename:
            web_app._store.delete_attachment(session_id, filename)
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
    run.cancel_waiters()
    return {"status": "cancelling"}
