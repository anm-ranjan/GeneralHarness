import sys
import os
import tempfile
import unittest
import mimetypes
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import backend.web_app as web_app
import utils


def request_with_headers(headers=None, client=("testclient", 50000)):
    raw_headers = [
        (key.lower().encode("latin1"), value.encode("latin1"))
        for key, value in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "client": client,
    })


class DesktopConfigTests(unittest.TestCase):
    def test_health_includes_desktop_metadata(self):
        data = web_app.health()
        self.assertIn("desktop_enabled", data)
        self.assertIn("electron_only", data)

    def test_vite_assets_use_chromium_compatible_mime_types(self):
        self.assertEqual(mimetypes.guess_type("index.js")[0], "application/javascript")
        self.assertEqual(mimetypes.guess_type("index.css")[0], "text/css")

    def test_forced_asset_content_type_helper(self):
        self.assertEqual(web_app._forced_content_type("/assets/index-abc.js"), "application/javascript")
        self.assertEqual(web_app._forced_content_type("/assets/index-abc.mjs"), "application/javascript")
        self.assertEqual(web_app._forced_content_type("/assets/index-abc.css"), "text/css")
        self.assertIsNone(web_app._forced_content_type("/assets/logo.png"))

    def test_static_files_force_js_content_type_despite_broken_registry(self):
        import asyncio
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "index-abc.js"
            asset.write_text("export const x = 1;\n", encoding="utf-8")

            static = web_app.DesktopAwareStaticFiles(directory=tmp)
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/index-abc.js",
                "headers": [],
                "client": ("testclient", 50000),
            }

            messages = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                messages.append(message)

            # Simulate a Windows registry that maps .js to text/plain.
            original = mimetypes.types_map.get(".js")
            mimetypes.add_type("text/plain", ".js")
            try:
                asyncio.run(static(scope, receive, send))
            finally:
                if original is not None:
                    mimetypes.add_type(original, ".js")

            start = next(m for m in messages if m["type"] == "http.response.start")
            self.assertEqual(start["status"], 200)
            headers = {
                key.decode("latin1").lower(): value.decode("latin1")
                for key, value in start["headers"]
            }
            self.assertEqual(headers["content-type"], "application/javascript")

    def test_electron_only_guard_blocks_browser_ui_without_desktop_header(self):
        original = utils.DESKTOP_ELECTRON_ONLY
        try:
            utils.DESKTOP_ELECTRON_ONLY = True
            with self.assertRaises(HTTPException):
                web_app._reject_browser_ui_if_electron_only(request_with_headers())
            web_app._reject_browser_ui_if_electron_only(
                request_with_headers({"X-MyHarness-Desktop": "1"})
            )
        finally:
            utils.DESKTOP_ELECTRON_ONLY = original

    def test_credential_routes_are_desktop_only_and_never_return_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {
                "MYHARNESS_CREDENTIALS_DIR": tmp,
                "MYHARNESS_API_KEY": "",
                "MYHARNESS_STT_API_KEY": "",
                "MYHARNESS_DESKTOP_CREDENTIAL_TOKEN": "desktop-test-token",
            },
        ):
            original_native_enabled = utils.NATIVE_CONFIG_ENABLED
            utils.NATIVE_CONFIG_ENABLED = False
            self.addCleanup(setattr, utils, "NATIVE_CONFIG_ENABLED", original_native_enabled)
            with self.assertRaises(HTTPException) as rejected:
                web_app.get_credentials(request_with_headers())
            self.assertEqual(rejected.exception.status_code, 403)

            secret = "sk-settings-secret"
            result = web_app.update_credentials(
                request_with_headers({
                    "X-MyHarness-Desktop": "1",
                    "X-MyHarness-Desktop-Credential": "desktop-test-token",
                }),
                web_app.CredentialUpdateRequest(native_api_key=secret),
            )
            self.assertTrue(result["native_api_key"]["configured"])
            self.assertTrue(result["native_api_key"]["stored"])
            self.assertEqual(result["native_api_key"]["source"], "credential")
            self.assertNotIn(secret, repr(result))
            self.assertTrue(web_app._native_available())

            removed = web_app.update_credentials(
                request_with_headers({
                    "X-MyHarness-Desktop": "1",
                    "X-MyHarness-Desktop-Credential": "desktop-test-token",
                }),
                web_app.CredentialUpdateRequest(remove_native_api_key=True),
            )
            self.assertFalse(removed["native_api_key"]["stored"])

    def test_electron_enables_microphone_for_configured_backend_origin(self):
        source = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
        self.assertIn("unsafely-treat-insecure-origin-as-secure", source)
        self.assertIn("allowMyHarnessMediaPermission", source)
        self.assertIn('permission !== "media"', source)
        self.assertIn("requestingOrigin === allowedOrigin", source)

    def test_electron_quit_only_shuts_down_owned_backend(self):
        source = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
        self.assertIn('backendMode !== "local"', source)
        self.assertIn("Skipping backend shutdown", source)
        self.assertIn('new URL("/api/shutdown", activeBackendUrl)', source)

    def test_electron_uses_the_same_configured_data_directory_as_the_backend(self):
        source = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
        self.assertIn("function configuredDataDir()", source)
        self.assertIn("const dataDir = configuredDataDir()", source)
        self.assertIn('MYHARNESS_WEB_DATA_DIR: dataDir', source)
        self.assertNotIn('app.getPath("userData")', source)
        self.assertNotIn("migratePackagedData", source)


if __name__ == "__main__":
    unittest.main()
