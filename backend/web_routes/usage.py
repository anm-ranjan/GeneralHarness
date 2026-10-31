"""Usage, account limits, and the Native Settings model/provider."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import native_settings
import openrouter_catalog
import usage_ledger
import utils

import web_app

router = APIRouter()


@router.get("/api/usage")
def get_usage():
    return usage_ledger.summary(web_app._store)


@router.get("/api/usage/accounts")
def get_usage_accounts(refresh: bool = False):
    return usage_ledger.accounts(
        web_app._native_available(),
        web_app._codex_app_server_available(),
        web_app._claude_agent_available(),
        refresh=refresh,
    )


class NativeSettingsRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    providers: list[str] | str = Field(default_factory=list)


def _native_payload(model_info: dict | None = None) -> dict:
    payload = native_settings.current()
    if model_info is not None:
        payload["model_info"] = model_info
    return payload


@router.get("/api/native/settings")
def get_native_settings(details: bool = False):
    payload = _native_payload()
    if details and payload["openrouter"]:
        try:
            payload["model_info"] = openrouter_catalog.model_endpoints(payload["model"])
        except openrouter_catalog.CatalogError as exc:
            payload["model_error"] = str(exc)
    return payload


@router.put("/api/native/settings")
def update_native_settings(req: NativeSettingsRequest):
    model = req.model.strip()
    providers = native_settings.normalize_providers(req.providers)
    model_info = None
    if native_settings.current()["openrouter"]:
        try:
            model_info = openrouter_catalog.model_endpoints(model)
        except openrouter_catalog.CatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        by_tag = {endpoint["tag"]: endpoint for endpoint in model_info["endpoints"]}
        unknown = [tag for tag in providers if tag not in by_tag]
        if unknown:
            known = ", ".join(sorted(by_tag)) or "none"
            raise HTTPException(
                status_code=400,
                detail=f"{model} has no provider {', '.join(unknown)}. Providers serving it: {known}",
            )
        without_tools = [tag for tag in providers if not by_tag[tag]["tools"]]
        if without_tools:
            raise HTTPException(
                status_code=400,
                detail=f"{', '.join(without_tools)} does not support tool calling for {model}; native needs it",
            )
        if not providers and not any(endpoint["tools"] for endpoint in model_info["endpoints"]):
            raise HTTPException(status_code=400, detail=f"No provider serves {model} with tool calling")
    native_settings.save(model, providers)
    payload = _native_payload(model_info)
    web_app._manager.notify_global({
        "type": "native_settings", "model": payload["model"], "providers": payload["providers"],
    })
    return payload


@router.delete("/api/native/settings")
def reset_native_settings():
    native_settings.clear()
    payload = _native_payload()
    web_app._manager.notify_global({
        "type": "native_settings", "model": payload["model"], "providers": payload["providers"],
    })
    return payload
