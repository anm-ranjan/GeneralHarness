"""Native prompt caching: request-only hints and provider usage normalization.

This never caches tool execution. Unknown OpenAI-compatible endpoints receive no
extra fields. History compression, model/tool selection and permissions remain
the caller's responsibility; a cache miss is always a valid outcome.
"""

from copy import deepcopy
import math
import threading
from urllib.parse import urlparse


def endpoint_kind(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower().rstrip(".")
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        return "openrouter"
    if host == "api.x.ai":
        return "xai"
    if host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "dashscope-us.aliyuncs.com"}:
        return "alibaba"
    return "other"


# Explicit caching support is endpoint/model specific. Extend via configuration
# for new supported releases rather than sending cache fields to every Qwen host.
_QWEN_EXPLICIT = {
    "qwen3-max", "qwen-plus", "qwen3.6-plus", "qwen3-coder-plus", "qwen3-coder-flash",
}


def _mark_text(message: dict, ttl: str) -> bool:
    content = message.get("content")
    if isinstance(content, str) and content:
        content = [{"type": "text", "text": content}]
        message["content"] = content
    if isinstance(content, list):
        for block in reversed(content):
            if block.get("type") == "text" and block.get("text"):
                block["cache_control"] = {"type": "ephemeral"}
                if ttl == "1h":
                    block["cache_control"]["ttl"] = ttl
                return True
    return False


def prepare_request(payload: dict, base_url: str, settings=None, session_key=None) -> tuple[dict, dict]:
    """Return isolated payload/headers. No mutation of canonical history/tools.

    mode=auto enables explicit controls only for known supported routes;
    explicit_models opts additional exact IDs into content-block breakpoints.
    off suppresses our hints, not provider-managed implicit caching.
    """
    request = deepcopy(payload)
    settings = settings if isinstance(settings, dict) else {}
    mode = settings.get("mode", "auto")
    if mode not in {"auto", "off"}:
        raise ValueError("native_caching.mode must be auto or off")
    ttl = settings.get("ttl", "5m")
    if ttl not in {"5m", "1h"}:
        raise ValueError("native_caching.ttl must be 5m or 1h")
    headers = {}
    kind = endpoint_kind(base_url)
    if mode == "off":
        return request, headers
    if session_key:
        if kind == "openrouter":
            request["session_id"] = str(session_key)[:256]
        elif kind == "xai":
            headers["x-grok-conv-id"] = str(session_key)[:256]

    model = str(payload.get("model", "")).split(":", 1)[0]
    explicit_models = settings.get("explicit_models") or []
    if not isinstance(explicit_models, list) or not all(isinstance(m, str) for m in explicit_models):
        raise ValueError("native_caching.explicit_models must be a list of model IDs")
    # OpenRouter translates this Anthropic-specific field to the selected host.
    if kind == "openrouter" and model.startswith("anthropic/"):
        request["cache_control"] = {"type": "ephemeral"}
        if ttl == "1h":
            request["cache_control"]["ttl"] = ttl
    else:
        # Qwen on unrelated hosts must not receive Alibaba-specific markers.
        provider = (request.get("provider") or {}).get("order") or []
        alibaba_route = kind == "alibaba" or (
            kind == "openrouter" and bool(provider)
            and all(str(p).lower().split("/", 1)[0] == "alibaba" for p in provider)
        )
        qwen_name = model.removeprefix("qwen/")
        explicit = model in explicit_models or (alibaba_route and qwen_name in _QWEN_EXPLICIT)
        if explicit and kind in {"openrouter", "alibaba"}:
            # Alibaba supports 5m, not Anthropic's configurable 1h lifetime.
            messages = request.get("messages", [])
            system = next((m for m in messages if m.get("role") == "system"), None)
            if system:
                _mark_text(system, "5m")
            for message in reversed(messages):
                if message is not system and _mark_text(message, "5m"):
                    break
    return request, headers


def normalize_usage(usage) -> dict:
    """Keep absent data absent. Cache misses are NOT cache writes.

    OpenRouter/OpenAI token totals include cached tokens. Anthropic-style raw
    input_tokens exclude cache reads/writes, so add them only for that shape.
    """
    if not isinstance(usage, dict):
        return {}
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    if not isinstance(details, dict):
        details = {}

    def number(*values):
        for value in values:
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                return value
        return None

    read = number(details.get("cached_tokens"), usage.get("prompt_cache_hit_tokens"), usage.get("cache_read_input_tokens"))
    write = number(details.get("cache_write_tokens"), details.get("cache_creation_input_tokens"), usage.get("cache_creation_input_tokens"))
    prompt = number(usage.get("prompt_tokens"))
    if prompt is None:
        prompt = number(usage.get("input_tokens"))
        if prompt is not None and ("cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage):
            prompt += (read or 0) + (write or 0)
    result = {}
    for key, value in {
        "input_tokens": prompt,
        "output_tokens": number(usage.get("completion_tokens"), usage.get("output_tokens")),
        "cache_read_tokens": read,
        "cache_write_tokens": write,
        "cost_usd": number(usage.get("cost")),
    }.items():
        if value is not None:
            result[key] = value
    return result


class UsageTotals:
    """Per-run, thread-safe totals with explicit reporting coverage."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests = 0
        self._totals = {}
        self._reported = {}
        self._estimated_input = {}

    def add(self, metrics):
        with self._lock:
            self._requests += 1
            estimates = metrics.get("estimated_input")
            if isinstance(estimates, dict):
                self._reported["estimated_input"] = self._reported.get("estimated_input", 0) + 1
            for key, value in (estimates if isinstance(estimates, dict) else {}).items():
                if key in {"instructions", "user_text", "tool_schemas", "tool_arguments", "tool_results",
                           "assistant_text", "overhead", "total"} and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    self._estimated_input[key] = self._estimated_input.get(key, 0) + value
            for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd"):
                value = metrics.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                    self._totals[key] = self._totals.get(key, 0) + value
                    self._reported[key] = self._reported.get(key, 0) + 1

    def snapshot(self):
        with self._lock:
            result = {"requests": self._requests, **self._totals, "reported_requests": dict(self._reported)}
            if self._estimated_input:
                result["estimated_input"] = dict(self._estimated_input)
            return result
