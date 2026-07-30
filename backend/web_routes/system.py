"""System routes: health, shutdown, codex status, project info, directory
browsing, local image serving, and audio transcription."""
from __future__ import annotations

import mimetypes
import os
import shutil
import signal
import sys
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import audio_transcription
import utils
from web_models import AudioTranscriptionRequest, BrowseDirectoryRequest

import web_app

router = APIRouter()


@router.get("/api/health")
def health():
    audio_config = audio_transcription.config_from_utils(utils)
    return {
        "status": "ok",
        "model": utils.MODEL,
        "read_model": utils.READ_MODEL,
        "write_model": utils.WRITE_MODEL,
        "approval_mode": utils.APPROVAL_MODE,
        "verbose": utils.UI_VERBOSE_TOOLS,
        "app_name": utils.APP_NAME,
        "splash_ascii": utils.SPLASH_ASCII,
        "native_enabled": web_app._native_available(),
        "default_provider": utils.DEFAULT_PROVIDER,
        "codex_enabled": web_app._codex_app_server_available(),
        "codex_app_server_enabled": web_app._codex_app_server_available(),
        "claude_agent_enabled": web_app._claude_agent_available(),
        "desktop_enabled": utils.DESKTOP_ENABLED,
        "desktop_backend_url": utils.DESKTOP_BACKEND_URL,
        "desktop_prefer_existing_backend": utils.DESKTOP_PREFER_EXISTING_BACKEND,
        "desktop_start_local_backend_fallback": utils.DESKTOP_START_LOCAL_BACKEND_FALLBACK,
        "electron_only": utils.DESKTOP_ELECTRON_ONLY,
        "git_writes_enabled": utils.GIT_WRITES_ENABLED,
        "audio": audio_transcription.public_config(audio_config),
    }


@router.get("/api/fleet")
def fleet():
    """The configured fleet of machines this UI can switch between.

    Read once from the host serving the page, so the list stays stable while
    the user switches around it. An empty `hosts` means single-machine mode.
    """
    return {"self": utils.FLEET_SELF_ID, "poll_seconds": utils.FLEET_POLL_SECONDS,
            "hosts": utils.fleet_registry()}


@router.get("/api/fleet/status")
def fleet_status():
    """Cheap liveness and activity summary, polled directly by the browser for
    every configured host so background runs on the machine you are not
    looking at stay visible — especially ones blocked on an approval."""
    waiting = set(web_app._manager.pending_approval_session_ids())
    running = 0
    for meta in web_app._store.list_sessions():
        if meta.id in waiting:
            continue
        if meta.status == "running":
            running += 1
    return {
        "host_id": utils.FLEET_SELF_ID,
        "app_name": utils.APP_NAME,
        "running": running,
        "waiting_approval": len(waiting),
    }


@router.post("/api/shutdown")
def shutdown_server():
    utils.kill_all_background_jobs()

    def stop():
        parent_pid = int(os.environ.get("MYHARNESS_RUN_PARENT_PID") or "0")

        if parent_pid:
            try:
                if sys.platform == "win32":
                    os.kill(parent_pid, signal.CTRL_BREAK_EVENT)
                else:
                    os.kill(parent_pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass

        if web_app._uvicorn_server is not None:
            web_app._uvicorn_server.should_exit = True
        else:
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass

    threading.Timer(0.2, stop).start()
    return {"status": "shutting_down"}


@router.get("/api/codex/status")
def codex_status():
    binary = utils.CODEX_APP_SERVER_BINARY
    available = shutil.which(binary) is not None
    return {
        "enabled": utils.CODEX_APP_SERVER_ENABLED,
        "binary": binary,
        "available": available,
        "listen": utils.CODEX_APP_SERVER_LISTEN,
        "sandbox": utils.CODEX_APP_SERVER_SANDBOX,
        "approval_policy": utils.CODEX_APP_SERVER_APPROVAL_POLICY,
    }


@router.get("/api/claude/status")
def claude_status():
    binary = utils.CLAUDE_AGENT_BINARY
    available = shutil.which(binary) is not None
    return {
        "enabled": utils.CLAUDE_AGENT_ENABLED,
        "binary": binary,
        "available": available,
        "model": utils.CLAUDE_AGENT_MODEL,
        "permission_mode": utils.CLAUDE_AGENT_PERMISSION_MODE,
    }


@router.get("/api/project")
def get_project():
    return {
        "name": os.path.basename(utils.ALLOWED_PATHS[0]) if utils.ALLOWED_PATHS else "Unknown",
        "root": utils.ALLOWED_PATHS[0] if utils.ALLOWED_PATHS else "",
        "allowed_paths": utils.ALLOWED_PATHS,
    }


@router.post("/api/browse")
def browse_directory(req: BrowseDirectoryRequest):
    if not req.path and len(utils.ALLOWED_PATHS) > 1:
        roots = []
        for p in utils.ALLOWED_PATHS:
            rp = web_app._resolve_path(p)
            if os.path.isdir(rp):
                roots.append({"name": os.path.basename(rp) or rp, "path": rp})
        return {"current": "", "parent": None, "entries": roots, "is_root_list": True}

    base = req.path or (utils.ALLOWED_PATHS[0] if utils.ALLOWED_PATHS else os.getcwd())
    base = web_app._resolve_path(base)
    if not utils.is_path_allowed(base):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    if not os.path.isdir(base):
        raise HTTPException(status_code=404, detail="Not a directory")
    entries = []
    try:
        for name in sorted(os.listdir(base)):
            full = os.path.join(base, name)
            if os.path.isdir(full) and not name.startswith('.'):
                entries.append({"name": name, "path": full})
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    is_allowed_root = base in [web_app._resolve_path(p) for p in utils.ALLOWED_PATHS]
    parent_path = os.path.dirname(base)
    if is_allowed_root and len(utils.ALLOWED_PATHS) > 1:
        parent = ""
    elif utils.is_path_allowed(parent_path):
        parent = parent_path
    else:
        parent = None

    return {
        "current": base,
        "parent": parent,
        "entries": entries,
    }


@router.get("/api/files/image")
def get_local_image(path: str, v: str | None = None):
    resolved = web_app._resolve_path(path)
    if not utils.is_path_allowed(resolved):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="Image not found")

    media_type = mimetypes.guess_type(resolved)[0]
    if not media_type or not media_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="File is not an image")
    cache_control = (
        "private, max-age=31536000, immutable"
        if v
        else "private, max-age=0, must-revalidate"
    )
    return FileResponse(
        resolved,
        media_type=media_type,
        headers={"Cache-Control": cache_control},
    )


@router.post("/api/audio/transcribe")
def transcribe_audio(req: AudioTranscriptionRequest):
    meta = web_app._store.load_session(req.session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    config = audio_transcription.config_from_utils(utils)
    return audio_transcription.transcribe_audio(
        data_dir=web_app._DATA_DIR,
        session_id=meta.id,
        audio_data=req.data,
        mime=req.mime,
        name=req.name,
        config=config,
    )
