"""Project instructions and project memory (Workspace > Notes: Instructions / Memory)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import project_knowledge as pk
import utils

import web_app

router = APIRouter()


class _InstructionsUpdate(BaseModel):
    text: str = Field(default="", max_length=pk.MAX_INSTRUCTIONS_CHARS)


class _InstructionsAppend(BaseModel):
    text: str = Field(min_length=1, max_length=pk.MAX_INSTRUCTIONS_CHARS)


class _MemoryCreate(BaseModel):
    body: str = Field(default="", max_length=pk.MAX_BODY_CHARS)
    title: str | None = Field(default=None, max_length=pk.MAX_TITLE_CHARS)
    description: str | None = Field(default=None, max_length=pk.MAX_DESCRIPTION_CHARS)
    tags: list[str] = Field(default_factory=list, max_length=pk.MAX_TAGS)
    pinned: bool = False
    source_session_id: str | None = None
    # Ask the summary model for the one-line description when none is given
    # (Notes tab "Save as Project Memory"); falls back to the first sentence.
    describe: bool = False


class _MemoryUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=pk.MAX_TITLE_CHARS)
    description: str | None = Field(default=None, max_length=pk.MAX_DESCRIPTION_CHARS)
    body: str | None = Field(default=None, max_length=pk.MAX_BODY_CHARS)
    tags: list[str] | None = Field(default=None, max_length=pk.MAX_TAGS)
    pinned: bool | None = None


class _MemoryRestore(BaseModel):
    entry: dict[str, Any]


class _ReplyToMemory(BaseModel):
    # Transcript items carry no event id, so the client sends the reply and
    # the prompt it answered; both are capped again before the model call.
    reply: str = Field(min_length=1, max_length=200_000)
    prompt: str = Field(default="", max_length=200_000)


def _require_project(project_id: str) -> None:
    if not pk.has_project(project_id):
        raise HTTPException(status_code=400, detail="Chats have no project instructions or memory")
    if web_app._store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")


@router.get("/api/projects/{project_id}/instructions")
def get_project_instructions(project_id: str):
    _require_project(project_id)
    return pk.load_instructions(web_app._store, project_id)


@router.put("/api/projects/{project_id}/instructions")
def put_project_instructions(project_id: str, req: _InstructionsUpdate):
    _require_project(project_id)
    try:
        return pk.save_instructions(web_app._store, project_id, req.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/projects/{project_id}/instructions/append")
def append_project_instructions(project_id: str, req: _InstructionsAppend):
    """Add a section to the instructions; ``previous_text`` lets the client undo."""
    _require_project(project_id)
    addition = req.text.strip()
    if not addition:
        raise HTTPException(status_code=400, detail="Nothing to add")
    with web_app._store._lock:
        previous = pk.load_instructions(web_app._store, project_id)["text"]
        text = f"{previous.rstrip()}\n\n{addition}" if previous.strip() else addition
        try:
            saved = pk.save_instructions(web_app._store, project_id, text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return {**saved, "previous_text": previous}


@router.get("/api/projects/{project_id}/memory")
def list_project_memory(project_id: str):
    _require_project(project_id)
    return {"entries": pk.list_memory(web_app._store, project_id), "limit": pk.MAX_MEMORY_ENTRIES}


@router.post("/api/projects/{project_id}/memory")
def create_project_memory(project_id: str, req: _MemoryCreate):
    _require_project(project_id)
    body = req.body.strip()
    derived_title, derived_description = pk.derive_title_and_description(body)
    title = (req.title or "").strip() or derived_title
    if not title:
        raise HTTPException(status_code=400, detail="A memory needs a title or some text")
    description = (req.description or "").strip()
    if not description and req.describe and body:
        import harness_agent as agent

        description = pk.describe(body, agent.call_api, utils.SUMMARY_MODEL)
    if not description:
        description = derived_description
    source: dict[str, str] = {}
    if req.source_session_id:
        meta = web_app._store.load_session(req.source_session_id)
        if meta is not None and meta.project_id == project_id:
            source = {"session_id": meta.id, "session_title": meta.title}
    try:
        return pk.add_memory(
            web_app._store,
            project_id,
            {"title": title, "description": description, "body": body, "tags": req.tags, "pinned": req.pinned},
            source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/projects/{project_id}/memory/restore")
def restore_project_memory(project_id: str, req: _MemoryRestore):
    """Undo a delete: re-insert the entry the DELETE call returned."""
    _require_project(project_id)
    try:
        return pk.restore_memory(web_app._store, project_id, req.entry)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/api/projects/{project_id}/memory/{memory_id}")
def update_project_memory(project_id: str, memory_id: str, req: _MemoryUpdate):
    _require_project(project_id)
    fields = req.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update")
    try:
        return pk.update_memory(web_app._store, project_id, memory_id, fields)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/projects/{project_id}/memory/{memory_id}")
def delete_project_memory(project_id: str, memory_id: str):
    _require_project(project_id)
    try:
        return pk.delete_memory(web_app._store, project_id, memory_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")


@router.post("/api/sessions/{session_id}/memory/from-reply")
def save_reply_to_memory(session_id: str, req: _ReplyToMemory):
    """"Save to memory" on an assistant reply: the native summary model condenses
    it (short timeout) and the note is saved to the session's project.

    Works for every provider, since it never touches the session's own model
    context, and adds nothing to the transcript.
    """
    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if getattr(meta, "kind", "project") == "chat":
        raise HTTPException(status_code=400, detail="Project memory is not available in chats")
    _require_project(meta.project_id)
    import harness_agent as agent

    if not req.reply.strip():
        raise HTTPException(status_code=400, detail="The reply is empty")
    try:
        note = pk.draft_reply_note(req.prompt, req.reply, agent.call_api, utils.SUMMARY_MODEL)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Memory was not saved. {exc}")
    try:
        entry = pk.add_memory(
            web_app._store, meta.project_id, note, {"session_id": meta.id, "session_title": meta.title}
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    project = web_app._store.get_project(meta.project_id)
    return {"memory": entry, "project_id": meta.project_id,
            "project_name": project.name if project else meta.project_id}
