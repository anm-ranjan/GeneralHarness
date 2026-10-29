"""Local input estimates; never confuse these with provider billed usage.

Counts are cached by value, so edits invalidate them and append-only prefixes
are cheap. cl100k is an approximation for non-OpenAI models and images.
"""
import hashlib
import json

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


def estimate_messages(messages):
    return max(1, sum(count_value(msg) for msg in wire_messages(messages)))


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
            counts[category] += count_value(msg["content"])
        rest = {k: v for k, v in msg.items() if k not in {"content", "tool_calls"}}
        for call in msg.get("tool_calls") or []:
            function = call.get("function") or {}
            counts["tool_arguments"] += count_value(function.get("arguments", ""))
            counts["overhead"] += count_value({**call, "function": {k: v for k, v in function.items() if k != "arguments"}})
        counts["overhead"] += count_value(rest)
    if tools:
        counts["tool_schemas"] = count_value(tools)
    return {**counts, "total": sum(counts.values())}
