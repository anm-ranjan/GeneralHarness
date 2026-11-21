import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import harness_agent as agent
import image_budget
import utils

# 1x1 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
INCEPTRON_ERROR = json.dumps({"error": {"message": "Provider returned error", "code": 400, "metadata": {
    "raw": json.dumps({"error": {"code": 400, "message": "At most 1 image(s) may be provided in one prompt. "
                                                         "(parameter=image)", "param": "image"}}),
    "provider_name": "Inceptron"}}})


def image_turn(text, n=1):
    return {"role": "user", "content": [{"type": "text", "text": text}] + [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{text}{i}"}} for i in range(n)]}


def images_in(messages):
    return [part["image_url"]["url"] for m in messages if isinstance(m.get("content"), list)
            for part in m["content"] if part.get("type") == "image_url"]


class Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._body

    def close(self):
        pass


OK = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {}}


class _TempData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, name, value in (
            (utils, "DATA_DIR", self.tmp.name),
            (utils, "BASE_URL", "https://openrouter.ai/api/v1"),
            (utils, "API_PROVIDER", ["inceptron/fp8"]),
            (image_budget, "_limits", None),
            (image_budget, "_catalog_cache", None),
            (utils, "MAX_IMAGES_PER_REQUEST", 8),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_catalog(self, models):
        Path(self.tmp.name, "openrouter_models.json").write_text(json.dumps({"data": [
            {"id": model_id, "architecture": {"input_modalities": modalities}}
            for model_id, modalities in models.items()]}))


class TrimTests(unittest.TestCase):
    def test_nothing_changes_at_or_under_the_limit(self):
        messages = [image_turn("a"), image_turn("b")]
        before = json.dumps(messages)
        self.assertEqual(image_budget.enforce_limit(messages, 2), 0)
        self.assertEqual(json.dumps(messages), before)

    def test_over_the_limit_trims_in_one_batch_to_half_and_keeps_the_newest(self):
        messages = [image_turn(t) for t in "abcde"]
        self.assertEqual(image_budget.enforce_limit(messages, 4), 3)
        self.assertEqual(images_in(messages), ["data:image/png;base64,d0", "data:image/png;base64,e0"])
        self.assertIn(image_budget.OMITTED_NOTE, json.dumps(messages[0]))
        # Room for new images before the history changes again.
        messages.append(image_turn("f"))
        messages.append(image_turn("g"))
        snapshot = json.dumps(messages)
        self.assertEqual(image_budget.enforce_limit(messages, 4), 0)
        self.assertEqual(json.dumps(messages), snapshot)

    def test_a_limit_of_one_keeps_only_the_newest_image(self):
        messages = [image_turn("a"), {"role": "assistant", "content": "seen"}, image_turn("b")]
        self.assertEqual(image_budget.enforce_limit(messages, 1), 1)
        self.assertEqual(images_in(messages), ["data:image/png;base64,b0"])
        self.assertEqual(messages[0]["content"][0], {"type": "text", "text": "a"})

    def test_a_limit_of_zero_replaces_every_image_with_one_note_per_message(self):
        messages = [image_turn("a", n=3)]
        self.assertEqual(image_budget.enforce_limit(messages, 0), 3)
        self.assertEqual(messages[0]["content"], [
            {"type": "text", "text": "a"}, {"type": "text", "text": image_budget.TEXT_ONLY_NOTE}])


class ErrorParsingTests(unittest.TestCase):
    def test_inceptron_limit_is_read_and_remembered(self):
        self.assertEqual(image_budget.parse_image_error(400, INCEPTRON_ERROR, 2), (1, True))

    def test_other_wordings_of_a_count(self):
        for text in ("Too many images: maximum of 4 images per request",
                     "You can send up to 3 images", "image limit is 2"):
            with self.subTest(text=text):
                limit, remember = image_budget.parse_image_error(400, text, 10)
                self.assertTrue(remember)
                self.assertIn(limit, (2, 3, 4))

    def test_no_image_support(self):
        self.assertEqual(image_budget.parse_image_error(
            404 if False else 400, "No endpoints found that support image input", 1), (0, True))
        self.assertEqual(image_budget.parse_image_error(
            400, "This model does not support image inputs", 2), (0, True))

    def test_image_complaint_without_a_number_retries_but_is_not_remembered(self):
        self.assertEqual(image_budget.parse_image_error(400, "image too large", 3), (1, False))
        self.assertEqual(image_budget.parse_image_error(400, "invalid image data", 1), (0, False))

    def test_unrelated_errors_and_requests_without_images_are_ignored(self):
        self.assertIsNone(image_budget.parse_image_error(400, "context length exceeded", 2))
        self.assertIsNone(image_budget.parse_image_error(400, INCEPTRON_ERROR, 0))
        self.assertIsNone(image_budget.parse_image_error(500, INCEPTRON_ERROR, 2))


class LimitTests(_TempData):
    def test_text_only_models_get_no_images(self):
        self.write_catalog({"z-ai/glm-5.3": ["text"], "z-ai/glm-5.3-flash": ["text", "image", "video"]})
        self.assertEqual(image_budget.effective_limit("z-ai/glm-5.3"), 0)
        self.assertEqual(image_budget.effective_limit("z-ai/glm-5.3-flash"), 8)
        self.assertEqual(image_budget.effective_limit("unknown/model"), 8)

    def test_catalogue_is_only_used_for_openrouter(self):
        self.write_catalog({"z-ai/glm-5.3": ["text"]})
        with patch.object(utils, "BASE_URL", "http://localhost:8000/v1"):
            self.assertTrue(image_budget.accepts_images("z-ai/glm-5.3"))

    def test_learned_limits_are_per_route_and_persist(self):
        image_budget.remember_limit("m", 1, "At most 1 image")
        self.assertEqual(image_budget.effective_limit("m"), 1)
        with patch.object(utils, "API_PROVIDER", ["other"]):
            self.assertEqual(image_budget.effective_limit("m"), 8)
        stored = json.loads(Path(self.tmp.name, "native_image_limits.json").read_text())
        self.assertEqual(stored["m|inceptron/fp8"]["max_images"], 1)
        with patch.object(image_budget, "_limits", None):
            self.assertEqual(image_budget.learned_limit("m"), 1)


class CallApiTests(_TempData):
    def call(self, messages, responses, streaming=False):
        with (
            patch.object(utils, "API_STREAMING", streaming),
            patch.object(agent.requests, "post", side_effect=responses) as post,
        ):
            result = agent.call_api(messages, model="z-ai/glm-5.3-flash", ui=None)
        return result, post

    def test_inceptron_rejection_is_retried_with_one_image_and_remembered(self):
        messages = [image_turn("first"), {"role": "assistant", "content": "seen"}, image_turn("second")]
        result, post = self.call(messages, [Response(400, INCEPTRON_ERROR), Response(200, OK)])
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(len(images_in(post.call_args_list[0].kwargs["json"]["messages"])), 2)
        self.assertEqual(images_in(post.call_args_list[1].kwargs["json"]["messages"]),
                         ["data:image/png;base64,second0"])
        # The session's own history keeps the trim, so later requests share it.
        self.assertEqual(images_in(messages), ["data:image/png;base64,second0"])
        self.assertEqual(image_budget.learned_limit("z-ai/glm-5.3-flash"), 1)

        messages += [{"role": "assistant", "content": "ok"}, image_turn("third")]
        _, post = self.call(messages, [Response(200, OK)])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(images_in(post.call_args.kwargs["json"]["messages"]), ["data:image/png;base64,third0"])

    def test_a_second_rejection_is_not_retried_again(self):
        messages = [image_turn("a"), image_turn("b")]
        result, post = self.call(messages, [Response(400, INCEPTRON_ERROR), Response(400, INCEPTRON_ERROR)])
        self.assertIsNone(result)
        self.assertEqual(post.call_count, 2)

    def test_unrelated_errors_do_not_touch_images(self):
        messages = [image_turn("a"), image_turn("b")]
        result, post = self.call(messages, [Response(400, {"error": {"message": "bad request"}})])
        self.assertIsNone(result)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(images_in(messages)), 2)

    def test_streaming_rejection_skips_the_stream_option_retry(self):
        messages = [image_turn("a"), image_turn("b")]
        ui = Mock(spec=["show_assistant_delta", "show_status", "show_error", "show_api_metrics"])
        with (
            patch.object(utils, "API_STREAMING", True),
            patch.object(agent, "_thread_model", return_value=None),
            patch.object(agent.requests, "post", side_effect=[
                Response(400, INCEPTRON_ERROR),       # stream, both images
                Response(500, "upstream busy"),       # retry stream
                Response(200, OK),                    # retry, plain
            ]) as post,
        ):
            result = agent.call_api(messages, model="z-ai/glm-5.3-flash", ui=ui)
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(post.call_count, 3)
        self.assertIn("stream_options", post.call_args_list[0].kwargs["json"])
        self.assertEqual(len(images_in(post.call_args_list[1].kwargs["json"]["messages"])), 1)
        ui.show_status.assert_called()

    def test_text_only_model_is_sent_notes_before_any_request(self):
        self.write_catalog({"z-ai/glm-5.3": ["text"]})
        messages = [image_turn("a")]
        with (
            patch.object(utils, "API_STREAMING", False),
            patch.object(agent.requests, "post", return_value=Response(200, OK)) as post,
        ):
            agent.call_api(messages, model="z-ai/glm-5.3", ui=None)
        self.assertEqual(images_in(post.call_args.kwargs["json"]["messages"]), [])
        self.assertIn(image_budget.TEXT_ONLY_NOTE, json.dumps(post.call_args.kwargs["json"]["messages"]))


class ImageReadTests(unittest.TestCase):
    def test_image_read_attaches_the_image_after_the_tool_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp, "screen1.png")
            image_path.write_bytes(PNG)
            replies = [
                {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "image_read", "arguments": json.dumps({"file_path": str(image_path)})}},
                ]}, "finish_reason": "tool_calls"}]},
                {"choices": [{"message": {"role": "assistant", "content": "I see it."}, "finish_reason": "stop"}]},
            ]
            seen = []

            def api_caller(messages, tools=None, **_kwargs):
                seen.append(json.loads(json.dumps(agent.history_tokens.wire_messages(messages))))
                return replies.pop(0)

            ui = Mock(run_settings={"approval_mode": "auto_approve", "verbose_tools": False})
            messages = [{"role": "system", "content": "rules"}]
            with (
                patch.object(agent, "log_interaction"),
                patch.object(utils, "is_path_allowed", return_value=True),
                patch.object(agent, "execute_tool", side_effect=lambda name, args, **_: utils.tool_image_read(
                    args["file_path"])) as execute,
            ):
                agent.run_agent("look", messages, max_iterations=3, ui=ui, api_caller=api_caller)

        execute.assert_called_once()
        second_request = seen[1]
        tool_msg = next(m for m in second_request if m["role"] == "tool")
        self.assertNotIn(utils.LOCAL_IMAGE_RESULT_MARKER, tool_msg["content"])
        follow = second_request[second_request.index(tool_msg) + 1]
        self.assertEqual(follow["role"], "user")
        self.assertIn("screen1.png", follow["content"][0]["text"])
        self.assertTrue(follow["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
