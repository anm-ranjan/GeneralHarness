"""No-network native working-history, evidence and recovery regressions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout
import io

import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import harness_agent as agent
import utils
import history_tokens
from history_evidence import HistoryEvidence
import web_app
import web_helpers
import session_backup
import session_context
from session_store import SessionStore
from web_models import EventEnvelope, EventType
from tests.test_prompt_cache import skill_messages


def exchange(name="file_read", args=None, result="output", call_id="t1", **extra):
    return [{"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args or {"file_path": "a.py"})}}]},
            {"role": "tool", "tool_call_id": call_id, "content": result, **extra}]


def large_history():
    return [{"role": "system", "content": "rules"}, {"role": "user", "content": "Fix a.py. Never delete input.csv."},
            *exchange("file_write", {"file_path": "a.py", "content": "x = 1\n" * 1000}, "OK: Wrote a.py"),
            {"role": "user", "content": "Preserve the public API.", "steering": True},
            *exchange("shell_run", {"command": "python -m unittest"}, "Exit code: 1\nFAILED: test_boundary", call_id="t2"),
            {"role": "assistant", "content": "Still need to resolve test_boundary."},
            {"role": "user", "content": "Continue"}, {"role": "assistant", "content": "Checking"}]


class AccountingAndCompressionTests(unittest.TestCase):
    def test_categories_include_schemas_and_ignore_private_recovery_fields(self):
        messages = large_history()
        original = deepcopy(messages)
        categories = agent.estimate_input_categories(messages, agent.TOOLS)
        for key in ("instructions", "user_text", "tool_schemas", "tool_arguments", "tool_results", "assistant_text", "overhead"):
            self.assertGreater(categories[key], 0)
        self.assertEqual(categories["total"], sum(v for k, v in categories.items() if k != "total"))
        messages[1]["_private"] = "not transmitted" * 100
        self.assertEqual(agent.estimate_tokens(messages), agent.estimate_tokens(original))
        self.assertEqual(agent.estimate_input_categories(messages, agent.TOOLS), categories)
        self.assertNotIn("_private", history_tokens.wire_messages(messages)[1])

    def test_short_or_highly_repetitive_content_never_expands(self):
        for content in ("x" * 600, "a\n" * 400, "print(1)\n" * 500):
            messages = exchange("file_write", {"file_path": "a.py", "content": content}, "OK")
            before = agent.estimate_tokens(messages)
            utils.compress_turn_tool_results(messages, 0)
            self.assertLessEqual(agent.estimate_tokens(messages), before)
            once = deepcopy(messages)
            utils.compress_turn_tool_results(messages, 0)
            self.assertEqual(messages, once)

    def test_shell_keeps_tail_failures_exit_code_and_test_outcome(self):
        result = "Exit code: 1\n" + "progress: still running\n" * 500 + "ERROR: assertion at a.py:99\nRan 20 tests\nFAILED (failures=1)"
        messages = exchange("shell_run", {"command": "python -m unittest"}, result, _evidence_id="a" * 64)
        before = agent.estimate_tokens(messages)
        utils.compress_turn_tool_results(messages, 0)
        text = messages[1]["content"]
        for expected in ("Exit code: 1", "a.py:99", "Ran 20 tests", "FAILED (failures=1)", "a" * 64):
            self.assertIn(expected, text)
        self.assertLess(agent.estimate_tokens(messages), before)

    def test_search_keeps_locations_and_read_keeps_provenance(self):
        for name in ("content_search", "file_read"):
            result = "\n".join(f"src/a.py:{i}: relevant line {i}" for i in range(500))
            messages = exchange(name, {"file_path": "src/a.py", "query": "relevant", "offset": 42}, result)
            utils.compress_turn_tool_results(messages, 0)
            text = messages[1]["content"]
            self.assertIn("src/a.py:0:", text)
            self.assertIn("src/a.py:499:", text)
            self.assertIn('"offset": 42', text)
            self.assertIn("reread current file", text)

    def test_unsuccessful_or_unexecuted_write_arguments_remain_exact(self):
        for result in (None, "ERROR: Tool call denied by user."):
            messages = exchange("file_replace", {"file_path": "a.py", "old_text": "exact old", "new_text": "new\n" * 1000}, result or "")
            if result is None:
                messages.pop()
            before = deepcopy(messages)
            utils.compress_turn_tool_results(messages, 0)
            self.assertEqual(messages, before)

    def test_archive_failure_preserves_only_copy_of_arguments_and_results(self):
        messages = exchange("file_write", {"file_path": "a.py", "content": "line\n" * 1000},
                            "Success\n" * 1000, _preserve_evidence=True)
        before = deepcopy(messages)
        utils._aggressive_compress_tool_results(messages, 0)
        self.assertEqual(messages, before)

    def test_identical_reads_require_matching_observed_evidence_hash(self):
        result = "\n".join(f"line {i}" for i in range(1000))
        messages = [*exchange(result=result, _evidence_id="a" * 64),
                    *exchange(result=result, call_id="t2", _evidence_id="a" * 64),
                    *exchange(result=result + "changed", call_id="t3", _evidence_id="b" * 64)]
        utils.compress_turn_tool_results(messages, 0)
        self.assertIn("Repeated identical", messages[1]["content"])
        self.assertNotIn("Repeated identical", messages[5]["content"])

    def test_cross_turn_deduplication_keeps_completed_prefix_stable(self):
        result = "\n".join(f"versioned line {i}" for i in range(500))
        messages = exchange(result=result, _evidence_id="a" * 64)
        prefix = deepcopy(messages)
        messages.extend(exchange(result=result, call_id="t2", _evidence_id="a" * 64))
        utils.compress_turn_tool_results(messages, 2)
        self.assertEqual(messages[:2], prefix)
        self.assertIn("Repeated identical", messages[-1]["content"])


class CompactionTests(unittest.TestCase):
    def test_multicall_exchange_is_kept_whole(self):
        calls = exchange()[0]
        calls["tool_calls"].append(deepcopy(calls["tool_calls"][0]))
        calls["tool_calls"][1]["id"] = "t2"
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "goal"}, calls,
                    {"role": "tool", "tool_call_id": "t1", "content": "one"},
                    {"role": "tool", "tool_call_id": "t2", "content": "two"},
                    {"role": "assistant", "content": "done"}]
        with patch.object(utils, "KEEP_RECENT_MESSAGES", 2):
            _, _, old, recent = agent.split_history_for_compaction(messages)
        self.assertEqual(old, messages[1:2])
        self.assertEqual(recent, messages[2:])
        self.assertTrue(web_helpers._valid_native_context(recent))

    def test_summary_failure_keeps_exact_requests_steering_and_failed_tests(self):
        for failure in (None, RuntimeError("unavailable"), "x " * 10000):
            messages = large_history()
            before = deepcopy(messages)
            summary = patch.object(agent, "summarize_history", side_effect=failure) if isinstance(failure, Exception) else patch.object(agent, "summarize_history", return_value=failure)
            with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 1), patch.object(utils, "KEEP_RECENT_MESSAGES", 2), summary:
                compacted = agent.compact_history_if_needed(messages, ui=Mock())
            self.assertEqual(messages, before)
            self.assertLess(agent.estimate_tokens(compacted), agent.estimate_tokens(messages))
            state = json.loads(compacted[1]["content"].split("\n", 1)[1])
            self.assertIn("Fix a.py. Never delete input.csv.", state["requests"])
            self.assertIn("Preserve the public API.", state["requests"])
            self.assertIn("test_boundary", json.dumps(state))
            self.assertLessEqual(history_tokens.count_value(state), utils.TASK_STATE_TOKENS)
            self.assertEqual(compacted[-2:], messages[-2:])
            self.assertTrue(web_helpers._valid_native_context(compacted[1:]))

    def test_task_state_budget_preserves_previous_state_and_refuses_constraint_loss(self):
        history = large_history()
        with patch.object(utils, "TASK_STATE_TOKENS", 600):
            state = agent.build_native_task_state([], history[1:-2])
            self.assertIsNotNone(state)
            self.assertLessEqual(history_tokens.count_value(state), 600)
            memory = [{"role": "system", "content": utils.MEMORY_SUMMARY_MARKER + "\n" + json.dumps(state)}]
            newer = [{"role": "user", "content": "Keep my units in mm."}, {"role": "assistant", "content": "Will do"}]
            next_state = agent.build_native_task_state(memory, newer)
            self.assertIn("Never delete input.csv.", next_state["requests"][0])
            self.assertIn("Keep my units in mm.", next_state["requests"])
            self.assertIsNone(agent.build_native_task_state([], [{"role": "user", "content": "constraint " * 3000}]))

    def test_no_shrink_means_no_context_replacement(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "one"},
                    {"role": "assistant", "content": "two"}, {"role": "user", "content": "three"}]
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 1), patch.object(utils, "KEEP_RECENT_MESSAGES", 1), patch.object(agent, "summarize_history", return_value="summary"):
            self.assertIs(agent.compact_history_if_needed(messages, ui=Mock()), messages)

    def test_skills_survive_failure_without_truncation(self):
        messages = large_history()
        instructions = "Never round intermediate results.\n" * 300
        messages[2:2] = skill_messages(text=instructions)
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 1), patch.object(utils, "KEEP_RECENT_MESSAGES", 2), patch.object(agent, "summarize_history", return_value=""):
            compacted = agent.compact_history_if_needed(messages, ui=Mock())
        self.assertIn(instructions, [m.get("content") for m in compacted if m.get("role") == "tool"])
        self.assertTrue(web_helpers._valid_native_context(compacted[1:]))

    def test_repeated_compaction_has_overall_task_state_budget(self):
        memory = []
        with patch.object(utils, "TASK_STATE_TOKENS", 700):
            for index in range(60):
                history = [{"role": "user", "content": f"Task {index}: preserve constraints."},
                           *exchange("shell_run", {"command": f"verify stage{index}"}, "Exit code: 0\npassed", call_id=f"t{index}"),
                           {"role": "assistant", "content": f"Stage {index} verified."}]
                state = agent.build_native_task_state(memory, history)
                if state is None:
                    # Protected requests eventually exhaust a tiny test budget;
                    # history is retained, never silently clipped.
                    break
                self.assertLessEqual(history_tokens.count_value(state), 700)
                self.assertEqual(len(state["requests"]), index + 1)
                memory = [{"role": "system", "content": utils.MEMORY_SUMMARY_MARKER + "\n" + json.dumps(state)}]
            self.assertGreater(state["omitted_facts"] if state else index, 0)

    def test_archive_failure_prevents_compaction_of_the_only_evidence(self):
        messages = large_history()
        messages[3]["_preserve_evidence"] = True
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 1), patch.object(utils, "KEEP_RECENT_MESSAGES", 2), patch.object(agent, "summarize_history") as summarize:
            self.assertIs(agent.compact_history_if_needed(messages, ui=Mock()), messages)
        summarize.assert_not_called()

    def test_compaction_does_not_silently_drop_visual_constraints(self):
        messages = large_history()
        messages[1]["content"] = [{"type": "text", "text": "Preserve this diagram's constraints"},
                                  {"type": "image_url", "image_url": {"url": "data:image/png;base64,synthetic"}}]
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 1), patch.object(utils, "KEEP_RECENT_MESSAGES", 2), patch.object(agent, "summarize_history") as summarize:
            self.assertIs(agent.compact_history_if_needed(messages, ui=Mock()), messages)
        summarize.assert_not_called()

    def test_compaction_request_usage_is_reported_once_including_estimates(self):
        metrics = {"input_tokens": 100, "cost_usd": .001, "estimated_input": {"instructions": 10, "total": 95}}
        def api(*args, **kwargs):
            agent._set_api_metrics(metrics)
            return {"choices": [{"message": {"content": '{"verification":["test passed"]}'}, "finish_reason": "stop"}]}
        ui = Mock()
        with patch.object(agent, "call_api", side_effect=api):
            self.assertTrue(agent.summarize_history([], [], ui=ui))
        ui.show_api_metrics.assert_called_once_with({**metrics, "purpose": "history_compaction"})
        totals = agent.prompt_cache.UsageTotals()
        totals.add(ui.show_api_metrics.call_args.args[0])
        self.assertEqual(totals.snapshot()["estimated_input"]["total"], 95)
        self.assertEqual(totals.snapshot()["cost_usd"], .001)

    def test_summary_rejects_malformed_truncated_and_oversize_outputs(self):
        for content, finish in (("bad json", "stop"), ('{"decisions": ["partial"]}', "length"),
                                (json.dumps({"decisions": ["very long " * 2000]}), "stop"),
                                ('{"decisions": "wrong type"}', "stop")):
            with patch.object(agent, "call_api", return_value={"choices": [{"message": {"content": content}, "finish_reason": finish}]}) as api:
                self.assertEqual(agent.summarize_history([], []), "")
                self.assertEqual(api.call_args.kwargs["max_tokens"], utils.SUMMARY_TARGET_TOKENS + 256)


def nudge(count=52):
    return {"role": "user", agent.SYSTEM_NUDGE_FLAG: True,
            "content": agent.SYSTEM_NUDGE_PREFIX + f"{count} tool calls. Pause briefly."}


def long_session(turns=6, reads=12):
    """Shape of a long real session: many reads/runs per turn, an answered
    question, a tool-call nudge mid-turn, and a final report per turn."""
    history = []
    for turn in range(turns):
        history.append({"role": "user", "content": f"Request {turn}: keep outputs in folder {turn}."})
        for read in range(reads):
            history += exchange("file_read", {"file_path": f"t{turn}_r{read}.txt"},
                                f"observed {turn}-{read} " + "data " * 80, call_id=f"r{turn}_{read}")
        history += exchange("shell_run", {"command": f"run case {turn}"}, f"Exit code: 0\ncase {turn} ok",
                            call_id=f"s{turn}")
        if turn == 1:
            history += exchange("ask_user", {"question": "Which contour range?", "options": ["auto", "fixed"]},
                                "The user answered: minimum 0, maximum 0.5", call_id="q1")
        if turn == 2:
            history += exchange("shell_run", {"command": "check log"}, "Exit code: 1\nFATAL at t=0.994",
                                call_id="fail2")
            history.append({"role": "assistant", "content": "Probing further."})
            history.append(nudge())
        history.append({"role": "assistant", "content": f"Turn {turn} done: wrote report {turn}."})
    return history


class LongSessionTaskStateTests(unittest.TestCase):
    def test_question_answers_are_kept_as_requests(self):
        with patch.object(utils, "TASK_STATE_TOKENS", 1500):
            state = agent.build_native_task_state([], long_session())
        self.assertIsNotNone(state)
        self.assertIn("[Answer to agent question] Which contour range?\nminimum 0, maximum 0.5", state["requests"])
        # Answers sit in chronological order between the user's requests.
        answer_index = next(i for i, r in enumerate(state["requests"]) if r.startswith("[Answer"))
        self.assertTrue(state["requests"][answer_index - 1].startswith("Request 1:"))
        self.assertTrue(state["requests"][answer_index + 1].startswith("Request 2:"))

    def test_nudge_is_not_a_request_and_does_not_split_the_turn(self):
        state = agent.build_native_task_state([], long_session())
        self.assertFalse(any(agent.SYSTEM_NUDGE_PREFIX in r for r in state["requests"]))
        reports = [fact["assistant_report"] for fact in state["facts"] if "assistant_report" in fact]
        self.assertEqual(reports, [f"Turn {turn} done: wrote report {turn}." for turn in range(6)])

    def test_nudge_saved_before_the_flag_existed_is_still_recognised(self):
        legacy = {"role": "user", "content": agent.SYSTEM_NUDGE_PREFIX + "52 tool calls. Pause briefly."}
        state = agent.build_native_task_state([], [{"role": "user", "content": "Do it."}, legacy,
                                                   {"role": "assistant", "content": "Done."}])
        self.assertEqual(state["requests"], ["Do it."])
        prior = {**state, "requests": ["Do it.", legacy["content"]]}
        memory = [{"role": "system", "content": utils.MEMORY_SUMMARY_MARKER + "\n" + json.dumps(prior)}]
        merged = agent.build_native_task_state(memory, [{"role": "user", "content": "Next."}])
        self.assertEqual(merged["requests"], ["Do it.", "Next."])

    def test_nudge_flag_is_not_sent_to_the_provider(self):
        self.assertNotIn(agent.SYSTEM_NUDGE_FLAG, history_tokens.wire_messages([nudge()])[0])

    def test_valid_summary_survives_by_trimming_facts(self):
        summary = json.dumps({"decisions": ["Range fixed at 0-0.5"], "unfinished_work": []})
        with patch.object(utils, "TASK_STATE_TOKENS", 1500):
            state = agent.build_native_task_state([], long_session(), summary)
        self.assertEqual(state["summary"], summary)
        self.assertGreater(state["omitted_facts"], 0)
        self.assertLessEqual(history_tokens.count_value(state), 1500)

    def test_summary_that_cannot_fit_beside_requests_falls_back(self):
        summary = json.dumps({"decisions": [" ".join(f"detail{i}" for i in range(1500))]})
        with patch.object(utils, "TASK_STATE_TOKENS", 1000):
            state = agent.build_native_task_state([], long_session(), summary)
        self.assertEqual(state["summary"], "")
        self.assertLessEqual(history_tokens.count_value(state), 1000)

    def test_trimming_drops_reads_before_reports_runs_and_failures(self):
        with patch.object(utils, "TASK_STATE_TOKENS", 1500):
            state = agent.build_native_task_state([], long_session())
        facts = state["facts"]
        self.assertGreater(state["omitted_facts"], 0)
        # Every report and the failure survive; reads go first.
        self.assertEqual(sum("assistant_report" in f for f in facts), 6)
        self.assertTrue(any(f.get("exit_code") == 1 for f in facts))
        # Only low-priority facts were dropped (72 reads plus the ask_user
        # fact, whose answer is already a request): every shell run remains.
        self.assertEqual(sum(f.get("name") == "shell_run" for f in facts), 7)
        low = sum(f.get("name") in ("file_read", "ask_user") for f in facts)
        self.assertEqual(state["omitted_facts"], 73 - low)
        # Survivors keep their chronological order.
        reports = [f["assistant_report"] for f in facts if "assistant_report" in f]
        self.assertEqual(reports, sorted(reports, key=lambda r: int(r.split()[1])))

    def test_compaction_end_to_end_keeps_answers_and_summary(self):
        messages = [{"role": "system", "content": "rules"}, *long_session(),
                    {"role": "user", "content": "Latest request"}, {"role": "assistant", "content": "Latest answer"}]
        summary = json.dumps({"decisions": ["Range fixed at 0-0.5"]})
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 1), patch.object(utils, "KEEP_RECENT_MESSAGES", 2), \
                patch.object(utils, "TASK_STATE_TOKENS", 1500), patch.object(agent, "summarize_history", return_value=summary):
            compacted = agent.compact_history_if_needed(messages, ui=Mock())
        state = json.loads(compacted[1]["content"].split("\n", 1)[1])
        self.assertEqual(state["summary"], summary)
        self.assertIn("[Answer to agent question] Which contour range?\nminimum 0, maximum 0.5", state["requests"])

    def test_compaction_event_carries_the_memory_the_model_sees(self):
        messages = [{"role": "system", "content": "rules"}, *long_session(),
                    {"role": "user", "content": "Latest request"}, {"role": "assistant", "content": "Latest answer"}]
        ui = Mock()
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 1), patch.object(utils, "KEEP_RECENT_MESSAGES", 2), \
                patch.object(utils, "TASK_STATE_TOKENS", 1500), patch.object(agent, "summarize_history", return_value=""):
            compacted = agent.compact_history_if_needed(messages, ui=ui)
        before, after, memory = ui.show_compaction.call_args.args
        self.assertGreater(before, after)
        self.assertEqual(memory, json.loads(compacted[1]["content"].split("\n", 1)[1]))
        self.assertIsNone(agent.compaction_memory([{"role": "system", "content": "rules"}]))
        self.assertEqual(compacted[-2:], messages[-2:])
        self.assertTrue(web_helpers._valid_native_context(compacted[1:]))


class EvidenceAndRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SessionStore(self.temp.name)
        self.store.ensure_project("p", "Project", self.temp.name)
        self.store.ensure_task("p", "t", "Task")
        self.meta = self.store.create_session("p", "t", "History")
        patcher = patch.object(web_app, "_store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def event(self, kind, data):
        event = EventEnvelope(session_id=self.meta.id, type=kind, data=data)
        self.store.append_event(event)
        return event

    def completed(self):
        self.event(EventType.USER_MESSAGE, {"text": "original"})
        self.event(EventType.ASSISTANT_MESSAGE, {"markdown": "done"})
        return self.event(EventType.RUN_FINISHED, {"reason": "completed"})

    def test_evidence_is_paged_hash_checked_and_scoped(self):
        archive = self.store.native_evidence(self.meta.id)
        text = "important evidence\n" * 1000
        evidence_id = archive.archive("shell_run", {"command": "verify"}, text)
        self.assertEqual(archive.archive("shell_run", {"command": "verify"}, text), evidence_id)
        first = archive.read(evidence_id, limit=100)
        second = archive.read(evidence_id, offset=first["next_offset"], limit=100)
        self.assertEqual(first["text"] + second["text"], text[:200])
        self.assertEqual(first["sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertIn("error", self.store.native_evidence("other").read(evidence_id))
        self.assertIn("error", archive.read("../../sessions/secret"))
        found = json.loads(session_context.execute(self.store, self.meta.id, {"action": "evidence", "query": "verify"}))
        self.assertEqual(found["evidence"][0]["evidence_id"], evidence_id)
        (archive.root / f"{evidence_id}.json").write_text("tampered")
        self.assertIn("hash check", archive.read(evidence_id)["error"])
        self.assertEqual(archive.archive("shell_run", {"command": "verify"}, text), evidence_id)
        self.assertEqual(archive.read(evidence_id)["text"], text[:4000])

    def test_checkpoint_restores_tools_skills_and_later_completed_steering_only(self):
        self.completed()
        messages = [{"role": "system", "content": "old system"}, {"role": "user", "content": "original"},
                    *skill_messages(), *exchange(result="real evidence"), {"role": "assistant", "content": "done"}]
        web_helpers._save_native_context(self.meta.id, self.temp.name, messages)
        self.event(EventType.USER_MESSAGE, {"text": "later"})
        self.event(EventType.USER_MESSAGE, {"text": "new restriction", "steering": True})
        self.event(EventType.ASSISTANT_MESSAGE, {"markdown": "finished"})
        self.event(EventType.RUN_FINISHED, {"reason": "completed"})
        for reason in ("interrupted", "error", "api timeout"):
            self.event(EventType.USER_MESSAGE, {"text": "discard " + reason})
            self.event(EventType.ASSISTANT_MESSAGE, {"markdown": "partial " + reason})
            self.event(EventType.RUN_FINISHED, {"reason": reason})
        self.event(EventType.USER_MESSAGE, {"text": "crashed without finish"})
        restored = web_helpers._restore_session_messages(self.meta.id, self.temp.name)
        text = json.dumps(restored)
        self.assertIn("real evidence", text)
        self.assertIn("new restriction", text)
        self.assertNotIn("discard", text)
        self.assertNotIn("crashed without finish", text)
        self.assertNotEqual(restored[0]["content"], "old system")
        self.assertTrue(web_helpers._valid_native_context(restored[1:]))

    def test_clear_and_bad_checkpoints_cannot_revive_old_work(self):
        finished = self.completed()
        bad = [{"role": "tool", "tool_call_id": "orphan", "content": "bad"}]
        self.store.save_native_context(self.meta.id, {"schema": "myharness-native-context-v1",
            "workspace_root": self.temp.name, "through_event_id": finished.id, "messages": bad})
        restored = web_helpers._restore_session_messages(self.meta.id, self.temp.name)
        self.assertNotIn("orphan", json.dumps(restored))
        web_helpers._save_native_context(self.meta.id, self.temp.name, restored)
        self.event(EventType.USER_MESSAGE, {"text": "/clear"})
        self.assertEqual(len(web_helpers._restore_session_messages(self.meta.id, self.temp.name)), 1)

    def test_uncommitted_or_malformed_checkpoint_is_ignored(self):
        self.completed()
        body = [{"role": "user", "content": "uncommitted branch"}]
        self.store.save_native_context(self.meta.id, {"schema": "myharness-native-context-v1", "through_event_id": "missing",
            "workspace_root": self.temp.name, "messages": body})
        self.assertNotIn("uncommitted branch", json.dumps(web_helpers._restore_session_messages(self.meta.id, self.temp.name)))
        for malformed in ([{"role": ["tool"]}], [{"role": "assistant", "tool_calls": 4}],
                          [{"role": "tool", "tool_call_id": {}}]):
            self.assertFalse(web_helpers._valid_native_context(malformed))

    def test_backup_keeps_originals_but_import_discards_machine_checkpoint(self):
        self.completed()
        evidence_id = self.store.native_evidence(self.meta.id).archive("file_read", {"file_path": "a.py"}, "exact original")
        web_helpers._save_native_context(self.meta.id, self.temp.name, [{"role": "system", "content": "rules"},
            {"role": "user", "content": "original"}, *exchange(result="exact original", _evidence_id=evidence_id)])
        backup = session_backup.export_session_backup(self.store, self.meta)
        imported = session_backup.import_session_backup(self.store, backup)
        self.assertEqual(self.store.native_evidence(imported.id).read(evidence_id)["text"], "exact original")
        self.assertEqual(self.store.load_native_context(imported.id), {})

    def test_archived_file_text_does_not_authorise_a_stale_exact_edit(self):
        path = Path(self.temp.name) / "a.py"
        old_text, current_text = "value = 1\n", "value = 2\n"
        path.write_text(old_text)
        evidence_id = self.store.native_evidence(self.meta.id).archive("file_read", {"file_path": str(path)}, old_text)
        path.write_text(current_text)
        archived = self.store.native_evidence(self.meta.id).read(evidence_id)["text"]
        with patch.object(utils, "ALLOWED_PATHS", [self.temp.name]):
            result = utils.tool_file_replace(str(path), archived, "wrong edit\n")
        self.assertTrue(result.startswith("ERROR"))
        self.assertEqual(path.read_text(), current_text)


class RunBoundaryTests(unittest.TestCase):
    def test_denied_real_write_preserves_literal_arguments_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "denied.py"
            content = "original literal content\n" * 500
            call = exchange("file_write", {"file_path": str(path), "content": content})[0]
            responses = iter([{"choices": [{"message": call, "finish_reason": "tool_calls"}]},
                              {"choices": [{"message": {"role": "assistant", "content": "Denied."}, "finish_reason": "stop"}]}])
            ui = Mock(run_settings={"approval_mode": "always_ask", "verbose_tools": False})
            ui.request_approval.return_value = False
            ui.archive_tool_evidence = HistoryEvidence(Path(directory) / "evidence").archive
            messages = [{"role": "system", "content": "rules"}]
            with patch.object(utils, "ALLOWED_PATHS", [directory]), patch.object(agent, "log_interaction"):
                agent.run_agent("write", messages, max_iterations=2, ui=ui, api_caller=lambda *a, **kw: next(responses))
            ui.request_approval.assert_called_once()
            self.assertFalse(path.exists())
            self.assertEqual(json.loads(messages[2]["tool_calls"][0]["function"]["arguments"])["content"], content)
            self.assertIn("denied", messages[3]["content"])

    def test_failed_and_interrupted_runs_roll_back_working_context(self):
        for interrupt in (False, True):
            messages = [{"role": "system", "content": "rules"}]
            cancel = threading.Event()
            def model(*args, **kwargs):
                if interrupt:
                    cancel.set()
                return None
            with patch.object(agent, "log_interaction"), patch.object(agent, "select_tools", return_value=[]):
                agent.run_agent("keep no partial work", messages, max_iterations=2, ui=Mock(run_settings={}), api_caller=model, cancel_event=cancel)
            self.assertEqual(messages, [{"role": "system", "content": "rules"}])

    def test_active_prefix_stays_exact_until_turn_finishes_and_originals_retrieve(self):
        content = "precise current line\n" * 500
        messages = [{"role": "system", "content": "rules"}]
        responses = iter([{"choices": [{"message": exchange(result=content)[0], "finish_reason": "tool_calls"}]},
                          {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]}])
        def model(history, **kwargs):
            if len(history) > 2:
                self.assertEqual(history[-1]["content"], content)
            return next(responses)
        with patch.object(agent, "log_interaction"), patch.object(agent, "execute_tool", return_value=content), patch.object(agent, "select_tools", return_value=[]), redirect_stdout(io.StringIO()):
            agent.run_agent("read it", messages, api_caller=model, max_iterations=2)
        tool = next(m for m in messages if m["role"] == "tool")
        self.assertIn("Summarized file_read", tool["content"])
        self.assertEqual(agent.local_history_evidence().read(tool["_evidence_id"], limit=8000)["text"], content[:8000])


if __name__ == "__main__":
    unittest.main()
