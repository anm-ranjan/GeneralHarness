import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import harness_agent as agent
from backend.agent import utils


PUBLIC_ADDRESS = "93.184.216.34"


class FakeResponse:
    def __init__(self, body=b"", *, status=200, reason="OK", content_type="text/plain", headers=None):
        self.body = body
        self.status = status
        self.reason = reason
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.offset = 0
        self.fp = None
        self.closed = False

    def read(self, size=-1):
        if self.offset >= len(self.body):
            return b""
        end = len(self.body) if size < 0 else min(len(self.body), self.offset + size)
        chunk = self.body[self.offset:end]
        self.offset = end
        return chunk

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def run_fetch(response, url="https://example.com/article", **kwargs):
    connection = FakeConnection()
    with (
        patch.object(utils, "_resolve_web_addresses", return_value=[PUBLIC_ADDRESS]),
        patch.object(utils, "_open_pinned_web_connection", return_value=connection),
        patch.object(utils, "_send_web_request", return_value=response),
    ):
        result = utils.tool_web_fetch(url, **kwargs)
    return result, connection


class WebFetchSchemaTests(unittest.TestCase):
    def test_native_schema_exposes_only_new_fetch_name(self):
        names = {tool["function"]["name"] for tool in agent.READ_ONLY_TOOLS}
        self.assertIn("web_fetch", names)
        self.assertNotIn("web_request", names)
        self.assertNotIn("web_fetch", agent.WRITE_TOOL_NAMES)

    def test_legacy_post_is_disabled(self):
        self.assertIn("GET-only", utils.tool_web_request("https://example.com", "POST"))

    def test_native_status_redacts_query_values(self):
        status = agent._tool_status_line(
            "web_fetch", {"url": "https://example.com/data?token=secret#fragment"}
        )
        self.assertIn("?REDACTED", status)
        self.assertNotIn("secret", status)
        self.assertNotIn("fragment", status)


class WebFetchSafetyTests(unittest.TestCase):
    def test_rejects_non_http_credentials_and_private_targets(self):
        cases = {
            "file:///etc/passwd": [PUBLIC_ADDRESS],
            "https://user:secret@example.com/": [PUBLIC_ADDRESS],
            "http://127.0.0.1/admin": ["127.0.0.1"],
            "http://169.254.169.254/latest/meta-data/": ["169.254.169.254"],
        }
        for url, addresses in cases.items():
            with self.subTest(url=url), patch.object(utils, "_resolve_web_addresses", return_value=addresses):
                result = utils.tool_web_fetch(url)
            self.assertTrue(result.startswith("ERROR: Unsafe URL:"), result)

    def test_redirect_target_is_revalidated_before_second_connection(self):
        redirect = FakeResponse(status=302, reason="Found", headers={"Location": "https://127.0.0.1/private"})
        connection = FakeConnection()

        def addresses(hostname, *_args):
            return ["127.0.0.1"] if hostname == "127.0.0.1" else [PUBLIC_ADDRESS]

        with (
            patch.object(utils, "_resolve_web_addresses", side_effect=addresses),
            patch.object(utils, "_open_pinned_web_connection", return_value=connection) as open_connection,
            patch.object(utils, "_send_web_request", return_value=redirect),
        ):
            result = utils.tool_web_fetch("https://example.com/start")

        self.assertIn("Unsafe URL", result)
        self.assertEqual(open_connection.call_count, 1)
        self.assertTrue(redirect.closed)
        self.assertTrue(connection.closed)

    def test_rejects_https_to_http_redirect_downgrade(self):
        redirect = FakeResponse(
            status=302, reason="Found", headers={"Location": "http://example.com/plaintext"}
        )
        result, connection = run_fetch(redirect, url="https://example.com/start")
        self.assertIn("HTTPS-to-HTTP", result)
        self.assertTrue(redirect.closed)
        self.assertTrue(connection.closed)

    def test_https_connection_uses_validated_ip_and_original_tls_hostname(self):
        raw_socket = Mock()
        wrapped_socket = Mock()
        context = Mock()
        context.wrap_socket.return_value = wrapped_socket
        with (
            patch.object(utils.ssl, "create_default_context", return_value=context),
            patch.object(utils.socket, "create_connection", return_value=raw_socket) as create_connection,
        ):
            connection = utils._PinnedHTTPSConnection("example.com", PUBLIC_ADDRESS, 443, 3)
            connection.connect()

        create_connection.assert_called_once_with((PUBLIC_ADDRESS, 443), 3, None)
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="example.com")
        self.assertIs(connection.sock, wrapped_socket)

    def test_rejects_unsupported_binary_and_encoded_content(self):
        for response, expected in (
            (FakeResponse(b"\x89PNG", content_type="image/png"), "Unsupported response content type"),
            (FakeResponse(b"compressed", headers={"Content-Encoding": "gzip"}), "Unsupported content encoding"),
        ):
            with self.subTest(expected=expected):
                result, _connection = run_fetch(response)
            self.assertIn(expected, result)

    def test_content_length_guard_runs_before_body_read(self):
        response = FakeResponse(b"small", headers={"Content-Length": str(utils.WEB_FETCH_MAX_BYTES + 1)})
        result, _connection = run_fetch(response)
        self.assertIn("Response is too large", result)
        self.assertEqual(response.offset, 0)

    def test_pre_cancelled_fetch_stops_before_connecting(self):
        cancelled = threading.Event()
        cancelled.set()
        with (
            patch.object(utils.socket, "getaddrinfo", return_value=[]) as resolve,
            patch.object(utils, "_open_pinned_web_connection") as connect,
        ):
            result = utils.tool_web_fetch("https://example.com", cancel_event=cancelled)
        self.assertIn("cancelled", result.lower())
        connect.assert_not_called()
        resolve.assert_called_once()

    def test_native_dispatch_propagates_cancellation(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(utils.socket, "getaddrinfo", return_value=[]):
            result = agent.execute_tool(
                "web_fetch", {"url": "https://example.com"}, cancel_event=cancelled
            )
        self.assertIn("cancelled", result.lower())

    def test_expired_total_deadline_is_enforced(self):
        with patch.object(utils.time, "monotonic", side_effect=[100.0, 100.0 + utils.WEB_FETCH_TOTAL_TIMEOUT + 1]):
            result = utils.tool_web_fetch("https://example.com")
        self.assertIn("total timeout", result)


class WebFetchExtractionTests(unittest.TestCase):
    def test_extracts_readable_html_and_marks_it_untrusted(self):
        body = b"""<html><head><title>Example</title><style>secret</style></head>
        <body><nav>Menu</nav><main><h1>Hello</h1><p>Useful content.</p>
        <script>ignore()</script><ul><li>First</li></ul></main></body></html>"""
        result, _connection = run_fetch(FakeResponse(body, content_type="text/html; charset=utf-8"))
        self.assertIn("WEB FETCH (UNTRUSTED EXTERNAL CONTENT)", result)
        self.assertIn("Title: Example", result)
        self.assertIn("# Hello", result)
        self.assertIn("Useful content.", result)
        self.assertNotIn("Menu", result)
        self.assertNotIn("ignore()", result)
        self.assertNotIn("secret", result)

    def test_formats_json_honors_character_limit_and_redacts_query(self):
        response = FakeResponse(b'{"message":"' + b"x" * 1000 + b'"}', content_type="application/json")
        result, _connection = run_fetch(
            response, url="https://example.com/data?token=secret#fragment", max_chars=500
        )
        self.assertIn('"message":', result)
        self.assertIn("Truncated: true", result)
        self.assertIn("?REDACTED", result)
        self.assertNotIn("secret", result)
        self.assertNotIn("fragment", result)

    @unittest.skipIf(utils.fitz is None, "PyMuPDF is not installed")
    def test_pdf_extraction_obeys_page_limit(self):
        document = utils.fitz.open()
        for page_number in range(3):
            page = document.new_page()
            page.insert_text((72, 72), f"Page {page_number + 1}")
        data = document.tobytes()
        document.close()

        with patch.object(utils, "WEB_FETCH_MAX_PDF_PAGES", 2):
            _title, text, truncated = utils._extract_web_content(
                {"Content-Type": "application/pdf"}, data, "auto", 10_000
            )
        self.assertIn("Page 1", text)
        self.assertIn("Page 2", text)
        self.assertNotIn("Page 3", text)
        self.assertTrue(truncated)


if __name__ == "__main__":
    unittest.main()
