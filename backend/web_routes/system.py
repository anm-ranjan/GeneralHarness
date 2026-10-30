"""System routes: health, shutdown, codex status, project info, directory
browsing, local image serving, and audio transcription."""
from __future__ import annotations

import mimetypes
import hmac
import os
import shutil
import signal
import sys
import threading
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

import audio_transcription
import fleet_bridge
import web_helpers
import credential_store
import performance_log
import utils
from web_desktop import _is_desktop_request
from web_models import AudioTranscriptionRequest, BrowseDirectoryRequest, CreateDirectoryRequest, CredentialUpdateRequest

import web_app

router = APIRouter()


class _ClientTiming(BaseModel):
    phase: Literal["sidebar_loaded", "session_replay_rendered"]
    elapsed_ms: float = Field(ge=0, le=600_000, allow_inf_nan=False)
    count: int = Field(ge=0, le=10_000_000)


@router.post("/api/diagnostics/performance")
def client_performance(req: _ClientTiming):
    """Content-free frontend timings (counts and durations only)."""
    performance_log.record(req.phase, time.perf_counter() - req.elapsed_ms / 1000, count=req.count, source="frontend")
    return {"status": "recorded"}


def _require_desktop(request: Request) -> None:
    if not _is_desktop_request(request):
        raise HTTPException(status_code=403, detail="Credential settings are available only in the Electron app")
    expected = os.environ.get("MYHARNESS_DESKTOP_CREDENTIAL_TOKEN", "").strip()
    supplied = request.headers.get("x-myharness-desktop-credential", "").strip()
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="Credential settings require the local Electron backend")


def _credential_field_status(name: str, stored: dict[str, str], yaml_value: str) -> dict:
    environment_value = os.environ.get(name, "").strip()
    if environment_value:
        source = "environment"
    elif stored.get(name):
        source = "credential"
    elif yaml_value:
        source = "yaml"
    else:
        source = "missing"
    return {
        "configured": source != "missing",
        "stored": bool(stored.get(name)),
        "source": source,
        "environment_override": bool(environment_value),
    }


def _credential_settings_payload() -> dict:
    try:
        stored = credential_store.load_credentials()
    except credential_store.CredentialStoreError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read encrypted credentials: {exc}") from exc
    return {
        "host_id": utils.FLEET_SELF_ID,
        "native_api_key": _credential_field_status(
            credential_store.NATIVE_API_KEY, stored, utils.CONFIG_API_KEY,
        ),
        "stt_api_key": _credential_field_status(
            credential_store.STT_API_KEY, stored, utils.CONFIG_AUDIO_TRANSCRIPTION_API_KEY,
        ),
    }


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


@router.get("/api/desktop/credentials")
def get_credentials(request: Request):
    _require_desktop(request)
    return _credential_settings_payload()


@router.put("/api/desktop/credentials")
def update_credentials(request: Request, req: CredentialUpdateRequest):
    _require_desktop(request)
    replacements = {}
    if req.native_api_key is not None and req.native_api_key.strip():
        replacements[credential_store.NATIVE_API_KEY] = req.native_api_key
    if req.stt_api_key is not None and req.stt_api_key.strip():
        replacements[credential_store.STT_API_KEY] = req.stt_api_key
    removals = []
    if req.remove_native_api_key:
        removals.append(credential_store.NATIVE_API_KEY)
    if req.remove_stt_api_key:
        removals.append(credential_store.STT_API_KEY)
    try:
        credential_store.update_credentials(replacements, removals)
    except credential_store.CredentialStoreError as exc:
        raise HTTPException(status_code=500, detail=f"Could not update encrypted credentials: {exc}") from exc
    return _credential_settings_payload()


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


def _bridge_host(host_id: str) -> dict:
    try:
        return fleet_bridge.find_host(host_id)
    except fleet_bridge.BridgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/fleet/bridge")
def fleet_bridge_status():
    """State of every peer this machine can bring online or offline.

    Always answered by the machine serving the page: it owns the SSH forwards,
    whichever host the UI is currently viewing."""
    return {"hosts": fleet_bridge.probe_all()}


@router.post("/api/fleet/bridge/{host_id}/start")
def fleet_bridge_start(host_id: str):
    host = _bridge_host(host_id)
    try:
        return fleet_bridge.start(host)
    except fleet_bridge.BridgeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class _BridgeStopRequest(BaseModel):
    force: bool = False


@router.post("/api/fleet/bridge/{host_id}/stop")
def fleet_bridge_stop(host_id: str, req: _BridgeStopRequest | None = None):
    host = _bridge_host(host_id)
    try:
        return fleet_bridge.stop(host, force=bool(req and req.force))
    except fleet_bridge.BridgeBusy as busy:
        raise HTTPException(
            status_code=409,
            detail={"message": str(busy), "running": busy.running, "waiting_approval": busy.waiting_approval},
        ) from busy
    except fleet_bridge.BridgeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class _FleetImportRequest(BaseModel):
    host: str
    source_path: str
    dest_name: str
    include_everything: bool = False


@router.post("/api/sessions/{session_id}/fleet-import")
def fleet_import(session_id: str, req: _FleetImportRequest):
    """Pull a folder from another fleet host into this session's workspace.

    Called by an agent (Codex, Claude or native) through the skill, so it is
    deliberately narrow: pull only, into a new folder inside the workspace,
    from a path the other host's own backend allows, and always behind an
    approval prompt in the calling session, whatever its approval mode."""
    from web_ui_adapter import WebUI

    meta = web_app._store.load_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    run = web_app._manager.get_active_run(session_id)
    if run is None:
        raise HTTPException(status_code=409, detail="Importing needs a running turn in the session, to ask for approval.")
    host = _bridge_host(req.host)
    workspace = web_helpers._workspace_root_for_session(meta)
    try:
        plan = fleet_bridge.plan_import(host, req.source_path, workspace, req.dest_name, req.include_everything)
    except fleet_bridge.BridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    summary = (
        f"Copy {plan['source']} from {host['label']} into {plan['target']}: "
        f"{plan['file_count']} files, {plan['size']} "
        f"({'everything' if req.include_everything else 'source files only'})."
    )
    ui = WebUI(session_id, web_app._manager, run, web_app._store)
    if not ui.request_approval("fleet_import", summary, None):
        raise HTTPException(status_code=403, detail="The import was declined.")
    try:
        return fleet_bridge.run_import(plan)
    except fleet_bridge.BridgeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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


@router.post("/api/browse/mkdir")
def create_directory(req: CreateDirectoryRequest):
    """Create one new folder inside an existing allowed folder (the picker's
    "New folder"). A single path component only: no nesting, no traversal."""
    name = req.name.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise HTTPException(status_code=400, detail="Enter a folder name without slashes")
    parent = web_app._resolve_path(req.parent)
    if not utils.is_path_allowed(parent):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    if not os.path.isdir(parent):
        raise HTTPException(status_code=404, detail="Not a directory")
    target = os.path.join(parent, name)
    if not utils.is_path_allowed(target):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    try:
        os.mkdir(target)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"A folder named '{name}' already exists")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not create the folder: {exc.strerror or exc}")
    return {"path": target, "name": name}


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
    materializations_dir = web_app._store._materializations_dir
    result = audio_transcription.transcribe_audio(
        data_dir=str(materializations_dir),
        session_id=meta.id,
        audio_data=req.data,
        mime=req.mime,
        name=req.name,
        config=config,
    )
    raw, resolved_mime = audio_transcription.decode_audio_payload(
        req.data, req.mime, config.max_upload_mb
    )
    extension = audio_transcription._AUDIO_MIME_EXT.get(resolved_mime, ".bin")
    filename = f"{result['voice_turn_id']}_input{extension}"
    web_app._store.store_attachment(
        meta.id,
        filename,
        raw,
        mime_type=resolved_mime,
        role="audio",
        display_name=req.name,
    )
    transient_dir = materializations_dir / "audio" / meta.id / result["voice_turn_id"]
    try:
        if transient_dir.exists():
            shutil.rmtree(transient_dir)
    except OSError:
        pass
    return result
