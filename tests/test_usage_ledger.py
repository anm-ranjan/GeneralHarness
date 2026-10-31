import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import HTTPException

from backend import web_app
import claude_agent_provider
import codex_app_server_provider
import native_settings
import openrouter_catalog
import prompt_cache
import usage_ledger
import utils
import web_routes.usage as usage_routes
from session_store import SessionStore
from web_models import EventEnvelope, EventType


class CodexUsageTests(unittest.TestCase):
    def test_last_call_is_recorded_with_thread_model(self):
        params = {
            "threadId": "t1",
            "tokenUsage": {
                "total": {"inputTokens": 99999, "outputTokens": 9999},
                "last": {"totalTokens": 17356, "inputTokens": 17351, "cachedInputTokens": 13184,
                         "cacheWriteInputTokens": 0, "outputTokens": 5, "reasoningOutputTokens": 2},
            },
        }
        metrics = codex_app_server_provider.codex_usage_metrics(params, {"t1": "gpt-6.1-sol"})
        self.assertEqual(metrics["model"], "gpt-6.1-sol")
        self.assertEqual(metrics["input_tokens"], 17351)
        self.assertEqual(metrics["cache_read_tokens"], 13184)
        self.assertEqual(metrics["output_tokens"], 5)
        self.assertEqual(metrics["reasoning_tokens"], 2)

    def test_empty_usage_is_ignored(self):
        self.assertEqual(codex_app_server_provider.codex_usage_metrics({"tokenUsage": {}}), {})

    def test_sparse_rate_limit_update_keeps_known_values(self):
        transport = codex_app_server_provider.AppServerTransport()
        transport._remember_account_notification({"method": "account/rateLimits/updated", "params": {
            "rateLimits": {"planType": "plus", "primary": {"usedPercent": 10}}}})
        transport._remember_account_notification({"method": "account/rateLimits/updated", "params": {
            "rateLimits": {"planType": None, "primary": {"usedPercent": 12}}}})
        limits = transport.account_notifications["account/rateLimits/updated"]["rateLimits"]
        self.assertEqual(limits["planType"], "plus")
        self.assertEqual(limits["primary"]["usedPercent"], 12)

    def test_codex_cost_uses_cached_price_for_cached_input(self):
        price = {"prompt": 1e-6, "completion": 5e-6, "input_cache_read": 1e-7}
        cost = openrouter_catalog.price_tokens(price, 1000, 800, 0, 100)
        self.assertAlmostEqual(cost, 200 * 1e-6 + 800 * 1e-7 + 100 * 5e-6)


class ClaudeUsageTests(unittest.TestCase):
    def test_model_usage_becomes_one_record_per_model(self):
        result = SimpleNamespace(model_usage={
            "claude-haiku-4-5": {"inputTokens": 533, "outputTokens": 65, "cacheReadInputTokens": 10,
                                 "cacheCreationInputTokens": 13119, "costUSD": 0.027},
        }, usage={"input_tokens": 10}, total_cost_usd=0.027)
        records = claude_agent_provider.claude_usage_metrics(result)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["model"], "claude-haiku-4-5")
        self.assertEqual(records[0]["input_tokens"], 533 + 10 + 13119)
        self.assertEqual(records[0]["cost_usd"], 0.027)

    def test_subscription_and_api_key_are_told_apart(self):
        self.assertEqual(usage_ledger.claude_auth(
            {"api_key_source": "none", "account": {"subscriptionType": "Claude Pro", "apiProvider": "firstParty"}}),
            "subscription")
        self.assertEqual(usage_ledger.claude_auth({"api_key_source": "ANTHROPIC_API_KEY", "account": {}}), "api")
        self.assertEqual(usage_ledger.claude_auth({"api_key_source": "none", "account": {"apiProvider": "bedrock"}}), "api")

    def test_identity_fields_are_not_kept(self):
        account = claude_agent_provider._public_account(
            {"account": {"email": "a@b.c", "organization": "Org", "subscriptionType": "Claude Pro"}})
        self.assertEqual(account, {"subscriptionType": "Claude Pro"})


class LedgerTests(unittest.TestCase):
    def test_records_sum_by_period_and_survive_thread_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(str(Path(tmp) / "data"))
            store.ensure_project("proj", "Project", tmp)
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "S", "native")
            for _ in range(2):
                store.record_usage(meta.id, {"provider": "native", "model": "qwen/qwen3-coder-next",
                                             "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.01}, auth="api")
            store.delete_session("proj", "task", meta.id)
            with patch.object(openrouter_catalog, "prices", return_value={}):
                summary = usage_ledger.summary(store)
            today = summary["periods"][0]
            self.assertEqual(today["id"], "today")
            row = today["rows"][0]
            self.assertEqual((row["requests"], row["input_tokens"], row["cost_kind"]), (2, 200, "reported"))
            self.assertAlmostEqual(row["cost_usd"], 0.02)
            store.close()

    def test_codex_subscription_rows_are_priced_as_equivalent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(str(Path(tmp) / "data"))
            store.record_usage("s", {"provider": "codex-app-server", "model": "gpt-6.1-sol",
                                     "input_tokens": 1000, "output_tokens": 100}, auth="subscription")
            with patch.object(openrouter_catalog, "prices",
                              return_value={"openai/gpt-6.1-sol": {"prompt": 1e-6, "completion": 1e-5}}):
                row = usage_ledger.summary(store)["periods"][-1]["rows"][0]
            self.assertEqual(row["cost_kind"], "equivalent")
            self.assertAlmostEqual(row["cost_usd"], 1000 * 1e-6 + 100 * 1e-5)
            store.close()

    def test_existing_native_metrics_are_backfilled_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            store = SessionStore(str(data))
            store.ensure_project("proj", "Project", tmp)
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "S", "native")
            store.append_event(EventEnvelope(session_id=meta.id, type=EventType.API_METRICS,
                                             data={"input_tokens": 50, "output_tokens": 5, "cost_usd": 0.001}))
            with store._lock, store._conn:
                store._conn.execute("DROP TABLE usage_records")
            store.close()
            reopened = SessionStore(str(data))
            rows = reopened.usage_totals()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["input_tokens"], 50)
            reopened.close()
            again = SessionStore(str(data))
            self.assertEqual(again.usage_totals()[0]["requests"], 1)
            again.close()

    def test_reasoning_tokens_are_normalized(self):
        usage = {"prompt_tokens": 10, "completion_tokens": 20, "completion_tokens_details": {"reasoning_tokens": 7}}
        self.assertEqual(prompt_cache.normalize_usage(usage)["reasoning_tokens"], 7)


class NativeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {name: getattr(utils, name) for name in ("DATA_DIR", "MODEL", "READ_MODEL", "WRITE_MODEL",
                                                               "SUMMARY_MODEL", "API_PROVIDER", "BASE_URL")}
        utils.DATA_DIR = self.tmp.name
        utils.BASE_URL = "https://openrouter.ai/api/v1"

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(utils, name, value)
        self.tmp.cleanup()

    def _catalog(self, model):
        if model != "openai/gpt-6.1-terra":
            raise openrouter_catalog.CatalogError(f"OpenRouter has no model '{model}'")
        return {"id": model, "name": "GPT Terra", "endpoints": [
            {"tag": "azure/eu", "tools": True}, {"tag": "openai/flex", "tools": True}, {"tag": "toolless", "tools": False}]}

    def test_save_applies_everywhere_and_broadcasts(self):
        with (
            patch.object(openrouter_catalog, "model_endpoints", side_effect=self._catalog),
            patch.object(web_app._manager, "notify_global") as notify,
        ):
            payload = usage_routes.update_native_settings(
                usage_routes.NativeSettingsRequest(model="openai/gpt-6.1-terra", providers="azure/eu"))
        self.assertEqual(payload["model"], "openai/gpt-6.1-terra")
        self.assertEqual(payload["providers"], ["azure/eu"])
        self.assertEqual((utils.READ_MODEL, utils.WRITE_MODEL, utils.SUMMARY_MODEL), ("openai/gpt-6.1-terra",) * 3)
        self.assertEqual(utils.build_openrouter_provider_preferences()["order"], ["azure/eu"])
        notify.assert_called_once()
        self.assertEqual(native_settings.load()["model"], "openai/gpt-6.1-terra")

    def test_unknown_provider_and_toolless_provider_are_rejected(self):
        with patch.object(openrouter_catalog, "model_endpoints", side_effect=self._catalog):
            for providers in (["azure/us"], ["toolless"]):
                with self.assertRaises(HTTPException) as ctx:
                    usage_routes.update_native_settings(
                        usage_routes.NativeSettingsRequest(model="openai/gpt-6.1-terra", providers=providers))
                self.assertEqual(ctx.exception.status_code, 400)
            with self.assertRaises(HTTPException):
                usage_routes.update_native_settings(usage_routes.NativeSettingsRequest(model="nobody/nothing"))

    def test_clear_restores_configuration(self):
        native_settings.save("deepseek/deepseek-v4", [])
        self.assertEqual(utils.MODEL, "deepseek/deepseek-v4")
        native_settings.clear()
        self.assertEqual(utils.MODEL, native_settings._CONFIG_DEFAULTS["MODEL"])


if __name__ == "__main__":
    unittest.main()
