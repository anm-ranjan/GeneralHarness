import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from web_models import EventEnvelope, EventType
from web_session import ConnectionManager


class FakeSocket:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []
        self.closed = False

    async def accept(self):
        pass

    async def send_json(self, payload):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append(payload)

    async def close(self):
        self.closed = True


class BroadcastTests(unittest.TestCase):
    def test_a_socket_that_fails_a_send_is_closed_so_the_client_reconnects(self):
        async def scenario():
            manager = ConnectionManager()
            good, bad = FakeSocket(), FakeSocket(fail=True)
            await manager.connect("ses_1", good)
            await manager.connect("ses_1", bad)
            event = EventEnvelope(session_id="ses_1", type=EventType.STATUS, data={"text": "hi"})
            await manager.broadcast("ses_1", event)
            await manager.broadcast("ses_1", event)
            return good, bad

        good, bad = asyncio.run(scenario())
        self.assertEqual(len(good.sent), 2)
        self.assertTrue(bad.closed)
        self.assertFalse(good.closed)


if __name__ == "__main__":
    unittest.main()
