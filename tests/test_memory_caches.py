"""Bounded-memory and caching behaviour for the hot run-loop paths."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (str(BACKEND), str(AGENT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import harness_agent as agent
import web_app
import web_ui_adapter


class EstimateTokensCacheTests(unittest.TestCase):
    def setUp(self):
        agent._TOKEN_COUNT_CACHE.clear()

    def test_counts_are_stable_and_additive(self):
        first = [{"role": "user", "content": "hello world"}]
        second = first + [{"role": "assistant", "content": "hi there, friend"}]

        a = agent.estimate_tokens(first)
        b = agent.estimate_tokens(second)
        self.assertGreater(b, a)
        # Repeat calls hit the cache and must not drift.
        self.assertEqual(agent.estimate_tokens(first), a)
        self.assertEqual(agent.estimate_tokens(second), b)

    def test_appending_reuses_cached_message_counts(self):
        messages = [{"role": "user", "content": f"message number {i}"} for i in range(20)]
        agent.estimate_tokens(messages)
        entries_after_first = len(agent._TOKEN_COUNT_CACHE)

        messages.append({"role": "user", "content": "one more"})
        agent.estimate_tokens(messages)
        # Exactly one new message was encoded, not all 21.
        self.assertEqual(len(agent._TOKEN_COUNT_CACHE), entries_after_first + 1)

    def test_changed_content_is_not_served_from_cache(self):
        original = [{"role": "user", "content": "short"}]
        baseline = agent.estimate_tokens(original)
        edited = [{"role": "user", "content": "short " + "much longer content " * 50}]
        self.assertGreater(agent.estimate_tokens(edited), baseline)

    def test_cache_is_bounded(self):
        limit = agent._TOKEN_COUNT_CACHE_MAX
        for i in range(limit + 10):
            agent.estimate_tokens([{"role": "user", "content": f"unique-{i}"}])
        self.assertLessEqual(len(agent._TOKEN_COUNT_CACHE), limit)

    def test_non_serializable_content_does_not_raise(self):
        class Odd:
            def __repr__(self):
                return "<odd>"

        self.assertGreater(agent.estimate_tokens([{"role": "user", "content": Odd()}]), 0)


class FileSnapshotEvictionTests(unittest.TestCase):
    def setUp(self):
        self._saved = web_ui_adapter._file_snapshots.copy()
        self._saved_budget = web_ui_adapter.SNAPSHOT_CACHE_MAX_BYTES
        web_ui_adapter._file_snapshots.clear()

    def tearDown(self):
        web_ui_adapter._file_snapshots.clear()
        web_ui_adapter._file_snapshots.update(self._saved)
        web_ui_adapter.SNAPSHOT_CACHE_MAX_BYTES = self._saved_budget

    def test_evicts_least_recently_written_sessions_over_budget(self):
        web_ui_adapter.SNAPSHOT_CACHE_MAX_BYTES = 1000
        for name in ("old", "middle", "active"):
            web_ui_adapter._file_snapshots[name] = {f"/{name}.py": "x" * 600}

        web_ui_adapter._evict_snapshots("active")

        self.assertNotIn("old", web_ui_adapter._file_snapshots)
        # The in-flight session is retained regardless of budget pressure.
        self.assertIn("active", web_ui_adapter._file_snapshots)

    def test_no_eviction_while_within_budget(self):
        web_ui_adapter.SNAPSHOT_CACHE_MAX_BYTES = 10_000
        web_ui_adapter._file_snapshots["a"] = {"/a.py": "x" * 100}
        web_ui_adapter._file_snapshots["b"] = {"/b.py": "x" * 100}

        web_ui_adapter._evict_snapshots("b")

        self.assertEqual(list(web_ui_adapter._file_snapshots), ["a", "b"])

    def test_none_snapshots_do_not_break_accounting(self):
        # read_capped returns None for unreadable files.
        self.assertEqual(web_ui_adapter._session_snapshot_bytes({"/x": None}), 0)


class SessionMessageCacheTests(unittest.TestCase):
    def test_evicts_cold_sessions_beyond_the_cap(self):
        cache = web_app._SessionMessageCache()
        cache.max_entries = 3
        for i in range(5):
            cache[f"ses_{i}"] = [{"role": "user", "content": str(i)}]

        self.assertLessEqual(len(cache), 3)
        self.assertIn("ses_4", cache)
        self.assertNotIn("ses_0", cache)

    def test_reads_refresh_recency(self):
        cache = web_app._SessionMessageCache()
        cache.max_entries = 2
        cache["a"] = [1]
        cache["b"] = [2]
        self.assertEqual(cache["a"], [1])  # 'a' becomes most recent
        cache["c"] = [3]

        self.assertIn("a", cache)
        self.assertNotIn("b", cache)

    def test_active_runs_are_never_evicted(self):
        cache = web_app._SessionMessageCache()
        cache.max_entries = 1

        web_app._manager.start_run("running")
        try:
            cache["running"] = [1]
            cache["other"] = [2]
            cache["newest"] = [3]
            # Over budget, but the in-flight session survives and the idle one
            # is the entry that gets dropped.
            self.assertIn("running", cache)
            self.assertNotIn("other", cache)
        finally:
            web_app._manager.end_run("running")

    def test_get_returns_default_for_missing_session(self):
        cache = web_app._SessionMessageCache()
        self.assertIsNone(cache.get("nope"))
        self.assertEqual(cache.get("nope", []), [])


if __name__ == "__main__":
    unittest.main()
