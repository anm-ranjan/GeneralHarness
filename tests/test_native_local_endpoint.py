"""Native Settings: a local OpenAI-compatible server (llama-server) as the endpoint."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import requests  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from backend import web_app  # noqa: E402
import native_settings  # noqa: E402
import openrouter_catalog  # noqa: E402
import utils  # noqa: E402
import web_routes.usage as usage_routes  # noqa: E402

LOCAL = "http://127.0.0.1:8081/v1"
GGUF = "/models/Inkling-2-Mini-UD-Q3_K_XL.gguf"
# The shape llama-server returns from /v1/models.
MODELS = {"object": "list", "data": [
    {"id": GGUF, "aliases": [GGUF, "inkling-2-mini"], "object": "model", "meta": {"n_ctx": 100096}}]}


class _Response:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._data


class LocalEndpointTests(unittest.TestCase):
    NAMES = ("DATA_DIR", "MODEL", "READ_MODEL", "WRITE_MODEL", "SUMMARY_MODEL", "API_PROVIDER", "BASE_URL",
             "CONTEXT_LIMIT_TOKENS", "COMPACT_THRESHOLD_TOKENS", "NATIVE_LOCAL_ENDPOINT")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # apply_saved also applies the agent-loop fields, so restore those too.
        agent_attrs = [attr for attr, *_ in native_settings.AGENT_FIELDS.values()]
        self.saved = {name: getattr(utils, name) for name in [*self.NAMES, *agent_attrs]}
        self.saved_state = dict(native_settings._endpoint_state)
        self.saved_defaults = dict(native_settings._CONFIG_DEFAULTS)
        utils.DATA_DIR = self.tmp.name
        utils.BASE_URL = native_settings._CONFIG_DEFAULTS["BASE_URL"] = "https://openrouter.ai/api/v1"
        self.gets = []
        get = patch.object(native_settings.requests, "get", side_effect=self._get)
        get.start()
        self.addCleanup(get.stop)
        notify = patch.object(web_app._manager, "notify_global")
        notify.start()
        self.addCleanup(notify.stop)

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(utils, name, value)
        native_settings._endpoint_state.update(self.saved_state)
        native_settings._CONFIG_DEFAULTS.update(self.saved_defaults)
        self.tmp.cleanup()

    def _get(self, url, timeout=None):
        self.gets.append(url)
        if url == f"{LOCAL}/models":
            return _Response(MODELS)
        raise requests.ConnectionError("refused")

    def _save(self, model=GGUF, base_url=LOCAL):
        return usage_routes.update_native_settings(usage_routes.NativeSettingsRequest(
            model=model, endpoint="local", base_url=base_url))

    def test_local_save_points_native_at_the_server_without_a_key(self):
        with patch.object(openrouter_catalog, "model_endpoints") as catalog:
            payload = self._save(base_url=LOCAL + "/chat/completions")
        catalog.assert_not_called()  # OpenRouter is never asked about a local model
        self.assertEqual(utils.BASE_URL, LOCAL)
        self.assertEqual((utils.MODEL, utils.READ_MODEL, utils.WRITE_MODEL, utils.SUMMARY_MODEL), (GGUF,) * 4)
        self.assertEqual((payload["endpoint"], payload["base_url"], payload["openrouter"]), ("local", LOCAL, False))
        self.assertTrue(utils.native_enabled())
        self.assertNotIn("Authorization", utils.api_headers())
        self.assertIsNone(utils.build_openrouter_provider_preferences(configured_provider=["azure/eu"]))
        stored = json.loads((Path(self.tmp.name) / "native_settings.json").read_text())
        self.assertEqual((stored["endpoint"], stored["base_url"]), ("local", LOCAL))

    def test_context_window_comes_from_the_server_n_ctx(self):
        payload = self._save(model="inkling-2-mini")  # an alias works too
        self.assertEqual(utils.CONTEXT_LIMIT_TOKENS, 100096)
        self.assertEqual(utils.COMPACT_THRESHOLD_TOKENS, int(100096 * 0.75))
        self.assertEqual(payload["context_source"], "local")

    def test_unknown_model_bad_url_and_unreachable_server_are_rejected(self):
        for model, base_url, needle in ((
                "other", LOCAL, f"It serves: {GGUF}"),
                (GGUF, "127.0.0.1:8081", "base URL"),
                (GGUF, "http://127.0.0.1:9999/v1", "No OpenAI-compatible server")):
            with self.assertRaises(HTTPException) as ctx:
                self._save(model=model, base_url=base_url)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn(needle, ctx.exception.detail)
        self.assertEqual(utils.BASE_URL, "https://openrouter.ai/api/v1")
        self.assertFalse((Path(self.tmp.name) / "native_settings.json").exists())

    def test_reset_and_config_save_restore_the_configured_endpoint_and_key(self):
        self._save()
        usage_routes.reset_native_settings()
        self.assertEqual(utils.BASE_URL, "https://openrouter.ai/api/v1")
        self.assertFalse(utils.NATIVE_LOCAL_ENDPOINT)
        self.assertIn("Authorization", utils.api_headers())
        self.assertEqual(native_settings.current()["endpoint"], "config")
        self._save()

        def catalog(model):
            return {"id": model, "name": "M", "endpoints": [{"tag": "a", "tools": True, "context_length": 1000}]}
        with patch.object(openrouter_catalog, "model_endpoints", side_effect=catalog) as asked:
            usage_routes.update_native_settings(usage_routes.NativeSettingsRequest(model="openai/gpt-6.1-terra"))
        asked.assert_called()  # the configured endpoint is OpenRouter, so the model is checked there
        self.assertEqual(utils.BASE_URL, "https://openrouter.ai/api/v1")
        self.assertNotIn("endpoint", json.loads((Path(self.tmp.name) / "native_settings.json").read_text()))

    def test_saved_local_endpoint_is_applied_again_at_startup(self):
        self._save()
        utils.BASE_URL = "https://openrouter.ai/api/v1"
        native_settings._endpoint_state["local"] = False
        utils.NATIVE_LOCAL_ENDPOINT = False
        native_settings.apply_saved()
        self.assertEqual((utils.BASE_URL, utils.MODEL, utils.NATIVE_LOCAL_ENDPOINT), (LOCAL, GGUF, True))
        # A hand-edited bad URL falls back to the configuration instead of failing.
        path = Path(self.tmp.name) / "native_settings.json"
        path.write_text(json.dumps({"model": GGUF, "endpoint": "local", "base_url": "nonsense"}))
        native_settings.apply_saved()
        self.assertEqual(utils.BASE_URL, "https://openrouter.ai/api/v1")
        self.assertFalse(utils.NATIVE_LOCAL_ENDPOINT)


if __name__ == "__main__":
    unittest.main()
