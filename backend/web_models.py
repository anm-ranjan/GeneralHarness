from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    SESSION_LOADED = "session_loaded"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    ASSISTANT_DELTA = "assistant_delta"
    STATUS = "status"
    ERROR = "error"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RESOLVED = "approval_resolved"
    QUESTION_REQUIRED = "question_required"
    QUESTION_RESOLVED = "question_resolved"
    API_METRICS = "api_metrics"
    CONTEXT_USAGE = "context_usage"
    ITERATION = "iteration"
    COMPACTION = "compaction"
    RUN_FINISHED = "run_finished"
    RUN_METRICS = "run_metrics"
    CODEX_COMMAND = "codex_command"
    CODEX_FILE_CHANGE = "codex_file_change"
    CODEX_ITEM = "codex_item"
    PROVIDER_WARNING = "provider_warning"
    PROVIDER_SWITCH = "provider_switch"
    FILE_CHANGE = "file_change"
    GENERATED_ARTIFACT = "generated_artifact"
    THINKING = "thinking"
    THINKING_DELTA = "thinking_delta"
    QUEUE_UPDATED = "queue_updated"
    WORKSPACE_CHANGED = "workspace_changed"
    PLAN_UPDATE = "plan_update"


def _evt_id() -> str:
    return f"evt_{uuid.uuid4().hex[:8]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EventEnvelope(BaseModel):
    id: str = Field(default_factory=_evt_id)
    session_id: str
    type: EventType
    created_at: datetime = Field(default_factory=_now)
    data: dict


class SessionMeta(BaseModel):
    id: str
    project_id: str
    task_id: str
    title: str
    summary: str = ""
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    status: str = "idle"
    provider: str = "native"
    kind: str = "project"
    working_directory: str = ""
    codex_state: dict = Field(default_factory=dict)
    claude_state: dict = Field(default_factory=dict)
    message_queue: list["QueuedMessage"] = Field(default_factory=list)
    # Per-session overrides for run behavior. Absent keys inherit the process
    # defaults from agent config. Known keys: approval_mode, verbose_tools,
    # max_iterations, model, reasoning_effort.
    run_settings: dict = Field(default_factory=dict)


class QueuedMessage(BaseModel):
    id: str = Field(default_factory=lambda: f"qmsg_{uuid.uuid4().hex[:8]}")
    text: str = ""
    images: list[dict] = Field(default_factory=list)
    attachments: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class TaskInfo(BaseModel):
    id: str
    name: str
    color: str = ""
    sessions: list[str] = Field(default_factory=list)


class ProjectInfo(BaseModel):
    id: str
    name: str
    root: str
    tasks: list[TaskInfo] = Field(default_factory=list)


class CreateProjectRequest(BaseModel):
    name: str
    root: str = ""


class CreateTaskRequest(BaseModel):
    project_id: str
    name: str


class CreateSessionRequest(BaseModel):
    project_id: str
    task_id: str = ""
    title: str = ""
    provider: str = "native"


class CreateChatRequest(BaseModel):
    title: str = ""
    provider: str = "native"


class ImageAttachment(BaseModel):
    data: str
    mime: str
    name: str = "image"


class FileAttachment(BaseModel):
    data: str
    mime: str = "application/octet-stream"
    name: str = "attachment"


class SendMessageRequest(BaseModel):
    text: str = ""
    images: list[ImageAttachment] = Field(default_factory=list)
    attachments: list[FileAttachment] = Field(default_factory=list)


class AudioTranscriptionRequest(BaseModel):
    session_id: str
    data: str
    mime: str = "audio/webm"
    name: str = "recording.webm"


class CredentialUpdateRequest(BaseModel):
    native_api_key: str | None = None
    stt_api_key: str | None = None
    remove_native_api_key: bool = False
    remove_stt_api_key: bool = False


class RenameProjectRequest(BaseModel):
    name: str


class RenameTaskRequest(BaseModel):
    name: str | None = None
    color: str | None = None


class RenameSessionRequest(BaseModel):
    title: str


class UpdateRunSettingsRequest(BaseModel):
    model: str | None = None
    reasoning_effort: str | None = None


class MoveSessionRequest(BaseModel):
    project_id: str
    task_id: str


class DeleteSessionRequest(BaseModel):
    project_id: str
    task_id: str


class BrowseDirectoryRequest(BaseModel):
    path: str = ""


class ApprovalResponse(BaseModel):
    approval_id: str
    approved: bool


class QuestionResponse(BaseModel):
    question_id: str
    answer: str
