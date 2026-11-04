"""Local input estimates; never confuse these with provider billed usage.

Counts are cached by value, so edits invalidate them and append-only prefixes
are cheap. cl100k is an approximation for non-OpenAI models.

Two parts of a stored message are not counted as text:
- inline images (base64 data URLs) are counted from their pixel size, the way
  vision models bill them, instead of as hundreds of thousands of base64 tokens;
- reasoning blocks (``reasoning_details`` and friends) returned by the model are
  kept for providers that want them echoed back, but measured provider usage
  shows they are not billed as input text, so they are excluded.

``context_tokens`` prefers the gateway's own ``prompt_tokens`` from the latest
response (the "usage anchor") plus a local estimate of what changed since.
"""
import base64
import binascii
import hashlib
import json
import math
import struct

try:
    import tiktoken
except ImportError:
    tiktoken = None

_encoding = None
_loaded = False
TOKEN_COUNT_CACHE = {}
TOKEN_COUNT_CACHE_MAX = 8192


def count_text(text):
    global _encoding, _loaded
    if not _loaded:
        _loaded = True
        try:
            _encoding = tiktoken.get_encoding("cl100k_base") if tiktoken else None
        except Exception:
            _encoding = None
    if _encoding is not None:
        try:
            return len(_encoding.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, (len(text) + 3) // 4)


def count_value(value):
    blob = json.dumps(value, ensure_ascii=False, default=str)
    key = hashlib.blake2b(blob.encode("utf-8", errors="replace"), digest_size=16).digest()
    count = TOKEN_COUNT_CACHE.get(key)
    if count is None:
        count = count_text(blob)
        if len(TOKEN_COUNT_CACHE) >= TOKEN_COUNT_CACHE_MAX:
            TOKEN_COUNT_CACHE.clear()
        TOKEN_COUNT_CACHE[key] = count
    return count


def wire_messages(messages):
    """Local recovery/provenance fields are never provider API parameters."""
    return [{k: v for k, v in msg.items() if k != "steering" and not k.startswith("_")}
            for msg in messages]


REASONING_KEYS = ("reasoning_details", "reasoning", "reasoning_content")
# Fallback when an image's size cannot be read (remote URL, unknown format).
DEFAULT_IMAGE_TOKENS = 1600
_IMAGE_MAX_EDGE = 1568
_IMAGE_PIXELS_PER_TOKEN = 750
_IMAGE_HEADER_CHARS = 262144  # base64 chars decoded to find JPEG frame headers
IMAGE_TOKEN_CACHE = {}
USAGE_ANCHOR_KEY = "_usage_anchor"
_last_tool_tokens = 0


def image_dimensions(data: bytes):
    """Return (width, height) from PNG/GIF/JPEG/WebP header bytes, else None."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            chunk = data[12:16]
            if chunk == b"VP8X":
                return (int.from_bytes(data[24:27], "little") + 1,
                        int.from_bytes(data[27:30], "little") + 1)
            if chunk == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def image_tokens_for_size(width: int, height: int) -> int:
    """Vision-model style estimate: fit the long edge, then pixels / 750."""
    if width <= 0 or height <= 0:
        return DEFAULT_IMAGE_TOKENS
    scale = min(1.0, _IMAGE_MAX_EDGE / max(width, height))
    pixels = (width * scale) * (height * scale)
    return max(85, min(DEFAULT_IMAGE_TOKENS, math.ceil(pixels / _IMAGE_PIXELS_PER_TOKEN)))


def image_tokens(url) -> int:
    if not isinstance(url, str) or not url.startswith("data:") or "," not in url:
        return DEFAULT_IMAGE_TOKENS
    key = hashlib.blake2b(url.encode("utf-8", errors="replace"), digest_size=16).digest()
    cached = IMAGE_TOKEN_CACHE.get(key)
    if cached is not None:
        return cached
    payload = url.split(",", 1)[1][:_IMAGE_HEADER_CHARS]
    payload = payload[:len(payload) - len(payload) % 4]
    try:
        size = image_dimensions(base64.b64decode(payload, validate=False))
    except (binascii.Error, ValueError):
        size = None
    tokens = image_tokens_for_size(*size) if size else DEFAULT_IMAGE_TOKENS
    if len(IMAGE_TOKEN_CACHE) >= TOKEN_COUNT_CACHE_MAX:
        IMAGE_TOKEN_CACHE.clear()
    IMAGE_TOKEN_CACHE[key] = tokens
    return tokens


def _content_without_images(content):
    """Return (content with image data removed, image token total)."""
    if not isinstance(content, list):
        return content, 0
    parts, extra = [], 0
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url") if isinstance(part.get("image_url"), dict) else None
            extra += image_tokens(url)
            parts.append({**part, "image_url": {"url": ""}})
        else:
            parts.append(part)
    return parts, extra


def count_content(content) -> int:
    stripped, extra = _content_without_images(content)
    return count_value(stripped) + extra


def count_message(message) -> int:
    if not isinstance(message, dict):
        return count_value(message)
    stripped = {k: v for k, v in message.items() if k not in REASONING_KEYS}
    stripped["content"], extra = _content_without_images(message.get("content"))
    if message.get("content") is None and "content" not in message:
        stripped.pop("content")
    return count_value(stripped) + extra


def estimate_messages(messages):
    return max(1, sum(count_message(msg) for msg in wire_messages(messages)))


def count_tools(tools) -> int:
    global _last_tool_tokens
    if not tools:
        return 0
    _last_tool_tokens = count_value(tools)
    return _last_tool_tokens


def _latest_anchor(messages):
    for message in reversed(messages or []):
        if isinstance(message, dict) and isinstance(message.get(USAGE_ANCHOR_KEY), dict):
            return message[USAGE_ANCHOR_KEY]
    return None


def record_provider_usage(messages, prompt_tokens, tools=None) -> None:
    """Remember the gateway's input count for exactly the messages just sent.

    The anchor is stored (as a private key that never reaches the wire) on the
    last message sent, so later appends, in-place compression and system prompt
    rebuilds are measured as local deltas against the provider's figure.
    """
    try:
        prompt_tokens = int(prompt_tokens or 0)
    except (TypeError, ValueError):
        return
    if prompt_tokens <= 0 or not messages or not isinstance(messages[-1], dict):
        return
    for message in messages:
        if isinstance(message, dict):
            message.pop(USAGE_ANCHOR_KEY, None)
    messages[-1][USAGE_ANCHOR_KEY] = {
        "provider": prompt_tokens,
        "local": estimate_messages(messages),
        "tools": count_tools(tools),
    }


def context_tokens(messages, tools=None) -> int:
    """Best estimate of the input the next request will carry.

    With tools=None the tool-definition size from the latest request is reused,
    because the end-of-turn check and the context display have no tool list.
    """
    local = estimate_messages(messages)
    anchor = _latest_anchor(messages)
    tool_tokens = count_tools(tools) if tools else None
    if anchor:
        try:
            base = int(anchor["provider"]) + local - int(anchor["local"])
            if tool_tokens is not None:
                base += tool_tokens - int(anchor.get("tools") or 0)
            return max(1, base)
        except (KeyError, TypeError, ValueError):
            pass
    return local + (tool_tokens if tool_tokens is not None else _last_tool_tokens)


def input_categories(messages, tools=None):
    """Disjoint estimates, including schemas and protocol/reasoning overhead.

    Field serialization differs slightly from whole-message serialization;
    total is the sum of these estimates, not a provider tokenizer prediction.
    No text, credentials, arguments or paths are returned in this telemetry.
    """
    counts = dict.fromkeys(("instructions", "user_text", "tool_schemas", "tool_arguments",
                           "tool_results", "assistant_text", "overhead"), 0)
    skill_ids = set()
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            function = call.get("function") or {}
            if function.get("name") == "skill_read":
                try:
                    raw = function.get("arguments", {})
                    args = json.loads(raw) if isinstance(raw, str) else raw
                    if args.get("resource", "SKILL.md") == "SKILL.md":
                        skill_ids.add(call.get("id"))
                except (ValueError, TypeError, AttributeError):
                    pass
    for msg in wire_messages(messages):
        role = msg.get("role")
        category = {"system": "instructions", "developer": "instructions", "user": "user_text",
                    "tool": "tool_results", "assistant": "assistant_text"}.get(role, "overhead")
        if role == "tool" and msg.get("tool_call_id") in skill_ids and not str(msg.get("content", "")).startswith("ERROR"):
            category = "instructions"
        if msg.get("content") is not None:
            counts[category] += count_content(msg["content"])
        rest = {k: v for k, v in msg.items() if k not in {"content", "tool_calls", *REASONING_KEYS}}
        for call in msg.get("tool_calls") or []:
            function = call.get("function") or {}
            counts["tool_arguments"] += count_value(function.get("arguments", ""))
            counts["overhead"] += count_value({**call, "function": {k: v for k, v in function.items() if k != "arguments"}})
        counts["overhead"] += count_value(rest)
    if tools:
        counts["tool_schemas"] = count_value(tools)
    return {**counts, "total": sum(counts.values())}
