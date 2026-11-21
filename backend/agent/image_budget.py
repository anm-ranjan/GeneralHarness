"""Keep the images a native request carries within what the model accepts.

Image attachments stay in the conversation as ``image_url`` parts, so every
request re-sends all earlier images. Providers cap that (Inceptron: one image
per prompt) and text-only models accept none, so before each request:

- a model whose catalogue entry has no image input gets text notes instead;
- otherwise at most ``limit`` images are sent, where ``limit`` is the lower of
  the configured cap and any limit a provider has reported for this model and
  route (learned from its error and remembered under the data directory).

Prompt caching matches on prefixes, so replacing an earlier image costs a
cache miss from that message onward. The history is therefore changed only
when the limit would be exceeded, and then in one batch down to half the limit
(at least one), leaving room for several new images before the next change.
Replacements are written into the session's own message list, so later
requests share the new prefix.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

import utils

OMITTED_NOTE = ("[Earlier image removed to stay within the model's image limit. "
                "Ask the user to send it again if you need to see it.]")
TEXT_ONLY_NOTE = ("[Image not sent: the current model does not accept image input. "
                  "Tell the user, and ask them to describe it or switch to a model that reads images.]")

_lock = threading.Lock()
_limits: dict | None = None
_catalog_cache: tuple[float, dict] | None = None

_IMAGE_WORD = re.compile(r"\bimages?\b|\bvision\b|image_url|image input", re.I)
_COUNT_PATTERNS = (
    re.compile(r"at most\s+(\d+)\s+images?", re.I),
    re.compile(r"(?:maximum|max)(?: number)?(?: of)?\s+(\d+)\s+images?", re.I),
    re.compile(r"up to\s+(\d+)\s+images?", re.I),
    re.compile(r"(?:only|no more than)\s+(\d+)\s+images?", re.I),
    re.compile(r"images?[^.\d]{0,40}(?:limit|maximum|max)(?: is| of)?\s*[:=]?\s*(\d+)", re.I),
)
_NO_IMAGE_PATTERNS = (
    re.compile(r"(?:does not|doesn't|do not|don't|cannot|can't)\s+support\s+(?:image|vision)", re.I),
    re.compile(r"no endpoints? found that supports? image", re.I),
    re.compile(r"image input is not supported|images? (?:are|is) not supported", re.I),
)


# ── image parts ──────────────────────────────────────────────────

def _is_image_part(part) -> bool:
    return isinstance(part, dict) and part.get("type") == "image_url"


def image_positions(messages: list) -> list[tuple[int, int]]:
    """(message index, part index) of every image, oldest first."""
    positions = []
    for mi, message in enumerate(messages):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            positions.extend((mi, pi) for pi, part in enumerate(content) if _is_image_part(part))
    return positions


def count_images(messages: list) -> int:
    return len(image_positions(messages))


def keep_after_trim(limit: int) -> int:
    """How many of the newest images survive a trim: half the limit, at least one."""
    return 0 if limit <= 0 else max(1, limit // 2)


def replace_images(messages: list, positions, note: str) -> int:
    """Swap the given image parts for a text note, in place. Returns how many."""
    by_message: dict[int, set[int]] = {}
    for mi, pi in positions:
        by_message.setdefault(mi, set()).add(pi)
    replaced = 0
    for mi, part_indexes in by_message.items():
        content = messages[mi]["content"]
        new_content = []
        for pi, part in enumerate(content):
            if pi in part_indexes and _is_image_part(part):
                replaced += 1
                # One note per message is enough, and keeps the text stable.
                if not new_content or new_content[-1] != {"type": "text", "text": note}:
                    new_content.append({"type": "text", "text": note})
            else:
                new_content.append(part)
        messages[mi]["content"] = new_content
    return replaced


def enforce_limit(messages: list, limit: int, note: str | None = None) -> int:
    """Trim older images when more than ``limit`` would be sent. Returns how many were removed."""
    positions = image_positions(messages)
    if len(positions) <= max(0, limit):
        return 0
    if limit <= 0:
        return replace_images(messages, positions, note or TEXT_ONLY_NOTE)
    keep = keep_after_trim(limit)
    return replace_images(messages, positions[:-keep], note or OMITTED_NOTE)


# ── model and provider limits ────────────────────────────────────

def _is_openrouter() -> bool:
    return "openrouter.ai" in str(utils.BASE_URL or "")


def _catalog() -> dict:
    """OpenRouter model id -> input modalities, from the cached model list."""
    global _catalog_cache
    path = os.path.join(utils.DATA_DIR, "openrouter_models.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    with _lock:
        if _catalog_cache and _catalog_cache[0] == mtime:
            return _catalog_cache[1]
    table = {}
    try:
        with open(path, encoding="utf-8") as handle:
            models = json.load(handle).get("data")
        for model in models if isinstance(models, list) else []:
            if isinstance(model, dict) and isinstance(model.get("id"), str):
                architecture = model.get("architecture") if isinstance(model.get("architecture"), dict) else {}
                modalities = architecture.get("input_modalities")
                if isinstance(modalities, list):
                    table[model["id"]] = [str(m) for m in modalities]
    except (OSError, ValueError, AttributeError):
        table = {}
    with _lock:
        _catalog_cache = (mtime, table)
    return table


def accepts_images(model: str) -> bool:
    """False only when the catalogue says the model has no image input."""
    if not model or not _is_openrouter():
        return True
    modalities = _catalog().get(model)
    return modalities is None or "image" in modalities


def route_key(model: str) -> str:
    preferences = utils.build_openrouter_provider_preferences() or {}
    providers = preferences.get("order") or []
    return f"{model}|{','.join(providers) if providers else '*'}"


def _limits_path() -> str:
    return os.path.join(utils.DATA_DIR, "native_image_limits.json")


def _load_limits() -> dict:
    global _limits
    with _lock:
        if _limits is None:
            try:
                with open(_limits_path(), encoding="utf-8") as handle:
                    data = json.load(handle)
                _limits = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                _limits = {}
        return _limits


def learned_limit(model: str) -> int | None:
    entry = _load_limits().get(route_key(model))
    value = entry.get("max_images") if isinstance(entry, dict) else None
    return value if isinstance(value, int) and value >= 0 else None


def remember_limit(model: str, limit: int, error_text: str = "") -> None:
    limits = _load_limits()
    with _lock:
        limits[route_key(model)] = {
            "max_images": int(limit),
            "learned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "error": str(error_text or "")[:300],
        }
        snapshot = dict(limits)
    try:
        os.makedirs(utils.DATA_DIR, exist_ok=True)
        temporary = _limits_path() + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, indent=2, sort_keys=True)
        os.replace(temporary, _limits_path())
    except OSError:
        pass  # Still applies for this process.


def effective_limit(model: str) -> int:
    if not accepts_images(model):
        return 0
    learned = learned_limit(model)
    configured = max(0, utils.MAX_IMAGES_PER_REQUEST)
    return configured if learned is None else min(configured, learned)


# ── provider errors ──────────────────────────────────────────────

def parse_image_error(status: int, text: str, sent_images: int) -> tuple[int, bool] | None:
    """(limit for the retry, whether to remember it), or None if not about images.

    The limit is the count the provider names (``At most 1 image(s)``) or 0
    when it says images are not supported; both are remembered for this model
    and route. An image complaint without a number (an image too large, say)
    retries with only the newest image, or none, and is not remembered.
    """
    if sent_images <= 0 or status not in (400, 413, 422) or not text or not _IMAGE_WORD.search(text):
        return None
    for pattern in _NO_IMAGE_PATTERNS:
        if pattern.search(text):
            return 0, True
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(text)
        if match:
            return max(0, min(int(match.group(1)), sent_images - 1)), True
    return (1 if sent_images > 1 else 0), False


def prepare(messages: list, model: str) -> tuple[int, int]:
    """Apply the model's limit before a request. Returns (removed, limit)."""
    limit = effective_limit(model)
    return enforce_limit(messages, limit), limit
