"""Machine-local native model and OpenRouter provider, set from Settings.

Stored as ``<data>/native_settings.json`` (data/ is host-local and survives
syncs), applied over agent_config.yaml at startup and on every save. One
model replaces the configured default, read, write and summary models.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import utils

_lock = threading.Lock()
# What agent_config.yaml set, so clearing the override can restore it.
_CONFIG_DEFAULTS = {
    "MODEL": utils.MODEL,
    "READ_MODEL": utils.READ_MODEL,
    "WRITE_MODEL": utils.WRITE_MODEL,
    "SUMMARY_MODEL": utils.SUMMARY_MODEL,
    "API_PROVIDER": utils.API_PROVIDER,
}


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
    return settings


def clear() -> None:
    with _lock:
        _path().unlink(missing_ok=True)
        _apply({})


def current() -> dict:
    """Effective native model and providers, and where they come from."""
    saved = load()
    provider = utils.API_PROVIDER
    return {
        "model": utils.MODEL,
        "providers": normalize_providers(provider if isinstance(provider, (list, tuple)) else [provider] if provider else []),
        "source": "settings" if saved else "config",
        "config_model": _CONFIG_DEFAULTS["MODEL"],
        "openrouter": utils.build_openrouter_provider_preferences(configured_provider=["x"]) is not None,
    }
