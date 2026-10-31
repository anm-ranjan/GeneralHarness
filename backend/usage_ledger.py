"""Usage ledger: records every model call and summarizes cost and limits.

Sources, per provider:
- native: OpenRouter usage, including ``cost`` (the amount charged).
- Codex: app-server ``thread/tokenUsage/updated`` tokens, priced here from
  OpenRouter's OpenAI prices; plan and 5h/weekly windows from
  ``account/read`` and ``account/rateLimits/read``.
- Claude: Agent SDK ``ResultMessage.model_usage`` (``costUSD`` is the SDK's
  estimate), plan from ``get_server_info()``, limit state from
  ``RateLimitEvent``. Nothing is read from the CLI directly.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import openrouter_catalog
import utils
from claude_agent_provider import CLAUDE_PROVIDER_ID, account_snapshot, probe_account

log = logging.getLogger(__name__)

CODEX_PROVIDER_ID = "codex-app-server"
_CLAUDE_PROBE_TTL = 15 * 60
_claude_probed_at = 0.0


def _codex_runtime():
    import web_runs  # imported late: web_runs imports the web app

    return getattr(web_runs, "_codex_runtime", None)


def codex_auth() -> str:
    """'subscription' or 'api' from the app-server's account notifications."""
    runtime = _codex_runtime()
    provider = getattr(runtime, "_provider", None) if runtime else None
    notes = getattr(getattr(provider, "transport", None), "account_notifications", {}) or {}
    mode = str((notes.get("account/updated") or {}).get("authMode") or "").lower()
    if mode in ("chatgpt", "chatgptauthtokens"):
        return "subscription"
    if mode in ("apikey", "api_key"):
        return "api"
    return ""


def claude_auth(snapshot: dict | None = None) -> str:
    snapshot = snapshot if snapshot is not None else account_snapshot()
    source = str(snapshot.get("api_key_source") or "")
    account = snapshot.get("account") or {}
    if source and source != "none":
        return "api"
    if str(account.get("apiProvider") or "firstParty") != "firstParty":
        return "api"
    if account.get("subscriptionType"):
        return "subscription"
    return ""


def record(store, session_id: str, metrics: dict) -> None:
    """Persist one api_metrics payload. Never raises into a run."""
    try:
        provider = str(metrics.get("provider") or "native")
        auth = "api" if provider == "native" else codex_auth() if provider == CODEX_PROVIDER_ID else claude_auth()
        store.record_usage(session_id, metrics, auth=auth)
    except Exception:
        log.debug("usage record failed", exc_info=True)


def periods(now: datetime | None = None) -> list[dict]:
    """Today (local midnight), then rolling windows, then all time."""
    now = now or datetime.now().astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spans = [
        ("today", "Today", midnight),
        ("week", "7 days", now - timedelta(days=7)),
        ("month", "30 days", now - timedelta(days=30)),
        ("half_year", "6 months", now - timedelta(days=182)),
        ("year", "12 months", now - timedelta(days=365)),
        ("all", "All time", None),
    ]
    return [
        {"id": key, "label": label, "since": start.astimezone(timezone.utc).isoformat() if start else None}
        for key, label, start in spans
    ]


def _priced(row: dict) -> dict:
    row = {key: (value or 0) if key not in ("cost_usd", "model", "provider", "auth", "cost_source") else value
           for key, value in row.items()}
    row["cost_kind"] = "reported" if row["provider"] == "native" else "estimated"
    if row["provider"] == CODEX_PROVIDER_ID:
        price = openrouter_catalog.codex_price(row.get("model") or "")
        if price:
            row["cost_usd"] = openrouter_catalog.price_tokens(
                price, row["input_tokens"], row["cache_read_tokens"], row["cache_write_tokens"], row["output_tokens"]
            )
            row["unpriced"] = 0
        else:
            row["cost_usd"] = None
    if row.get("auth") == "subscription":
        # Covered by the plan: the figure is what the same use would cost on the API.
        row["cost_kind"] = "equivalent"
    return row


def summary(store) -> dict:
    result = []
    for period in periods():
        rows = [_priced(row) for row in store.usage_totals(period["since"])]
        rows.sort(key=lambda row: (row["provider"], -(row.get("cost_usd") or 0), row["model"]))
        result.append({**period, "rows": rows})
    return {"periods": result, "first_record": store.usage_first_record()}


def _window_label(minutes) -> str:
    if minutes == 300:
        return "5 hours"
    if minutes == 10080:
        return "Weekly"
    if isinstance(minutes, int) and minutes > 0:
        return f"{minutes // 60} hours" if minutes < 1440 else f"{minutes // 1440} days"
    return "Window"


def _codex_windows(snapshot: dict | None) -> list[dict]:
    windows = []
    for key in ("primary", "secondary"):
        window = (snapshot or {}).get(key)
        if isinstance(window, dict) and window.get("usedPercent") is not None:
            windows.append({
                "label": _window_label(window.get("windowDurationMins")),
                "used_percent": window.get("usedPercent"),
                "window_minutes": window.get("windowDurationMins"),
                "resets_at": window.get("resetsAt"),
            })
    return windows


def codex_status(available: bool) -> dict:
    if not available:
        return {"available": False}
    try:
        status = _codex_runtime_or_start().account_status()
    except Exception as exc:
        return {"available": True, "error": f"Could not read the Codex account: {exc}"}
    account = (status.get("account") or {}).get("account") or {}
    kind = account.get("type") or ""
    limits = (status.get("rate_limits") or {}).get("rateLimits") or {}
    update = (status.get("rate_limits_update") or {}).get("rateLimits") or {}
    # A live update after the read is newer; fall back to the read.
    snapshot = update if update.get("primary") or update.get("secondary") else limits
    credits = limits.get("credits") if isinstance(limits.get("credits"), dict) else None
    resets = ((status.get("rate_limits") or {}).get("rateLimitResetCredits") or {}).get("availableCount")
    return {
        "available": True,
        "auth": "subscription" if kind == "chatgpt" else "api" if kind == "apiKey" else "cloud" if kind else "",
        "login": {"chatgpt": "ChatGPT", "apiKey": "API key", "amazonBedrock": "Amazon Bedrock", "gcp": "Google Cloud"}.get(kind, kind),
        "plan": account.get("planType") or limits.get("planType") or "",
        "windows": _codex_windows(snapshot),
        "limit_reached": limits.get("rateLimitReachedType"),
        "credits_balance": credits.get("balance") if credits and credits.get("hasCredits") else None,
        "reset_credits": resets,
    }


def _codex_runtime_or_start():
    import web_runs

    return web_runs._get_codex_runtime()


def claude_status(available: bool, refresh: bool = False) -> dict:
    global _claude_probed_at
    if not available:
        return {"available": False}
    snapshot = account_snapshot()
    error = ""
    stale = time.time() - _claude_probed_at > _CLAUDE_PROBE_TTL
    if refresh or (not snapshot.get("account") and stale):
        _claude_probed_at = time.time()
        try:
            snapshot = probe_account(cli_path=utils.CLAUDE_AGENT_BINARY)
        except Exception as exc:
            error = f"Could not read the Claude account: {exc}"
    account = snapshot.get("account") or {}
    limits = []
    for window, info in sorted((snapshot.get("rate_limits") or {}).items()):
        utilization = info.get("utilization")
        limits.append({
            "type": window,
            "label": {"five_hour": "5 hours", "seven_day": "Weekly", "seven_day_opus": "Weekly (Opus)",
                      "seven_day_sonnet": "Weekly (Sonnet)", "overage": "Extra usage"}.get(window, window.replace("_", " ")),
            "status": info.get("status") or "",
            "resets_at": info.get("resetsAt"),
            "used_percent": round(utilization * 100) if isinstance(utilization, (int, float)) else None,
            "observed_at": info.get("observed_at"),
        })
    auth = claude_auth(snapshot)
    provider = account.get("apiProvider") or ""
    return {
        "available": True,
        "auth": auth,
        "login": "API key" if auth == "api" and provider in ("", "firstParty") else
                 {"bedrock": "Amazon Bedrock", "vertex": "Google Vertex", "foundry": "Microsoft Foundry"}.get(provider, provider)
                 if auth == "api" else "Claude account" if auth else "",
        "plan": account.get("subscriptionType") or "",
        "limits": limits,
        "error": error,
    }


def native_status() -> dict:
    return {"available": True, "auth": "api", "login": "OpenRouter API key", "model": utils.MODEL}


def accounts(native_available: bool, codex_available: bool, claude_available: bool, refresh: bool = False) -> dict:
    return {
        "native": native_status() if native_available else {"available": False},
        "codex": codex_status(codex_available),
        "claude": claude_status(claude_available, refresh=refresh),
    }
