from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from codex_history_import import ProjectCatalog, convert, snapshot_codex, source_session_id
from session_store import CHATS_PROJECT_ID, SessionStore
from web_models import EventEnvelope, EventType, SessionMeta


class FakeHistory:
    def __init__(self, threads, turns):
        self.threads = threads
        self.saved_turns = turns

    async def pages(self, method, *, archived, **_):
        assert method == "thread/list"
        for thread in self.threads:
            if thread["archived"] == archived:
                yield thread

    async def turns(self, thread):
        for turn in self.saved_turns[thread["id"]]:
            yield turn


class CodexHistoryImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name).resolve()
        self.source = directory / "codex"
        self.source.mkdir()
        self.root = directory / "project"
        self.nested = self.root / "nested"
        self.nested.mkdir(parents=True)
        state = self.source / "state_5.sqlite"
        with closing(sqlite3.connect(state)) as db, db:
            db.executescript("""
                CREATE TABLE projects (id TEXT, name TEXT, position INTEGER);
                CREATE TABLE project_roots (project_id TEXT, path TEXT, position INTEGER);
                CREATE TABLE threads (id TEXT, rollout_path TEXT);
                INSERT INTO projects VALUES ('primary', 'Project', 0), ('nested', 'Nested', 1);
            """)
            db.executemany("INSERT INTO project_roots VALUES (?, ?, 0)", [
                ("primary", str(self.root)), ("nested", str(self.nested)),
            ])
        (self.source / ".codex-global-state.json").write_text(json.dumps({
            "local-projects": {
                "legacy": {"name": "Project", "rootPaths": [str(self.root)]},
            },
            "thread-project-assignments": {"explicit": {"projectKind": "local", "projectId": "legacy"}},
            "projectless-thread-ids": ["standalone"],
        }), encoding="utf-8")
        self.catalog = ProjectCatalog(state, self.source)
        self.store = SessionStore(str(directory / "target"))
        self.addCleanup(self.store.close)

    def test_snapshot_preserves_compressed_rollouts_without_touching_source(self):
        archive = self.source / "archived_sessions" / "rollout-parent.jsonl"
        archive.parent.mkdir()
        archive.with_suffix(".jsonl.zst").write_bytes(b"compressed rollout")
        state = self.source / "state_5.sqlite"
        with closing(sqlite3.connect(state)) as db, db:
            db.execute("INSERT INTO threads VALUES (?, ?)", ("fork", str(archive)))
        snapshot = self.source.parent / "snapshot"
        snapshot.mkdir()

        copied = snapshot_codex(self.source, snapshot)

        relocated = snapshot / archive.relative_to(self.source)
        self.assertEqual(relocated.with_suffix(".jsonl.zst").read_bytes(), b"compressed rollout")
        for db_path, expected in ((state, archive), (copied, relocated)):
            with closing(sqlite3.connect(db_path)) as db:
                self.assertEqual(db.execute("SELECT rollout_path FROM threads").fetchone()[0], str(expected))

    def test_import_preserves_history_projects_and_archive_status(self):
        existing = self.store.ensure_project("existing-project", "Existing name", str(self.root))
        old_thread = self.store.create_session(existing.id, "", "Already here")
        threads = [
            {"id": "explicit", "name": "Original title", "preview": "Prompt", "cwd": str(self.nested),
             "archived": False, "createdAt": 1700000000, "updatedAt": 1700000100},
            {"id": "archive", "preview": "Archived prompt", "cwd": str(self.nested), "archived": True},
            {"id": "standalone", "preview": "Projectless prompt", "cwd": str(self.nested), "archived": False},
        ]
        image = b"fixture image bytes"
        turn = {"status": "completed", "startedAt": 1700000003, "completedAt": 1700000005, "items": [
            {"type": "userMessage", "content": [
                {"type": "text", "text": "Prompt"},
                {"type": "image", "url": "data:image/png;base64," + base64.b64encode(image).decode()},
                {"type": "localImage", "path": str(self.source / "missing.png")},
            ]},
            {"type": "reasoning", "summary": ["private reasoning"]},
            {"type": "commandExecution", "command": "pwd", "aggregatedOutput": "project", "exitCode": 0},
            {"type": "agentMessage", "text": "Answer", "completedAtMs": 1700000004200},
        ]}
        history = FakeHistory(threads, {"explicit": [turn], "archive": [turn], "standalone": [turn]})

        def run():
            return asyncio.run(convert(history, self.catalog, self.source, self.store))

        result = run()
        self.assertEqual((result["projects"], result["threads"], result["archived_threads"]), (2, 3, 1))
        self.assertEqual((result["standalone_chats"], result["images"], result["unavailable_images"]), (1, 3, 3))
        imported = self.store.load_session(source_session_id("explicit"))
        self.assertEqual((imported.project_id, imported.working_directory), (existing.id, str(self.nested)))
        self.assertEqual((imported.title, imported.provider, imported.codex_state), ("Original title", "codex-app-server", {}))
        self.assertEqual(imported.updated_at.timestamp(), 1700000100)
        self.assertEqual(self.store.get_project(existing.id).name, "Existing name")
        archived = self.store.load_session(source_session_id("archive"))
        self.assertTrue(archived.archived)
        self.assertEqual(self.store.get_project(archived.project_id).root, str(self.nested))
        self.assertEqual(self.store.load_session(source_session_id("standalone")).project_id, CHATS_PROJECT_ID)
        events = self.store.load_events(imported.id)
        self.assertEqual([event.type for event in events], [
            EventType.USER_MESSAGE, EventType.TOOL_CALL, EventType.TOOL_RESULT,
            EventType.ASSISTANT_MESSAGE, EventType.RUN_FINISHED,
        ])
        self.assertEqual(events[0].data["text"], "Prompt\n\n[Codex image unavailable]")
        filename = events[0].data["images"][0]["filename"]
        self.assertEqual(self.store.read_attachment(imported.id, filename), (image, "image/png"))
        self.assertAlmostEqual(events[3].created_at.timestamp(), 1700000004.2)
        self.assertIn("Codex: Answer", self.store.load_codex_summary(imported.id)["working_summary"])

        self.store.rename_session(imported.id, "Edited after import")
        duplicate = run()
        self.assertEqual((duplicate["threads"], duplicate["already_imported"]), (0, 3))
        self.assertEqual(self.store.load_session(imported.id).title, "Edited after import")
        self.assertEqual(self.store.event_count(imported.id), len(events))
        self.assertEqual(self.store.load_session(old_thread.id).title, "Already here")

    def test_store_rolls_back_thread_on_event_failure(self):
        self.store.ensure_chats_bucket()
        now = datetime.now(timezone.utc)
        meta = SessionMeta(
            id="test_import", project_id=CHATS_PROJECT_ID, task_id=CHATS_PROJECT_ID,
            title="Import", created_at=now, updated_at=now,
        )
        event = EventEnvelope(session_id=meta.id, type=EventType.USER_MESSAGE, data={"text": "Hello"})
        wrong_thread = event.model_copy(update={"session_id": "another_thread"})
        with self.assertRaisesRegex(ValueError, "another session"):
            self.store.insert_imported_session(meta, [event, wrong_thread], {}, {})
        self.assertIsNone(self.store.load_session(meta.id))
        self.assertEqual(self.store.event_count(meta.id), 0)
        self.assertTrue(self.store.insert_imported_session(meta, [event], {}, {}))


if __name__ == "__main__":
    unittest.main()
