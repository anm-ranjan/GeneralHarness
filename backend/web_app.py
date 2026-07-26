from __future__ import annotations

import asyncio
import mimetypes
import os
import sys
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

# Add the backend directory and the bundled agent source to the Python path
_BACKEND_DIR = str(Path(__file__).resolve().parent)
_AGENT_DIR = str(Path(__file__).resolve().parent / "agent")
for _dir in (_BACKEND_DIR, _AGENT_DIR):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

# This module is imported as "backend.web_app" (tests), "web_app" (flat), or
# run as a script ("__main__"). Helper and route modules access mutable server
# state through "import web_app", so alias this module object under that name
# to guarantee a single shared instance regardless of how it was first loaded.
sys.modules.setdefault("web_app", sys.modules[__name__])

import threading

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import shutil

from session_store import SessionStore
from web_session import SessionManager

import utils
import audio_transcription
from web_desktop import (
    DesktopAwareStaticFiles,
    _desktop_backend_host,
    _forced_content_type,
    _is_desktop_request,
    _reject_browser_ui_if_electron_only,
)


def _resolve_path(p: str) -> str:
    """Resolve a path to an absolute canonical form.

    Uses abspath only — realpath mangles UNC paths on Windows by resolving
    mapped drives or changing prefix forms, causing path comparisons to fail.
    """
    return os.path.normpath(os.path.abspath(p))


# ── globals ────────────────────────────────────────────────────────

_DATA_DIR = os.environ.get(
    "MYHARNESS_WEB_DATA_DIR",
    str(Path(__file__).resolve().parent.parent / "data"),
)

_STATIC_DIR = os.environ.get(
    "MYHARNESS_WEB_STATIC_DIR",
    str(Path(__file__).resolve().parent.parent / "frontend" / "dist"),
)

_SCRIPTS_DIR = Path(_DATA_DIR) / "scripts"
_CHATS_DIR = Path(_DATA_DIR) / "chats"
_ATTACHMENTS_DIR = Path(_DATA_DIR) / "attachments"

_store: SessionStore | None = None
_manager = SessionManager()


class _SessionMessageCache(OrderedDict):
    """LRU cache of in-memory agent history, keyed by session.

    Entries are rebuildable from the persisted event log via
    ``_restore_session_messages``, so evicting a cold session costs one replay
    on next use rather than losing anything. Sessions with an active run are
    never evicted.
    """

    max_entries = 24

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def get(self, key, default=None):
        return self[key] if key in self else default

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        self._evict()

    def _evict(self) -> None:
        if len(self) <= self.max_entries:
            return
        newest = next(reversed(self))
        for session_id in list(self):
            if len(self) <= self.max_entries:
                break
            if session_id == newest:
                continue
            if _manager.get_active_run(session_id) is not None:
                continue
            del self[session_id]


_session_messages: _SessionMessageCache = _SessionMessageCache()
_uvicorn_server: "uvicorn.Server | None" = None

# Per-session run locks: enqueue/dequeue/start/finish races are session-scoped,
# so sessions no longer serialize against each other on a single global lock.
_run_locks: dict[str, threading.RLock] = {}
_run_locks_guard = threading.Lock()


def _run_lock_for(session_id: str) -> threading.RLock:
    with _run_locks_guard:
        lock = _run_locks.get(session_id)
        if lock is None:
            lock = threading.RLock()
            _run_locks[session_id] = lock
        return lock


def _discard_run_lock(session_id: str) -> None:
    """Drop a deleted session's lock. Callers ensure no run is active."""
    with _run_locks_guard:
        _run_locks.pop(session_id, None)


def _scripts_dir_for_session(session_id: str) -> str:
    """Per-session managed directory for the helper scripts the agent authors.

    Lives under the web data dir (outside any workspace) and is registered as an
    allowed path so file_write/shell_run may use it. Cleaned up with the session.
    """
    path = _SCRIPTS_DIR / session_id
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _chat_workspace_dir(chat_id: str) -> str:
    """Per-chat sandboxed workspace for the general-purpose agent.

    Lives under the web data dir (outside any user path) and is registered as an
    allowed path so the agent may read/write/run inside it. Cleaned up with the chat.
    """
    path = _CHATS_DIR / chat_id
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


# Windows can inherit a registry MIME mapping that reports built Vite bundles
# as text/plain. Chromium refuses to execute module scripts with that MIME.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")


def _codex_app_server_available() -> bool:
    return utils.CODEX_APP_SERVER_ENABLED and shutil.which(utils.CODEX_APP_SERVER_BINARY) is not None


def _claude_agent_available() -> bool:
    return utils.CLAUDE_AGENT_ENABLED and shutil.which(utils.CLAUDE_AGENT_BINARY) is not None


# ── app ────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _store
    _store = SessionStore(_DATA_DIR)
    _store.reset_running_sessions()
    _SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    utils.register_allowed_path(str(_SCRIPTS_DIR))
    _CHATS_DIR.mkdir(parents=True, exist_ok=True)
    utils.register_allowed_path(str(_CHATS_DIR))
    _ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    utils.register_allowed_path(str(_ATTACHMENTS_DIR))
    _manager.set_event_loop(asyncio.get_event_loop())
    yield


app = FastAPI(title=utils.APP_NAME, version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── routers ────────────────────────────────────────────────────────

import web_routes.system
import web_routes.sessions
import web_routes.runs
import web_routes.workspace

app.include_router(web_routes.system.router)
app.include_router(web_routes.sessions.router)
app.include_router(web_routes.runs.router)
app.include_router(web_routes.workspace.router)


# ── static frontend ───────────────────────────────────────────────

if os.path.isdir(_STATIC_DIR):
    @app.get("/")
    def index(request: Request):
        _reject_browser_ui_if_electron_only(request)
        for name in ("index.html", "prototype.html"):
            path = os.path.join(_STATIC_DIR, name)
            if os.path.isfile(path):
                return FileResponse(path)
        raise HTTPException(status_code=404, detail="No frontend found")

    _assets_dir = os.path.join(_STATIC_DIR, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", DesktopAwareStaticFiles(directory=_assets_dir), name="assets")
    app.mount("/static", DesktopAwareStaticFiles(directory=_STATIC_DIR), name="static")


# ── compatibility re-exports ──────────────────────────────────────
# Helper and route callables kept addressable as web_app.<name> for tests
# and any external callers that predate the router split.

from web_models import (  # noqa: F401
    AudioTranscriptionRequest,
    ApprovalResponse,
    BrowseDirectoryRequest,
    CreateChatRequest,
    CreateProjectRequest,
    CreateSessionRequest,
    CreateTaskRequest,
    EventEnvelope,
    EventType,
    MoveSessionRequest,
    QueuedMessage,
    RenameProjectRequest,
    RenameSessionRequest,
    RenameTaskRequest,
    SendMessageRequest,
)
from web_helpers import (  # noqa: F401
    _codex_external_path_refs,
    _codex_prompt_with_attachments,
    _copy_attachments_to_codex_workspace,
    _copy_images_to_codex_workspace,
    _decode_image_payload,
    _emit_queue_updated,
    _emit_session_event,
    _enqueue_message_locked,
    _native_user_content,
    _path_is_inside,
    _project_root_for_session,
    _queue_payload,
    _restore_session_messages,
    _resolve_chdir_target,
    _save_message_attachments,
    _save_message_images,
    _task_name_for_session,
    _workspace_root_for_session,
)
from web_runs import (  # noqa: F401
    _emit_run_metrics,
    _finish_run_and_start_next,
    _handle_chdir_command,
    _handle_slash_command,
    _message_block_detail,
    _start_agent_run_locked,
)
from web_routes.system import (  # noqa: F401
    browse_directory,
    codex_status,
    get_local_image,
    get_project,
    health,
    shutdown_server,
    transcribe_audio,
)
from web_routes.sessions import (  # noqa: F401
    _ImportSessionRequest,
    backup_session,
    create_chat,
    import_session,
    create_project,
    create_session,
    create_task,
    delete_project,
    delete_session,
    delete_task,
    export_session,
    get_session,
    get_session_attachment,
    get_session_events,
    get_session_metrics,
    list_sessions,
    move_session,
    rename_project,
    rename_session,
    rename_task,
    search_sessions,
)
from web_routes.runs import (  # noqa: F401
    _QueueReorderRequest,
    cancel_run,
    remove_queued_message,
    reorder_queued_messages,
    resolve_approval,
    send_message,
    session_events,
)
from web_routes.workspace import (  # noqa: F401
    _DiffRequest,
    _GitCommitRequest,
    _GitStageRequest,
    _RevertFileRequest,
    _RevertRunRequest,
    _WorkspaceRenameRequest,
    _WorkspaceSaveRequest,
    workspace_diff,
    workspace_file,
    workspace_git_commit,
    workspace_git_diff,
    workspace_git_stage,
    workspace_git_status,
    workspace_rename_entry,
    workspace_revert_file,
    workspace_revert_run,
    workspace_save_file,
    workspace_tree,
)


# ── entry point ───────────────────────────────────────────────────

def main():
    global _uvicorn_server
    # Precedence: environment variable > server.* in agent_config.yaml > default.
    host = os.environ.get("MYHARNESS_WEB_HOST", "").strip() or utils.SERVER_HOST or "127.0.0.1"
    try:
        port = int(os.environ.get("MYHARNESS_WEB_PORT", "").strip() or utils.SERVER_PORT or 8420)
    except ValueError:
        port = 8420
    print(f"Starting {utils.APP_NAME} on http://{host}:{port}")
    utils.print_startup_warnings(host, port)
    print(f"Agent source: {_AGENT_DIR}")
    print(f"Data dir: {_DATA_DIR}")
    print(f"Static dir: {_STATIC_DIR}")
    config = uvicorn.Config(app, host=host, port=port)
    _uvicorn_server = uvicorn.Server(config)
    _uvicorn_server.run()


if __name__ == "__main__":
    main()
