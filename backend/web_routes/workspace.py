"""Workspace routes: file tree, previews, renames, diffs, and git source
control."""
from __future__ import annotations

import os
import tempfile

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel as _PydanticBaseModel

import git_status as git_ops
import utils
from web_desktop import _is_desktop_request
from web_models import EventType
from web_ui_adapter import _file_snapshots, content_hash, read_capped

import web_app
from web_helpers import _emit_session_event

router = APIRouter()


class _DiffRequest(_PydanticBaseModel):
    session_id: str
    file_path: str


class _RevertFileRequest(_PydanticBaseModel):
    session_id: str
    file_path: str
    run_id: str = ""
    force: bool = False


class _RevertRunRequest(_PydanticBaseModel):
    session_id: str
    run_id: str
    force: bool = False


class _WorkspaceRenameRequest(_PydanticBaseModel):
    path: str
    name: str


class _WorkspaceSaveRequest(_PydanticBaseModel):
    path: str
    content: str
    base_hash: str | None = None


# Editor reads/writes stay well under the preview cap so a save round-trip
# cannot silently truncate a file the panel only partially loaded.
_MAX_EDITABLE_BYTES = 2_000_000


def _workspace_entry(full: str) -> dict:
    name = os.path.basename(full)
    is_dir = os.path.isdir(full)
    entry = {"name": name, "path": full, "is_dir": is_dir}
    if not is_dir:
        try:
            entry["size"] = os.path.getsize(full)
        except OSError:
            entry["size"] = 0
        dot = name.rfind(".")
        entry["extension"] = name[dot:] if dot > 0 else ""
    return entry


@router.get("/api/workspace/tree")
def workspace_tree(path: str, depth: int = 1):
    resolved = web_app._resolve_path(path)
    if not utils.is_path_allowed(resolved):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    if not os.path.isdir(resolved):
        raise HTTPException(status_code=404, detail="Directory not found")

    _SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "dist", ".tox", ".mypy_cache"}

    entries = []
    try:
        for name in sorted(os.listdir(resolved)):
            if name.startswith("."):
                continue
            if name in _SKIP_DIRS and os.path.isdir(os.path.join(resolved, name)):
                continue
            full = os.path.join(resolved, name)
            entries.append(_workspace_entry(full))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return {"path": resolved, "entries": entries}


@router.patch("/api/workspace/entry")
def workspace_rename_entry(req: _WorkspaceRenameRequest):
    source = web_app._resolve_path(req.path)
    if not utils.is_path_allowed(source):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    if not os.path.exists(source):
        raise HTTPException(status_code=404, detail="File or directory not found")

    new_name = req.name.strip()
    if (
        not new_name
        or new_name in {".", ".."}
        or "/" in new_name
        or "\\" in new_name
        or "\x00" in new_name
    ):
        raise HTTPException(status_code=400, detail="Invalid filename")

    parent = os.path.dirname(source)
    target = web_app._resolve_path(os.path.join(parent, new_name))
    if os.path.dirname(target) != parent or not utils.is_path_allowed(target):
        raise HTTPException(status_code=403, detail="Target path not within allowed paths")
    if os.path.exists(target):
        raise HTTPException(status_code=409, detail="A file or directory with that name already exists")

    try:
        os.rename(source, target)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not rename entry: {exc}")

    return {"entry": _workspace_entry(target)}


def _read_text_for_editor(resolved: str) -> tuple[str, str]:
    """Whole-file text for the editor, LF-normalized, plus the file's dominant
    line ending so a later save can write it back in its original convention."""
    try:
        with open(resolved, "rb") as f:
            raw = f.read()
    except OSError:
        raise HTTPException(status_code=500, detail="Could not read file")
    if b"\x00" in raw:
        raise HTTPException(status_code=415, detail="Binary files cannot be edited")
    text = raw.decode("utf-8", errors="replace")
    eol = "\r\n" if text.count("\r\n") > text.count("\n") - text.count("\r\n") else "\n"
    return text.replace("\r\n", "\n"), eol


@router.get("/api/workspace/file")
def workspace_file(path: str, offset: int = 0, lines: int = 400, full: bool = False):
    resolved = web_app._resolve_path(path)
    if not utils.is_path_allowed(resolved):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    size = os.path.getsize(resolved)
    if size > _MAX_EDITABLE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 2MB)")

    if full:
        text, eol = _read_text_for_editor(resolved)
        return {
            "path": resolved,
            "content": text,
            "size": size,
            "eol": "crlf" if eol == "\r\n" else "lf",
            "content_hash": content_hash(text),
            "total_lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
            "editable": True,
        }

    offset = max(0, offset)
    lines = min(max(1, lines), 1000)
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            selected = []
            total = 0
            for i, line in enumerate(f):
                total = i + 1
                if i < offset:
                    continue
                if len(selected) >= lines:
                    continue
                selected.append(line.rstrip("\n"))
    except (OSError, IsADirectoryError):
        raise HTTPException(status_code=500, detail="Could not read file")
    return {
        "path": resolved,
        "content": "\n".join(selected),
        "size": size,
        "offset": offset,
        "lines": len(selected),
        "total_lines": total,
        "has_more": offset + len(selected) < total,
    }


@router.put("/api/workspace/file")
def workspace_save_file(req: _WorkspaceSaveRequest, request: Request):
    # Editing is a desktop-shell feature: the Electron renderer stamps
    # X-MyHarness-Desktop on every backend request, so a plain browser tab
    # cannot reach this endpoint even if it forges the UI state.
    if not _is_desktop_request(request):
        raise HTTPException(status_code=403, detail="File editing is only available in the desktop app")

    resolved = web_app._resolve_path(req.path)
    if not utils.is_path_allowed(resolved):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    if len(req.content.encode("utf-8")) > _MAX_EDITABLE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 2MB)")

    current, eol = _read_text_for_editor(resolved)
    if req.base_hash and content_hash(current) != req.base_hash:
        raise HTTPException(
            status_code=409,
            detail="File changed on disk since it was opened; reload before saving.",
        )

    text = req.content.replace("\r\n", "\n")
    payload = text.replace("\n", eol) if eol == "\r\n" else text

    directory = os.path.dirname(resolved) or "."
    try:
        mode = os.stat(resolved).st_mode
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".myharness-edit-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(payload)
            os.chmod(tmp_path, mode & 0o7777)
            os.replace(tmp_path, resolved)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save file: {exc}")

    return {
        "path": resolved,
        "size": os.path.getsize(resolved),
        "content_hash": content_hash(text),
        "eol": "crlf" if eol == "\r\n" else "lf",
    }


def _persisted_before(session_id: str, resolved: str) -> str | None:
    """Baseline content for a file from the earliest persisted run manifest,
    used when the in-memory session snapshot was lost to a backend restart."""
    for manifest in web_app._store.load_change_manifests(session_id):
        entry = (manifest.get("files") or {}).get(resolved)
        if entry is not None:
            if not entry.get("existed_before"):
                return ""
            return entry.get("before")
    return None


@router.post("/api/workspace/diff")
def workspace_diff(req: _DiffRequest):
    import difflib

    resolved = web_app._resolve_path(req.file_path)
    if not utils.is_path_allowed(resolved):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")

    snaps = _file_snapshots.get(req.session_id, {})
    before = snaps.get(resolved) or snaps.get(req.file_path)
    if before is None:
        before = _persisted_before(req.session_id, resolved)
    if before is None:
        before = ""

    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            after = f.read(512_000)
    except (OSError, FileNotFoundError):
        after = ""

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines, after_lines,
        fromfile="before", tofile="after", lineterm=""
    )
    diff_text = "\n".join(diff)

    return {
        "file_path": resolved,
        "diff_text": diff_text,
        "before_lines": len(before_lines),
        "after_lines": len(after_lines),
    }


# ── revert from persisted run manifests ───────────────────────────


def _latest_recorded_entry(manifests: list[dict], path: str) -> dict | None:
    latest = None
    for manifest in manifests:
        entry = (manifest.get("files") or {}).get(path)
        if entry is not None:
            latest = entry
    return latest


def _apply_revert(entry: dict, manifests: list[dict], force: bool) -> dict:
    """Restore one file to its recorded before-state. Refuses when the file's
    current content does not match the last recorded write, unless forced."""
    path = entry.get("path") or ""
    current = read_capped(path) if os.path.isfile(path) else None

    if not force:
        latest = _latest_recorded_entry(manifests, path)
        if latest is not None:
            if latest.get("action") == "deleted":
                if current is not None:
                    return {
                        "ok": False,
                        "code": 409,
                        "error": "File was recreated after the recorded delete; use force to revert anyway.",
                    }
            elif current is None or content_hash(current) != latest.get("after_hash"):
                return {
                    "ok": False,
                    "code": 409,
                    "error": "File changed outside recorded agent runs; use force to revert anyway.",
                }

    if not entry.get("existed_before"):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as exc:
            return {"ok": False, "code": 500, "error": f"Could not delete file: {exc}"}
        return {"ok": True, "action": "deleted"}

    before = entry.get("before")
    if before is None:
        return {
            "ok": False,
            "code": 400,
            "error": "No before-content was recorded for this file (unreadable or too large at snapshot time).",
        }
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(before)
    except OSError as exc:
        return {"ok": False, "code": 500, "error": f"Could not restore file: {exc}"}
    return {"ok": True, "action": "restored"}


def _revert_guard(session_id: str) -> None:
    if web_app._store.load_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if web_app._manager.get_active_run(session_id):
        raise HTTPException(status_code=409, detail="Cannot revert while a run is active in this session")


@router.post("/api/workspace/revert")
def workspace_revert_file(req: _RevertFileRequest):
    _revert_guard(req.session_id)
    resolved = web_app._resolve_path(req.file_path)
    if not utils.is_path_allowed(resolved):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")

    manifests = web_app._store.load_change_manifests(req.session_id)
    if req.run_id:
        manifest = web_app._store.load_change_manifest(req.session_id, req.run_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail="Run manifest not found")
        entry = (manifest.get("files") or {}).get(resolved)
    else:
        entry = None
        for manifest in manifests:
            candidate = (manifest.get("files") or {}).get(resolved)
            if candidate is not None:
                entry = candidate
                break
    if entry is None:
        raise HTTPException(status_code=404, detail="No recorded change for this file")

    result = _apply_revert(entry, manifests, req.force)
    if not result["ok"]:
        raise HTTPException(status_code=result["code"], detail=result["error"])
    _emit_session_event(
        req.session_id,
        EventType.FILE_CHANGE,
        {"path": resolved, "action": "reverted", "tool": "revert"},
    )
    return {"status": "reverted", "file_path": resolved, "action": result["action"]}


@router.post("/api/workspace/revert_run")
def workspace_revert_run(req: _RevertRunRequest):
    _revert_guard(req.session_id)
    manifest = web_app._store.load_change_manifest(req.session_id, req.run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Run manifest not found")
    manifests = web_app._store.load_change_manifests(req.session_id)

    results = []
    for path, entry in (manifest.get("files") or {}).items():
        if not utils.is_path_allowed(web_app._resolve_path(path)):
            results.append({"file_path": path, "ok": False, "error": "Path not within allowed paths"})
            continue
        result = _apply_revert(entry, manifests, req.force)
        if result["ok"]:
            _emit_session_event(
                req.session_id,
                EventType.FILE_CHANGE,
                {"path": path, "action": "reverted", "tool": "revert"},
            )
            results.append({"file_path": path, "ok": True, "action": result["action"]})
        else:
            results.append({"file_path": path, "ok": False, "error": result["error"]})

    failed = [r for r in results if not r["ok"]]
    return {
        "status": "partial" if failed else "reverted",
        "run_id": req.run_id,
        "results": results,
    }


# ── workspace git (source control) ────────────────────────────────


class _GitStageRequest(_PydanticBaseModel):
    path: str
    files: list[str] = []
    unstage: bool = False


class _GitCommitRequest(_PydanticBaseModel):
    path: str
    message: str


def _allowed_repo_root(path: str) -> str:
    resolved = web_app._resolve_path(path)
    if not utils.is_path_allowed(resolved):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    if not os.path.isdir(resolved):
        raise HTTPException(status_code=404, detail="Directory not found")
    return resolved


def _allowed_repo_file(root: str, file_path: str) -> str:
    """Resolve a per-file path (absolute or relative to the repo root) within the allowlist."""
    candidate = file_path
    if not os.path.isabs(candidate):
        candidate = os.path.join(root, candidate)
    resolved = web_app._resolve_path(candidate)
    if not utils.is_path_allowed(resolved):
        raise HTTPException(status_code=403, detail="Path not within allowed paths")
    return resolved


def _require_git_writes() -> None:
    if not utils.GIT_WRITES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Git write operations are disabled. Enable ui.git_writes_enabled to commit from MyHarness.",
        )


@router.get("/api/workspace/git/status")
def workspace_git_status(path: str):
    root = _allowed_repo_root(path)
    return git_ops.status(root)


@router.get("/api/workspace/git/diff")
def workspace_git_diff(path: str, file: str = "", staged: bool = False):
    root = _allowed_repo_root(path)
    if not git_ops.is_git_repo(root):
        raise HTTPException(status_code=400, detail="Not a git repository")
    rel = file
    if file:
        resolved_file = _allowed_repo_file(root, file)
        rel = os.path.relpath(resolved_file, root)
    return git_ops.file_diff(root, rel, staged=staged)


@router.post("/api/workspace/git/stage")
def workspace_git_stage(req: _GitStageRequest):
    _require_git_writes()
    root = _allowed_repo_root(req.path)
    if not git_ops.is_git_repo(root):
        raise HTTPException(status_code=400, detail="Not a git repository")
    rels = []
    for file in req.files:
        resolved_file = _allowed_repo_file(root, file)
        rels.append(os.path.relpath(resolved_file, root))
    result = git_ops.stage(root, rels, unstage=req.unstage)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "git stage failed")
    return git_ops.status(root)


@router.post("/api/workspace/git/commit")
def workspace_git_commit(req: _GitCommitRequest):
    _require_git_writes()
    root = _allowed_repo_root(req.path)
    if not git_ops.is_git_repo(root):
        raise HTTPException(status_code=400, detail="Not a git repository")
    result = git_ops.commit(root, req.message)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "git commit failed")
    return {**result, "status": git_ops.status(root)}
