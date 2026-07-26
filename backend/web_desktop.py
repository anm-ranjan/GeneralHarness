"""Desktop (Electron) request gating and forced static asset MIME types.
Cycle-free: depends only on FastAPI and utils."""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import utils


def _is_desktop_request(request: Request) -> bool:
    return request.headers.get("x-myharness-desktop", "").strip() == "1"


def _reject_browser_ui_if_electron_only(request: Request) -> None:
    if utils.DESKTOP_ELECTRON_ONLY and not _is_desktop_request(request):
        raise HTTPException(status_code=403, detail="The browser UI is disabled while desktop.electron_only is enabled.")


# Force these Content-Types regardless of the host OS mimetypes registry.
# Locked-down Windows clients can map .js to text/plain in the registry, which
# clobbers mimetypes.add_type and makes Chromium refuse to execute Vite module
# scripts (black screen). We rewrite the header on the ASGI response instead of
# trusting mimetypes.guess_type.
_FORCED_ASSET_CONTENT_TYPES = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
}


def _forced_content_type(path: str) -> str | None:
    lowered = path.lower()
    for suffix, content_type in _FORCED_ASSET_CONTENT_TYPES.items():
        if lowered.endswith(suffix):
            return content_type
    return None


class DesktopAwareStaticFiles(StaticFiles):
    async def __call__(self, scope, receive, send):
        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        if utils.DESKTOP_ELECTRON_ONLY and headers.get("x-myharness-desktop", "").strip() != "1":
            response = JSONResponse(
                {"detail": "The browser UI is disabled while desktop.electron_only is enabled."},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        forced = _forced_content_type(scope.get("path", ""))
        if forced is None:
            await super().__call__(scope, receive, send)
            return

        async def send_with_forced_type(message):
            if message["type"] == "http.response.start":
                raw_headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.decode("latin1").lower() != "content-type"
                ]
                raw_headers.append((b"content-type", forced.encode("latin1")))
                message = {**message, "headers": raw_headers}
            await send(message)

        await super().__call__(scope, receive, send_with_forced_type)


def _desktop_backend_host() -> str | None:
    # The Electron shell connects over the configured desktop.backend_url,
    # which may be a LAN address rather than loopback.
    url = (utils.DESKTOP_BACKEND_URL or "").strip()
    if not url:
        return None
    return urlparse(url).hostname or None
