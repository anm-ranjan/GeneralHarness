"""Machine-local native model, OpenRouter provider and agent loop, set from Settings.

Stored as ``<data>/native_settings.json`` (data/ is host-local and survives
syncs), applied over agent_config.yaml at startup and on every save. One
model replaces the configured default, read, write and summary models. The
``agent`` key holds agent-loop limits (iterations, nudges, sub-agents) that
replace their agent_config.yaml values; it is kept apart from the model, so
resetting one leaves the other alone. ``reasoning_replay`` there replaces
reasoning_replay.mode; the YAML's per-model ``models:`` entries still win.

The endpoint is either the configured api.base_url ("config", OpenRouter or
another hosted API) or a local OpenAI-compatible server ("local", for example
llama-server) given by its base URL. A local endpoint gets no API key: the
stored Native key is never sent to it, and Native needs none.

The context window follows the model and providers in effect: on OpenRouter
it comes from the providers' listed windows, on a local server from the n_ctx
its /models lists, otherwise (or when the lookup fails) from
memory.context_limit_tokens in agent_config.yaml.

``server`` and ``launched`` belong to model_server.py (the LAN llama-server
launcher); probe_local() is "Check endpoint" and saves nothing. The file is a
protected path, so agent tools cannot change the saved launch script.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from urllib.parse import urlparse

import requests

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
    "BASE_URL": utils.BASE_URL,
}
ENDPOINTS = ("config", "local")
_LOCAL_TIMEOUT = 5
# Whether BASE_URL currently holds a local endpoint set here.
_endpoint_state = {"local": False}
# Agent-loop settings: key -> (utils attribute, kind, minimum, maximum).
# Each is read from utils when it is used, so a save applies to the next turn.
# For the two nudges 0 turns them off; 0 images per request sends none.
AGENT_FIELDS = {
    "max_iterations": ("MAX_AGENT_ITERATIONS", int, 1, 1000),
    "tool_call_checkpoint": ("TOOL_CALL_CHECKPOINT", int, 0, 1000),
    "plan_reminder_after_tool_calls": ("PLAN_REMINDER_AFTER_TOOL_CALLS", int, 0, 100),
    "max_images_per_request": ("MAX_IMAGES_PER_REQUEST", int, 0, 50),
    "multi_agent_enabled": ("MULTI_AGENT_ENABLED", bool, None, None),
    "max_concurrent_subagents": ("MAX_CONCURRENT_SUBAGENTS", int, 1, 16),
    "max_total_subagents": ("MAX_TOTAL_SUBAGENTS", int, 1, 200),
    "max_subagent_depth": ("MAX_SUBAGENT_DEPTH", int, 1, 5),
    "reasoning_replay": ("REASONING_REPLAY_MODE", str, None, None),
}
# Allowed values of the str fields.
AGENT_CHOICES = {"reasoning_replay": utils.REASONING_REPLAY_MODES}
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


class EndpointError(ValueError):
    pass


def normalize_base_url(value) -> str:
    """``http://host:8081/v1/`` or a pasted ``.../chat/completions`` URL -> base URL."""
    text = str(value or "").strip().rstrip("/")
    if text.endswith("/chat/completions"):
        text = text[: -len("/chat/completions")].rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise EndpointError("Enter the server's base URL, for example http://127.0.0.1:8081/v1")
    return text


def load() -> dict:
    data = _read_file()
    if not isinstance(data.get("model"), str) or not data["model"].strip():
        return {}
    if data.get("endpoint") == "local":
        try:
            base_url = normalize_base_url(data.get("base_url"))
        except EndpointError:
            return {}  # a hand-edited bad URL: back to the configuration
        return {"model": data["model"].strip(), "providers": [], "endpoint": "local", "base_url": base_url}
    return {"model": data["model"].strip(), "providers": normalize_providers(data.get("providers"))}


def _local_model_entry(models: dict, model: str):
    entries = [entry for entry in (models.get("data") or []) if isinstance(entry, dict)]
    for entry in entries:
        if model == entry.get("id") or model in (entry.get("aliases") or []):
            return entry
    return None


def local_models(base_url: str) -> dict:
    """GET <base_url>/models of a local server; raises EndpointError when unreachable."""
    try:
        response = requests.get(f"{base_url}/models", timeout=_LOCAL_TIMEOUT)
        if response.status_code == 503:  # llama-server while it loads the model
            raise EndpointError(f"{base_url} is up but not ready yet (HTTP 503, usually still loading the model)")
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise EndpointError(f"No OpenAI-compatible server answered at {base_url}/models ({exc})") from exc
    if not isinstance(data, dict):
        raise EndpointError(f"{base_url}/models did not return a model list")
    return data


def check_local(model: str, base_url: str) -> dict:
    """The server's entry for model; raises EndpointError naming what it serves."""
    models = local_models(base_url)
    entry = _local_model_entry(models, model)
    if entry is None:
        served = [str(e.get("id")) for e in models.get("data") or [] if isinstance(e, dict) and e.get("id")]
        raise EndpointError(f"{base_url} does not serve '{model}'. It serves: {', '.join(served) or 'nothing'}")
    return entry


def probe_local(base_url, model: str = "") -> dict:
    """"Check endpoint": does a local server answer, and does it list the model?
    Saves nothing. Raises EndpointError only for a malformed base URL."""
    base_url = normalize_base_url(base_url)
    model = str(model or "").strip()
    started = time.perf_counter()
    try:
        models = local_models(base_url)
    except EndpointError as exc:
        return {"ok": False, "base_url": base_url, "message": str(exc),
                "ms": int((time.perf_counter() - started) * 1000)}
    ms = int((time.perf_counter() - started) * 1000)
    served = [str(e.get("id")) for e in models.get("data") or [] if isinstance(e, dict) and e.get("id")]
    entry = _local_model_entry(models, model) if model else None
    n_ctx = ((entry or {}).get("meta") or {}).get("n_ctx")
    window = n_ctx if isinstance(n_ctx, int) and n_ctx > 0 else None
    if not model:
        message = f"Responding in {ms} ms."
    elif entry is not None:
        message = f"Responding in {ms} ms and serves {model}" + (f" ({window:,} tokens)." if window else ".")
    else:
        message = f"Responding in {ms} ms, but it does not serve '{model}'."
    return {"ok": entry is not None or not model, "base_url": base_url, "message": message, "ms": ms,
            "models": served[:50], "model_listed": (entry is not None) if model else None,
            "context_window": window}


class AgentSettingError(ValueError):
    pass


_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
REASONING_MAP_KEYS = ("variable", "low", "medium", "high")


def normalize_reasoning_map(values) -> dict:
    """Validated reasoning map (all four keys); raises AgentSettingError."""
    if not isinstance(values, dict):
        raise AgentSettingError("The reasoning map must be an object")
    unknown = sorted(set(values) - set(REASONING_MAP_KEYS))
    if unknown:
        raise AgentSettingError(f"Unknown reasoning map key '{unknown[0]}'")
    result = {}
    for key in REASONING_MAP_KEYS:
        value = values.get(key, utils.REASONING_MAP_DEFAULT[key])
        if not isinstance(value, str):
            raise AgentSettingError(f"{key} must be text")
        value = value.strip()
        if len(value) > 64:
            raise AgentSettingError(f"{key} must be at most 64 characters")
        result[key] = value
    if not _VARIABLE_RE.match(result["variable"]):
        raise AgentSettingError("The template variable must be a name such as reasoning_effort")
    return result


def load_reasoning_map() -> dict:
    """The saved map, or the Inkling 2 defaults (also for a hand-edited bad entry)."""
    saved = _read_file().get("reasoning_map")
    try:
        return normalize_reasoning_map(saved) if isinstance(saved, dict) else dict(utils.REASONING_MAP_DEFAULT)
    except AgentSettingError:
        return dict(utils.REASONING_MAP_DEFAULT)


def save_reasoning_map(values: dict) -> dict:
    """Replace the map; the defaults are not stored, so reset follows them."""
    mapping = normalize_reasoning_map(values)
    with _lock:
        data = _read_file()
        if mapping == utils.REASONING_MAP_DEFAULT:
            data.pop("reasoning_map", None)
        else:
            data["reasoning_map"] = mapping
        _write_file(data)
        utils.LOCAL_REASONING_MAP = mapping
    return current_reasoning_map()


def clear_reasoning_map() -> dict:
    return save_reasoning_map(dict(utils.REASONING_MAP_DEFAULT))


def current_reasoning_map() -> dict:
    return {
        "values": dict(utils.LOCAL_REASONING_MAP),
        "defaults": dict(utils.REASONING_MAP_DEFAULT),
        "custom": utils.LOCAL_REASONING_MAP != utils.REASONING_MAP_DEFAULT,
    }


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
        if kind is str:
            if value not in AGENT_CHOICES[key]:
                raise AgentSettingError(f"{key} must be one of {', '.join(AGENT_CHOICES[key])}")
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
    local = settings.get("endpoint") == "local"
    if settings:
        model = settings["model"]
        utils.MODEL = utils.READ_MODEL = utils.WRITE_MODEL = utils.SUMMARY_MODEL = model
        providers = settings.get("providers") or []
        utils.API_PROVIDER = providers or None
    else:
        for name, value in _CONFIG_DEFAULTS.items():
            if name != "BASE_URL":
                setattr(utils, name, value)
    if local:
        utils.BASE_URL = settings["base_url"]
    elif _endpoint_state["local"]:
        utils.BASE_URL = _CONFIG_DEFAULTS["BASE_URL"]
    _endpoint_state["local"] = local
    utils.NATIVE_LOCAL_ENDPOINT = local


def _is_openrouter() -> bool:
    return utils.build_openrouter_provider_preferences(configured_provider=["x"]) is not None


def _set_context(window: int | None, source: str, detail: str) -> None:
    configured_limit = _CONFIG_DEFAULTS["CONTEXT_LIMIT_TOKENS"]
    configured_compact = _CONFIG_DEFAULTS["COMPACT_THRESHOLD_TOKENS"]
    limit = window if window else configured_limit
    utils.CONTEXT_LIMIT_TOKENS = limit
    utils.COMPACT_THRESHOLD_TOKENS = int(limit * _COMPACT_SHARE) if limit > 0 else configured_compact
    _context.update(source=source, detail=detail)


def _resolve_local_context(model: str, base_url: str) -> None:
    try:
        entry = _local_model_entry(local_models(base_url), model)
    except EndpointError as exc:
        _set_context(None, "config", f"agent_config.yaml (the local server could not be asked: {exc})")
        return
    if utils.MODEL != model or utils.BASE_URL != base_url:
        return
    n_ctx = ((entry or {}).get("meta") or {}).get("n_ctx")
    if isinstance(n_ctx, int) and n_ctx > 0:
        _set_context(n_ctx, "local", f"{base_url} (n_ctx {n_ctx})")
    else:
        _set_context(None, "config", "agent_config.yaml (the local server reports no n_ctx for it)")


def resolve_context_window() -> None:
    """Set the context limit (and cap the compaction point) for the model in effect."""
    model = utils.MODEL
    if _endpoint_state["local"]:
        _resolve_local_context(model, utils.BASE_URL)
        return
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
        utils.LOCAL_REASONING_MAP = load_reasoning_map()
        return settings


def save(model: str, providers: list[str], endpoint: str = "config", base_url: str = "") -> dict:
    if endpoint == "local":
        settings = {"model": model.strip(), "providers": [], "endpoint": "local",
                    "base_url": normalize_base_url(base_url)}
    else:
        settings = {"model": model.strip(), "providers": normalize_providers(providers)}
    with _lock:
        data = _read_file()
        for key in ("endpoint", "base_url"):
            data.pop(key, None)
        data.update(settings)
        _write_file(data)
        _apply(settings)
    resolve_context_window()
    return settings


def config_is_openrouter() -> bool:
    """Whether agent_config.yaml's own api.base_url is OpenRouter."""
    return utils.build_openrouter_provider_preferences(
        base_url=_CONFIG_DEFAULTS["BASE_URL"], configured_provider=["x"]) is not None


def clear() -> None:
    with _lock:
        data = _read_file()
        for key in ("model", "providers", "endpoint", "base_url"):
            data.pop(key, None)
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
        "choices": {key: list(values) for key, values in AGENT_CHOICES.items()},
        # Models with their own reasoning_replay entry in agent_config.yaml.
        "replay_model_overrides": sorted(str(key) for key in utils.reasoning_replay_overrides()),
    }


def current() -> dict:
    """Effective native model and providers, and where they come from."""
    saved = load()
    provider = utils.API_PROVIDER
    local = saved.get("endpoint") == "local"
    return {
        "model": utils.MODEL,
        "providers": [] if local else normalize_providers(
            provider if isinstance(provider, (list, tuple)) else [provider] if provider else []),
        "endpoint": "local" if local else "config",
        "base_url": saved.get("base_url", "") if local else "",
        "config_openrouter": config_is_openrouter(),
        "source": "settings" if saved else "config",
        "config_model": _CONFIG_DEFAULTS["MODEL"],
        "openrouter": _is_openrouter(),
        "context_window": utils.CONTEXT_LIMIT_TOKENS,
        "compact_threshold": utils.COMPACT_THRESHOLD_TOKENS,
        "context_source": _context["source"],
        "context_detail": _context["detail"],
        "agent": current_agent(),
        "reasoning_map": current_reasoning_map(),
        "server": _server_status(),
    }


def _server_status() -> dict:
    import model_server  # imports this module; loaded late to avoid a cycle
    return model_server.saved_status()
