import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import performance_log


class PerformanceLogTests(unittest.TestCase):
    def test_records_bounded_measurements_in_a_rotating_log(self):
        logger = performance_log._logger
        previous = logger.handlers[:]
        logger.handlers = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                performance_log.configure(tmp)
                performance_log.record('session_http', time.perf_counter(), route='/api/sessions', status=200)
                handler = logger.handlers[0]
                self.assertEqual(handler.backupCount, 3)
                self.assertEqual(handler.maxBytes, 2_000_000)
                handler.flush()
                content = (Path(tmp) / 'performance.log').read_text()
                self.assertIn('"phase": "session_http"', content)
                self.assertIn('"status": 200', content)
                handler.close()
        finally:
            for handler in logger.handlers:
                handler.close()
            logger.handlers = previous

    def test_unserializable_measurements_never_raise(self):
        performance_log.record('broken', time.perf_counter(), value=object())


if __name__ == '__main__':
    unittest.main()
