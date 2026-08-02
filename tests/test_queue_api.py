import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException

from backend import web_app
from backend.session_store import SessionStore
from backend.web_models import EventType, QueuedMessage


def _make_session(store):
    store.ensure_project("proj", "Project", "/tmp/project")
    store.ensure_task("proj", "task", "Task")
    return store.create_session("proj", "task", "Session")


class RemoveQueuedMessageTests(unittest.TestCase):
    def test_removes_queued_message_and_emits_queue_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = _make_session(store)
            first = QueuedMessage(text="keep me")
            second = QueuedMessage(text="drop me")
            meta.message_queue = [first, second]
            store.update_session(meta)

            with patch.object(web_app, "_store", store):
                res = web_app.remove_queued_message(meta.id, second.id)

            self.assertEqual(res["status"], "removed")
            self.assertEqual([i["id"] for i in res["items"]], [first.id])
            remaining = store.load_session(meta.id).message_queue
            self.assertEqual([i.id for i in remaining], [first.id])
            queue_events = [
                e for e in store.load_events(meta.id)
                if e.type == EventType.QUEUE_UPDATED
            ]
            self.assertTrue(queue_events)
            self.assertEqual(
                [i["id"] for i in queue_events[-1].data["items"]], [first.id]
            )

    def test_removes_saved_queue_image_blob_and_materialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = _make_session(store)
            image = store.store_attachment(
                meta.id, "queued_image.png", b"png-bytes", mime_type="image/png", role="image"
            )
            image_path = Path(image["path"])
            queued = QueuedMessage(text="", images=[image])
            meta.message_queue = [queued]
            store.update_session(meta)

            with patch.object(web_app, "_store", store):
                web_app.remove_queued_message(meta.id, queued.id)

            self.assertFalse(image_path.exists())
            self.assertIsNone(store.read_attachment(meta.id, "queued_image.png"))
            self.assertEqual(store.load_session(meta.id).message_queue, [])

    def test_removes_saved_queue_attachment_blob_and_materialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = _make_session(store)
            attachment = store.store_attachment(
                meta.id, "queued_notes.pdf", b"pdf-bytes", mime_type="application/pdf"
            )
            file_path = Path(attachment["path"])
            queued = QueuedMessage(text="", attachments=[attachment])
            meta.message_queue = [queued]
            store.update_session(meta)

            with patch.object(web_app, "_store", store):
                web_app.remove_queued_message(meta.id, queued.id)

            self.assertFalse(file_path.exists())
            self.assertIsNone(store.read_attachment(meta.id, "queued_notes.pdf"))
            self.assertEqual(store.load_session(meta.id).message_queue, [])

    def test_unknown_session_and_message_return_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = _make_session(store)

            with patch.object(web_app, "_store", store):
                with self.assertRaises(HTTPException) as ctx:
                    web_app.remove_queued_message("ses_missing", "qmsg_x")
                self.assertEqual(ctx.exception.status_code, 404)

                with self.assertRaises(HTTPException) as ctx:
                    web_app.remove_queued_message(meta.id, "qmsg_missing")
                self.assertEqual(ctx.exception.status_code, 404)


class ReorderQueuedMessagesTests(unittest.TestCase):
    def test_reorders_queue_and_emits_queue_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = _make_session(store)
            a = QueuedMessage(text="a")
            b = QueuedMessage(text="b")
            c = QueuedMessage(text="c")
            meta.message_queue = [a, b, c]
            store.update_session(meta)

            with patch.object(web_app, "_store", store):
                res = web_app.reorder_queued_messages(
                    meta.id, web_app._QueueReorderRequest(order=[c.id, a.id, b.id])
                )

            self.assertEqual(res["status"], "reordered")
            self.assertEqual([i["id"] for i in res["items"]], [c.id, a.id, b.id])
            persisted = store.load_session(meta.id).message_queue
            self.assertEqual([i.id for i in persisted], [c.id, a.id, b.id])
            queue_events = [
                e for e in store.load_events(meta.id)
                if e.type == EventType.QUEUE_UPDATED
            ]
            self.assertTrue(queue_events)
            self.assertEqual(
                [i["id"] for i in queue_events[-1].data["items"]],
                [c.id, a.id, b.id],
            )

    def test_stale_or_mismatched_order_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = _make_session(store)
            a = QueuedMessage(text="a")
            b = QueuedMessage(text="b")
            meta.message_queue = [a, b]
            store.update_session(meta)

            with patch.object(web_app, "_store", store):
                # Missing an id
                with self.assertRaises(HTTPException) as ctx:
                    web_app.reorder_queued_messages(
                        meta.id, web_app._QueueReorderRequest(order=[a.id])
                    )
                self.assertEqual(ctx.exception.status_code, 409)

                # Unknown id
                with self.assertRaises(HTTPException) as ctx:
                    web_app.reorder_queued_messages(
                        meta.id, web_app._QueueReorderRequest(order=[a.id, "qmsg_ghost"])
                    )
                self.assertEqual(ctx.exception.status_code, 409)

                # Unknown session
                with self.assertRaises(HTTPException) as ctx:
                    web_app.reorder_queued_messages(
                        "ses_missing", web_app._QueueReorderRequest(order=[])
                    )
                self.assertEqual(ctx.exception.status_code, 404)

            # Queue unchanged after rejected reorders
            persisted = store.load_session(meta.id).message_queue
            self.assertEqual([i.id for i in persisted], [a.id, b.id])


if __name__ == "__main__":
    unittest.main()
