"""Portable session backup/import.

Bundles a session's metadata, event stream, attachments, run change
manifests, and codex summary into a single ZIP archive that can be restored
on another machine (or after a data-directory loss). Machine-specific state
(workspace override, provider bindings, queued messages) is reset on import.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime, timezone

from session_store import SessionStore
from web_models import EventEnvelope, SessionMeta

BACKUP_FORMAT = "myharness-session-backup"
BACKUP_VERSION = 1


def export_session_backup(
    store: SessionStore,
    meta: SessionMeta,
    project_name: str = "",
    task_name: str = "",
) -> bytes:
    session_id = meta.id
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "backup.json",
            json.dumps(
                {
                    "format": BACKUP_FORMAT,
                    "version": BACKUP_VERSION,
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "session": meta.model_dump(mode="json"),
                    "project_name": project_name,
                    "task_name": task_name,
                },
                indent=2,
                default=str,
            ),
        )

        events_jsonl = store.export_events_jsonl(session_id)
        if events_jsonl:
            zf.writestr("events.jsonl", events_jsonl)

        for attachment in store.list_attachments(session_id):
            stored = store.read_attachment(session_id, attachment["filename"])
            if stored is not None:
                zf.writestr(f"attachments/{attachment['filename']}", stored[0])

        for manifest in store.load_change_manifests(session_id):
            run_id = manifest.get("run_id") or "run_unknown"
            zf.writestr(
                f"changes/{run_id}.json",
                json.dumps(manifest, indent=2, default=str),
            )

        codex_summary = store.load_codex_summary(session_id)
        if codex_summary is not None:
            zf.writestr(
                "codex/summary.json",
                json.dumps(codex_summary, indent=2, default=str),
            )
    return buffer.getvalue()


def _rewrite_attachment_paths(data: dict, store: SessionStore, session_id: str) -> None:
    for key in ("attachments", "images"):
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("filename"):
                try:
                    item["path"] = str(store.materialize_attachment(session_id, item["filename"]))
                except FileNotFoundError:
                    item.pop("path", None)


def import_session_backup(
    store: SessionStore,
    data: bytes,
    project_id: str = "",
    task_id: str = "",
) -> SessionMeta:
    """Restore a backup as a NEW session. Returns the created SessionMeta.

    Raises ValueError for invalid archives or unresolvable import targets.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        info = json.loads(zf.read("backup.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Not a valid MyHarness session backup: {exc}")

    if info.get("format") != BACKUP_FORMAT:
        raise ValueError("Not a valid MyHarness session backup (unknown format)")
    if info.get("version") != BACKUP_VERSION:
        raise ValueError(f"Unsupported backup version: {info.get('version')}")

    old_meta = info.get("session") or {}
    old_id = old_meta.get("id")
    if not old_id:
        raise ValueError("Backup is missing the original session id")

    project_id = project_id or old_meta.get("project_id") or ""
    task_id = task_id or old_meta.get("task_id") or ""
    project = store.get_project(project_id)
    if project is None or not any(t.id == task_id for t in project.tasks):
        raise ValueError(
            "Import target not found. Choose an existing project and task for the imported session."
        )

    title = old_meta.get("title") or "Imported session"
    new_meta = store.create_session(
        project_id,
        task_id,
        title=f"{title} (imported)",
        provider=old_meta.get("provider", "native"),
        kind=old_meta.get("kind", "project"),
    )
    new_id = new_meta.id

    # Portable fields carry over; machine/provider-specific state is reset.
    new_meta.summary = old_meta.get("summary", "")
    new_meta.message_count = int(old_meta.get("message_count") or 0)
    run_settings = old_meta.get("run_settings")
    if isinstance(run_settings, dict):
        new_meta.run_settings = run_settings
    store.update_session(new_meta)

    names = set(zf.namelist())

    for name in sorted(names):
        if not name.startswith("attachments/"):
            continue
        base = os.path.basename(name)
        if not base:
            continue
        store.store_attachment(
            new_id,
            base,
            zf.read(name),
            mime_type="",
            role="attachment",
            display_name=base,
        )

    if "events.jsonl" in names:
        text = zf.read("events.jsonl").decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # Old session ids are embedded in attachment URLs and payload
            # references; rewrite them wholesale before parsing.
            line = line.replace(old_id, new_id)
            try:
                raw = json.loads(line)
                # A backup restores as a new thread, so event identities must
                # be regenerated even though their order and payload remain.
                raw.pop("id", None)
                raw["session_id"] = new_id
                if isinstance(raw.get("data"), dict):
                    _rewrite_attachment_paths(raw["data"], store, new_id)
                event = EventEnvelope(**raw)
            except Exception:
                continue
            store.append_event(event)

    for name in sorted(names):
        if not name.startswith("changes/") or not name.endswith(".json"):
            continue
        try:
            manifest = json.loads(zf.read(name).decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(manifest, dict) and manifest.get("run_id"):
            manifest["session_id"] = new_id
            store.save_change_manifest(new_id, manifest)

    if "codex/summary.json" in names:
        try:
            summary_text = zf.read("codex/summary.json").decode("utf-8", errors="replace")
            summary = json.loads(summary_text.replace(old_id, new_id))
        except (json.JSONDecodeError, UnicodeDecodeError):
            summary = None
        if isinstance(summary, dict):
            store.write_codex_summary(new_id, summary)

    refreshed = store.load_session(new_id)
    return refreshed if refreshed is not None else new_meta
