"""Machine-local native model and OpenRouter provider, set from Settings.

Stored as ``<data>/native_settings.json`` (data/ is host-local and survives
syncs), applied over agent_config.yaml at startup and on every save. One
model replaces the configured default, read, write and summary models.

The context window follows the model and providers in effect: on OpenRouter
it comes from the providers' listed windows, otherwise (or when OpenRouter
cannot be reached) from memory.context_limit_tokens in agent_config.yaml.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import openrouter_catalog
import utils

_lock = threading.Lock()
# What agent_config.yaml set, so clearing the override can restore it.
_CONFIG_DEFAULTS = {
    "MODEL": utils.MODEL,
    "READ_MODEL": utils.READ_MODEL,
    "WRITE_MODEL": utils.WRITE_MODEL,
    "SUMMARY_MODEL": utils.SUMMARY_MODEL,
    "API_PROVIDER": utils.API_PROVIDER,
    "CONTEXT_LIMIT_TOKENS": utils.CONTEXT_LIMIT_TOKENS,
    "COMPACT_THRESHOLD_TOKENS": utils.COMPACT_THRESHOLD_TOKENS,
}
# History is compacted once it fills this share of the context window, which
# leaves room for the reply and tool results. The configured
# compact_threshold_tokens applies only when there is no window (limit <= 0).
_COMPACT_SHARE = 0.75
# Where the context window in effect came from, for Settings.
_context = {"source": "config", "detail": "agent_config.yaml"}


def _path() -> Path:
    return Path(utils.DATA_DIR) / "native_settings.json"


def normalize_providers(value) -> list[str]:
    """``"azure/eu, openai"`` or a list -> ordered unique provider tags."""
    items = value.split(",") if isinstance(value, str) else value if isinstance(value, (list, tuple)) else []
    providers: list[str] = []
    for item in items:
        tag = str(item or "").strip()
        if tag and tag not in providers:
            providers.append(tag)
    return providers


def load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("model"), str) or not data["model"].strip():
        return {}
    return {"model": data["model"].strip(), "providers": normalize_providers(data.get("providers"))}


def _apply(settings: dict) -> None:
    if settings:
        model = settings["model"]
        utils.MODEL = utils.READ_MODEL = utils.WRITE_MODEL = utils.SUMMARY_MODEL = model
        providers = settings.get("providers") or []
        utils.API_PROVIDER = providers or None
    else:
        for name, value in _CONFIG_DEFAULTS.items():
            setattr(utils, name, value)


def _is_openrouter() -> bool:
    return utils.build_openrouter_provider_preferences(configured_provider=["x"]) is not None


def _set_context(window: int | None, source: str, detail: str) -> None:
    configured_limit = _CONFIG_DEFAULTS["CONTEXT_LIMIT_TOKENS"]
    configured_compact = _CONFIG_DEFAULTS["COMPACT_THRESHOLD_TOKENS"]
    limit = window if window else configured_limit
    utils.CONTEXT_LIMIT_TOKENS = limit
    utils.COMPACT_THRESHOLD_TOKENS = int(limit * _COMPACT_SHARE) if limit > 0 else configured_compact
    _context.update(source=source, detail=detail)


def resolve_context_window() -> None:
    """Set the context limit (and cap the compaction point) for the model in effect."""
    model = utils.MODEL
    provider = utils.API_PROVIDER
    providers = normalize_providers(provider if isinstance(provider, (list, tuple)) else [provider] if provider else [])
    if not _is_openrouter():
        _set_context(None, "config", "agent_config.yaml (the native API is not OpenRouter)")
        return
    try:
        found = openrouter_catalog.context_window(model, providers)
    except Exception as exc:  # network or catalog trouble: keep working on the config value
        _set_context(None, "config", f"agent_config.yaml (OpenRouter lookup failed: {exc})")
        return
    if utils.MODEL != model:  # changed while the lookup ran; that change resolves its own
        return
    if found is None:
        _set_context(None, "config", "agent_config.yaml (OpenRouter lists no window for it)")
    else:
        _set_context(found[0], "openrouter", found[1])


def resolve_context_window_in_background() -> None:
    """For startup: the lookup may need the network, and startup must not wait."""
    threading.Thread(target=resolve_context_window, name="native-context-window", daemon=True).start()


def apply_saved() -> dict:
    with _lock:
        settings = load()
        _apply(settings)
        return settings


def save(model: str, providers: list[str]) -> dict:
    settings = {"model": model.strip(), "providers": normalize_providers(providers)}
    with _lock:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        _apply(settings)
    resolve_context_window()
    return settings


def clear() -> None:
    with _lock:
        _path().unlink(missing_ok=True)
        _apply({})
    resolve_context_window()


def current() -> dict:
    """Effective native model and providers, and where they come from."""
    saved = load()
    provider = utils.API_PROVIDER
    return {
        "model": utils.MODEL,
        "providers": normalize_providers(provider if isinstance(provider, (list, tuple)) else [provider] if provider else []),
        "source": "settings" if saved else "config",
        "config_model": _CONFIG_DEFAULTS["MODEL"],
        "openrouter": _is_openrouter(),
        "context_window": utils.CONTEXT_LIMIT_TOKENS,
        "compact_threshold": utils.COMPACT_THRESHOLD_TOKENS,
        "context_source": _context["source"],
        "context_detail": _context["detail"],
    }
