"""OpenRouter's public model catalogue: prices and per-model providers.

Both endpoints are public (no API key). The model list is cached under the
data directory and refreshed at most once a day; it is used to price Codex
token usage, which the app-server reports without a cost.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests

import utils

OPENROUTER_API = "https://openrouter.ai/api/v1"
_CACHE_TTL_SECONDS = 24 * 3600
_TIMEOUT_SECONDS = 15

_lock = threading.Lock()
_prices: dict[str, dict[str, float]] | None = None
_loaded_at = 0.0
_refreshing = False


class CatalogError(RuntimeError):
    pass


def _cache_path() -> Path:
    return Path(utils.DATA_DIR) / "openrouter_models.json"


def _parse_prices(models: list) -> dict[str, dict[str, float]]:
    prices: dict[str, dict[str, float]] = {}
    for model in models if isinstance(models, list) else []:
        if not isinstance(model, dict) or not isinstance(model.get("id"), str):
            continue
        pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
        parsed = {}
        for key in ("prompt", "completion", "input_cache_read", "input_cache_write"):
            try:
                value = float(pricing.get(key))
            except (TypeError, ValueError):
                continue
            if value >= 0:
                parsed[key] = value
        if "prompt" in parsed and "completion" in parsed:
            prices[model["id"]] = parsed
    return prices


def _fetch_models() -> list:
    response = requests.get(f"{OPENROUTER_API}/models", timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, list):
        raise CatalogError("OpenRouter returned no model list")
    return data


def _refresh() -> None:
    global _prices, _loaded_at, _refreshing
    try:
        models = _fetch_models()
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"fetched_at": time.time(), "data": models}), encoding="utf-8")
        os.replace(temporary, path)
        with _lock:
            _prices = _parse_prices(models)
            _loaded_at = time.time()
    except Exception:
        pass
    finally:
        with _lock:
            _refreshing = False


def prices() -> dict[str, dict[str, float]]:
    """Per-token USD prices by OpenRouter model id. Never blocks on the network.

    Serves the disk cache; starts a background refresh when it is missing or
    older than a day. Empty until the first fetch has landed.
    """
    global _prices, _loaded_at, _refreshing
    with _lock:
        if _prices is None:
            try:
                cached = json.loads(_cache_path().read_text(encoding="utf-8"))
                _prices = _parse_prices(cached.get("data"))
                _loaded_at = float(cached.get("fetched_at") or 0)
            except (OSError, ValueError, AttributeError):
                _prices = {}
                _loaded_at = 0.0
        stale = time.time() - _loaded_at > _CACHE_TTL_SECONDS
        if stale and not _refreshing:
            _refreshing = True
            threading.Thread(target=_refresh, name="openrouter-catalog", daemon=True).start()
        return dict(_prices)


def codex_price(model: str) -> dict[str, float] | None:
    """OpenRouter price for a Codex model id such as ``gpt-6.1-sol``."""
    if not model:
        return None
    table = prices()
    name = model.strip()
    for candidate in (name, f"openai/{name}", f"openai/{name.split('/')[-1]}"):
        if candidate in table:
            return table[candidate]
    return None


def price_tokens(price: dict[str, float], input_tokens: int, cache_read: int, cache_write: int, output_tokens: int) -> float:
    """Cost of OpenAI-style counts, where cached input is part of input."""
    uncached = max(0, input_tokens - cache_read - cache_write)
    return (
        uncached * price["prompt"]
        + cache_read * price.get("input_cache_read", price["prompt"])
        + cache_write * price.get("input_cache_write", price["prompt"])
        + output_tokens * price["completion"]
    )


def model_endpoints(model: str) -> dict:
    """Name, context and providers (with ``tag``) serving an OpenRouter model."""
    model = (model or "").strip()
    if "/" not in model:
        raise CatalogError("Use the OpenRouter id, e.g. openai/gpt-6.1-terra")
    author, slug = model.split("/", 1)
    url = f"{OPENROUTER_API}/models/{quote(author, safe='')}/{quote(slug, safe=':')}/endpoints"
    try:
        response = requests.get(url, timeout=_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise CatalogError(f"Could not reach OpenRouter: {exc}") from exc
    if response.status_code == 404:
        raise CatalogError(f"OpenRouter has no model '{model}'")
    if response.status_code != 200:
        raise CatalogError(f"OpenRouter answered {response.status_code} for '{model}'")
    data = response.json().get("data") or {}
    endpoints = []
    for endpoint in data.get("endpoints") or []:
        if not isinstance(endpoint, dict) or not endpoint.get("tag"):
            continue
        params = endpoint.get("supported_parameters") or []
        pricing = endpoint.get("pricing") if isinstance(endpoint.get("pricing"), dict) else {}
        endpoints.append({
            "tag": endpoint["tag"],
            "provider_name": endpoint.get("provider_name") or "",
            "quantization": endpoint.get("quantization") or "",
            "context_length": endpoint.get("context_length"),
            "tools": "tools" in params,
            "reasoning": "reasoning" in params,
            "implicit_caching": bool(endpoint.get("supports_implicit_caching")),
            "pricing": {key: pricing.get(key) for key in ("prompt", "completion", "input_cache_read", "input_cache_write") if pricing.get(key) is not None},
        })
    return {
        "id": data.get("id") or model,
        "name": data.get("name") or model,
        "endpoints": endpoints,
    }
