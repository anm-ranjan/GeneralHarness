"""Machine-local native model, OpenRouter provider and agent loop, set from Settings.

Stored as ``<data>/native_settings.json`` (data/ is host-local and survives
syncs), applied over agent_config.yaml at startup and on every save. One
model replaces the configured default, read, write and summary models. The
``agent`` key holds agent-loop limits (iterations, nudges, sub-agents) that
replace their agent_config.yaml values; it is kept apart from the model, so
resetting one leaves the other alone.

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
# Agent-loop settings: key -> (utils attribute, kind, minimum, maximum).
# Each is read from utils when it is used, so a save applies to the next turn.
# For the two nudges 0 turns them off.
AGENT_FIELDS = {
    "max_iterations": ("MAX_AGENT_ITERATIONS", int, 1, 1000),
    "tool_call_checkpoint": ("TOOL_CALL_CHECKPOINT", int, 0, 1000),
    "plan_reminder_after_tool_calls": ("PLAN_REMINDER_AFTER_TOOL_CALLS", int, 0, 100),
    "shell_default_timeout": ("DEFAULT_SHELL_TIMEOUT", int, 5, 3600),
    "multi_agent_enabled": ("MULTI_AGENT_ENABLED", bool, None, None),
    "max_concurrent_subagents": ("MAX_CONCURRENT_SUBAGENTS", int, 1, 16),
    "max_total_subagents": ("MAX_TOTAL_SUBAGENTS", int, 1, 200),
    "max_subagent_depth": ("MAX_SUBAGENT_DEPTH", int, 1, 5),
}
_AGENT_DEFAULTS = {key: getattr(utils, attr) for key, (attr, _kind, _lo, _hi) in AGENT_FIELDS.items()}
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


def _read_file() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_file(data: dict) -> None:
    path = _path()
    if not data:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def load() -> dict:
    data = _read_file()
    if not isinstance(data.get("model"), str) or not data["model"].strip():
        return {}
    return {"model": data["model"].strip(), "providers": normalize_providers(data.get("providers"))}


class AgentSettingError(ValueError):
    pass


def normalize_agent(values) -> dict:
    """Validated agent-loop overrides; raises AgentSettingError on a bad value."""
    if not isinstance(values, dict):
        raise AgentSettingError("Agent settings must be an object")
    result = {}
    for key, value in values.items():
        if key not in AGENT_FIELDS:
            raise AgentSettingError(f"Unknown agent setting '{key}'")
        _attr, kind, low, high = AGENT_FIELDS[key]
        if kind is bool:
            if not isinstance(value, bool):
                raise AgentSettingError(f"{key} must be true or false")
            result[key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise AgentSettingError(f"{key} must be a whole number")
        if not low <= value <= high:
            raise AgentSettingError(f"{key} must be between {low} and {high}")
        result[key] = int(value)
    return result


def load_agent() -> dict:
    """Saved agent-loop overrides; a hand-edited bad entry is skipped, not fatal."""
    saved = _read_file().get("agent")
    result = {}
    for key, value in (saved.items() if isinstance(saved, dict) else []):
        try:
            result.update(normalize_agent({key: value}))
        except AgentSettingError:
            continue
    return result


def _apply_agent(overrides: dict) -> None:
    for key, (attr, _kind, _lo, _hi) in AGENT_FIELDS.items():
        setattr(utils, attr, overrides.get(key, _AGENT_DEFAULTS[key]))


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
        _apply_agent(load_agent())
        return settings


def save(model: str, providers: list[str]) -> dict:
    settings = {"model": model.strip(), "providers": normalize_providers(providers)}
    with _lock:
        data = _read_file()
        data.update(settings)
        _write_file(data)
        _apply(settings)
    resolve_context_window()
    return settings


def clear() -> None:
    with _lock:
        data = _read_file()
        data.pop("model", None)
        data.pop("providers", None)
        _write_file(data)
        _apply({})
    resolve_context_window()


def save_agent(values: dict) -> dict:
    """Replace the agent-loop overrides. A value equal to its config default is
    dropped, so the setting keeps following agent_config.yaml."""
    overrides = {k: v for k, v in normalize_agent(values).items() if v != _AGENT_DEFAULTS[k]}
    with _lock:
        data = _read_file()
        if overrides:
            data["agent"] = overrides
        else:
            data.pop("agent", None)
        _write_file(data)
        _apply_agent(overrides)
    return current_agent()


def clear_agent() -> dict:
    return save_agent({})


def current_agent() -> dict:
    """Agent-loop values in effect, their agent_config.yaml defaults and limits."""
    saved = load_agent()
    return {
        "values": {key: getattr(utils, attr) for key, (attr, _k, _lo, _hi) in AGENT_FIELDS.items()},
        "defaults": dict(_AGENT_DEFAULTS),
        "overridden": sorted(saved),
        "limits": {key: {"min": lo, "max": hi} for key, (_a, kind, lo, hi) in AGENT_FIELDS.items() if kind is int},
    }


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
        "agent": current_agent(),
    }
