import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (str(BACKEND), str(AGENT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import harness_agent as agent
import utils


class OpenRouterProviderPreferenceTests(unittest.TestCase):
    def test_scalar_provider_is_exclusive(self):
        self.assertEqual(
            utils.build_openrouter_provider_preferences(
                "https://openrouter.ai/api/v1", "together"
            ),
            {"order": ["together"], "allow_fallbacks": False},
        )

    def test_list_preserves_priority_and_removes_blanks_and_duplicates(self):
        self.assertEqual(
            utils.build_openrouter_provider_preferences(
                "https://openrouter.ai/api/v1/",
                [" together ", "deepinfra", "", "together", "fireworks"],
            ),
            {
                "order": ["together", "deepinfra", "fireworks"],
                "allow_fallbacks": False,
            },
        )

    def test_openrouter_hostname_is_matched_without_matching_lookalikes(self):
        expected = {"order": ["together"], "allow_fallbacks": False}
        self.assertEqual(
            utils.build_openrouter_provider_preferences(
                "https://API.OPENROUTER.AI:443/api/v1", ["together"]
            ),
            expected,
        )
        self.assertIsNone(
            utils.build_openrouter_provider_preferences(
                "https://openrouter.ai.example.com/v1", ["together"]
            )
        )

    def test_non_openrouter_and_empty_values_are_ignored(self):
        self.assertIsNone(
            utils.build_openrouter_provider_preferences(
                "https://example.com/v1", ["together"]
            )
        )
        self.assertIsNone(
            utils.build_openrouter_provider_preferences(
                "https://openrouter.ai/api/v1", []
            )
        )
        self.assertIsNone(
            utils.build_openrouter_provider_preferences(
                "https://openrouter.ai/api/v1", {"order": ["together"]}
            )
        )

    def test_absent_provider_keeps_existing_request_behavior(self):
        with patch.object(utils, "API_PROVIDER", None):
            self.assertIsNone(
                utils.build_openrouter_provider_preferences(
                    "https://openrouter.ai/api/v1"
                )
            )

    def test_native_call_includes_preferences_in_request_payload(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {},
                }

        with (
            patch.object(agent.utils, "BASE_URL", "https://openrouter.ai/api/v1"),
            patch.object(agent.utils, "API_PROVIDER", ["together", "deepinfra"]),
            patch.object(agent.utils, "API_STREAMING", False),
            patch.object(agent.requests, "post", return_value=Response()) as post,
        ):
            result = agent.call_api(
                [{"role": "user", "content": "Hello"}],
                model="meta-llama/llama-3.3-70b-instruct",
            )

        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(
            post.call_args.kwargs["json"]["provider"],
            {
                "order": ["together", "deepinfra"],
                "allow_fallbacks": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
