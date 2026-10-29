import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/agent')]
from backend import web_app
from backend.web_routes.runs import edit_pending_message, _QueuedMessageEdit
from backend.session_store import SessionStore
from backend.web_models import QueuedMessage
from backend.web_session import SessionManager
from fastapi import HTTPException


class PendingMessageEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(self.tmp.name)
        self.store.ensure_project('p', 'Project', self.tmp.name)
        self.store.ensure_task('p', 't', 'Task')
        self.meta = self.store.create_session('p', 't')
        self.first = QueuedMessage(text='first', attachments=[{'path': '/tmp/attachment.pdf'}])
        self.second = QueuedMessage(text='second')
        self.steering = QueuedMessage(text='guide')
        self.meta.message_queue = [self.first, self.second]
        self.meta.steering_queue = [self.steering]
        self.store.update_session(self.meta)
        self.manager = SessionManager()
        self.run = self.manager.start_run(self.meta.id)
        self.addCleanup(patch.stopall)
        patch.object(web_app, '_store', self.store).start()
        patch.object(web_app, '_manager', self.manager).start()

    def test_edits_without_changing_order_or_attachments(self):
        edit_pending_message(self.meta.id, self.first.id, _QueuedMessageEdit(text='revised', expected_text='first'))
        queue = self.store.load_session(self.meta.id).message_queue
        self.assertEqual([q.id for q in queue], [self.first.id, self.second.id])
        self.assertEqual(queue[0].text, 'revised')
        self.assertEqual(queue[0].attachments, self.first.attachments)

    def test_edit_steering_broadcasts_both_queues(self):
        result = edit_pending_message(self.meta.id, self.steering.id, _QueuedMessageEdit(text='new guide', expected_text='guide'))
        self.assertEqual(result['steering_items'][0]['text'], 'new guide')
        self.assertEqual(len(result['items']), 2)

    def test_consumed_stale_and_inflight_messages_reject_edits(self):
        for message_id, expected, inflight in [('gone', 'x', set()), (self.first.id, 'old text', set()), (self.steering.id, 'guide', {self.steering.id})]:
            with self.subTest(message_id=message_id):
                self.run.steering_in_flight = inflight
                with self.assertRaises(HTTPException) as error:
                    edit_pending_message(self.meta.id, message_id, _QueuedMessageEdit(text='changed', expected_text=expected))
                self.assertEqual(error.exception.status_code, 409)

    def test_invalid_text_rejected(self):
        for text in ['', '   ', '/clear']:
            with self.assertRaises(HTTPException) as error:
                edit_pending_message(self.meta.id, self.second.id, _QueuedMessageEdit(text=text, expected_text='second'))
            self.assertEqual(error.exception.status_code, 400)

    def test_can_clear_text_while_preserving_attachment(self):
        edit_pending_message(self.meta.id, self.first.id, _QueuedMessageEdit(text='', expected_text='first'))
        saved = self.store.load_session(self.meta.id).message_queue[0]
        self.assertEqual(saved.text, '')
        self.assertEqual(saved.attachments, self.first.attachments)
