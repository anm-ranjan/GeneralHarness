"""Refuse thread-starting calls that agents may not make (see request_origin).

- Antigravity (an ACP agent) may not create threads, start agent runs, send,
  steer or edit messages, or answer approvals and questions.
- No agent may create an ACP thread or put a prompt into one: ACP threads
  only run prompts the user typed.
- No agent may answer approvals or questions; they are the user's to answer.

Everything else, including reading the tree and fleet-import, stays open.
"""
from __future__ import annotations

import json
import re
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

import acp_provider
import request_origin

_CREATE = re.compile(r"^/api/(sessions|chats)/?$")
_AGENT_RUN = re.compile(r"^/api/agents/[^/]+/runs/?$")
_PROMPT = re.compile(
    r"^/api/sessions/(?P<sid>[^/]+)/(message|stash|queue/reorder|queue/[^/]+/steer|queue/[^/]+|steering/[^/]+)/?$"
)
_ANSWER = re.compile(r"^/api/sessions/[^/]+/(approval|question)/?$")

ACP_REFUSAL = (
    "Antigravity threads cannot start, message or answer other threads. "
    "Ask the user to do it from the app."
)
AGENT_TO_ACP_REFUSAL = (
    "Agents cannot start Antigravity threads or send them prompts; Antigravity only runs prompts "
    "the user typed. Use another provider, or ask the user."
)
ANSWER_REFUSAL = "Approvals and questions are the user's to answer; agents cannot answer them."


def _guarded(method: str, path: str) -> str:
    if method not in {"POST", "PUT", "PATCH"}:
        return ""
    if method == "POST" and _CREATE.match(path):
        return "create"
    if method == "POST" and _AGENT_RUN.match(path):
        return "agent_run"
    if _ANSWER.match(path):
        return "answer"
    if _PROMPT.match(path):
        return "prompt"
    return ""


def _refuse(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=403)


async def _origin(request: Request) -> str:
    import asyncio

    client = request.client
    host, port = (client.host, client.port) if client else (None, None)
    return await asyncio.to_thread(request_origin.classify, host, port, acp_provider.live_agent_pids())


def make_middleware(load_provider: Callable[[str], str | None]):
    async def guard(request: Request, call_next: Callable[[Request], Awaitable]):
        kind = _guarded(request.method, request.url.path)
        if not kind:
            return await call_next(request)
        origin = await _origin(request)
        if origin == request_origin.USER:
            return await call_next(request)
        if origin == request_origin.ACP:
            return _refuse(ACP_REFUSAL)
        # An agent other than Antigravity.
        if kind == "answer":
            return _refuse(ANSWER_REFUSAL)
        if kind == "create":
            try:
                provider = (json.loads(await request.body() or b"{}") or {}).get("provider")
            except (ValueError, AttributeError):
                provider = None
            if acp_provider.is_acp(provider):
                return _refuse(AGENT_TO_ACP_REFUSAL)
        if kind == "prompt":
            sid = _PROMPT.match(request.url.path).group("sid")
            if acp_provider.is_acp(load_provider(sid)):
                return _refuse(AGENT_TO_ACP_REFUSAL)
        return await call_next(request)

    return guard
