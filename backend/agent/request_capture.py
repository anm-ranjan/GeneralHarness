"""Opt-in native HTTP captures. No headers, credentials or config dumps.

Messages are independently content-addressed: prefixes reuse the same blobs,
without depending on earlier requests. Image URLs retain their exact spelling
and the decoded bytes are archived as well. All references are hash checked.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit, urlunsplit

_scope = ContextVar("native_request_capture", default=None)
_log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_capture(operation, *args, **kwargs):
    """A capture failure must not fail a model call, and must be observable."""
    try:
        return operation(*args, **kwargs)
    except Exception as exc:
        # Exception text can contain workspace contents or response data.
        _log.warning("Native request capture failed (%s)", type(exc).__name__)
        return None


def bind(scope):
    return _scope.set(scope)


def reset(token):
    _scope.reset(token)


class CaptureStore:
    def __init__(self, root):
        self.root = Path(root)

    def put(self, value):
        return self.put_bytes(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def put_bytes(self, data):
        digest = hashlib.sha256(data).hexdigest()
        directory = self.root / "blobs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / digest
        if not path.exists():
            tmp = directory / ("." + uuid.uuid4().hex)
            try:
                tmp.write_bytes(data)
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        return digest

    def read_bytes(self, digest):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid capture blob reference")
        data = (self.root / "blobs" / digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Capture blob hash mismatch")
        return data

    def read(self, digest):
        return json.loads(self.read_bytes(digest))

    def pack(self, value):
        # Tag every container to avoid collisions with user-supplied dicts.
        if isinstance(value, dict):
            return ["dict", [[k, self.pack(v)] for k, v in value.items()]]
        if isinstance(value, list):
            return ["list", [self.pack(v) for v in value]]
        if isinstance(value, str) and value.startswith("data:image/") and ";base64," in value:
            header, encoded = value.split(",", 1)
            return ["image", self.put(value), self.put_bytes(base64.b64decode(encoded)), header[5:].split(";")[0]]
        return ["value", value]

    def unpack(self, value):
        kind = value[0]
        if kind == "dict":
            return {k: self.unpack(v) for k, v in value[1]}
        if kind == "list":
            return [self.unpack(v) for v in value[1]]
        if kind == "image":
            self.read_bytes(value[2])
            return self.read(value[1])
        if kind == "value":
            return value[1]
        raise ValueError("Unknown capture encoding")

    def payload(self, payload):
        body = dict(payload)
        messages = body.pop("messages", None)
        return {"body": self.put(self.pack(body)), "messages": None if messages is None else
                [self.put(self.pack(message)) for message in messages]}

    def reconstruct(self, refs):
        body = self.unpack(self.read(refs["body"]))
        if refs["messages"] is not None:
            body["messages"] = [self.unpack(self.read(ref)) for ref in refs["messages"]]
        return body

    def record(self, record):
        directory = self.root / "records"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (uuid.uuid4().hex + ".json")
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"schema": "myharness-request-capture-v1", "timestamp": now(), **record},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


class CaptureScope:
    def __init__(self, store, identity, emit=None):
        self.store, self.identity, self.emit = store, dict(identity), emit

    def record(self, kind, **data):
        record = {**self.identity, "kind": kind, **data}
        self.store.record(record)
        if self.emit:
            # Only metadata enters transcript events; never raw payloads.
            self.emit({k: v for k, v in record.items() if k not in {"payload", "response", "settings"}})

    def outcome(self, reason, kind="run_outcome"):
        status = "interrupted" if reason == "interrupted" else "completed" if reason == "completed" else "failed"
        self.record(kind, status=status, reason=reason, quality=None, quality_source=None)


class CaptureCall:
    def __init__(self, scope, purpose):
        self.scope, self.purpose = scope, purpose
        self.request_id = "req_" + uuid.uuid4().hex
        self.attempt = 0

    def post(self, post, url, **kwargs):
        self.attempt += 1
        identity = {"request_id": self.request_id, "attempt_id": "attempt_" + uuid.uuid4().hex,
                    "attempt": self.attempt, "purpose": self.purpose}
        parts = urlsplit(url)
        # Omit URL userinfo/query and all HTTP headers.
        endpoint = urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
        safe_capture(self.scope.record, "request", **identity, endpoint=endpoint,
                     payload=safe_capture(self.scope.store.payload, kwargs["json"]))
        try:
            response = post(url, **kwargs)
        except Exception as exc:
            safe_capture(self.scope.record, "response", **identity, status="transport_error", error_type=type(exc).__name__)
            raise
        return CapturedResponse(response, self.scope, identity, kwargs.get("stream", False))


def post(call, sender, *args, **kwargs):
    return call.post(sender, *args, **kwargs) if call else sender(*args, **kwargs)


def start_call(purpose="agent"):
    scope = _scope.get()
    return CaptureCall(scope, purpose) if scope is not None else None


class CapturedResponse:
    def __init__(self, response, scope, identity, streamed):
        self._response, self._scope, self._identity = response, scope, identity
        self._streamed, self._done = streamed, False
        self._lines = []

    def __getattr__(self, name):
        return getattr(self._response, name)

    def _save(self, status, response):
        if not self._done:
            self._done = True
            safe_capture(self._scope.record, "response", **self._identity, http_status=self.status_code,
                         status=status, response=safe_capture(self._scope.store.put, response))

    @property
    def text(self):
        text = self._response.text
        self._save("http_error" if self.status_code != 200 else "received", {"format": "text", "text": text})
        return text

    def json(self):
        try:
            result = self._response.json()
        except Exception:
            self._save("invalid_json", {"format": "text", "text": self._response.text})
            raise
        self._save("received", {"format": "json", "body": result})
        return result

    def iter_lines(self, *args, **kwargs):
        try:
            for line in self._response.iter_lines(*args, **kwargs):
                self._lines.append(base64.b64encode(line if isinstance(line, bytes) else line.encode("utf-8")).decode("ascii"))
                yield line
        except Exception:
            self._save("stream_error", {"format": "sse_lines_base64", "lines": self._lines})
            raise

    def finish_stream(self, assembled, cancelled=False):
        self._save("interrupted" if cancelled else "received" if assembled.get("finish_reason") else "partial",
                   {"format": "sse_lines_base64", "lines": self._lines, "assembled": assembled})

    def close(self):
        if not self._done:
            if self.status_code != 200:
                # Do not introduce a new network read on rejected streams.
                body = {"format": "unread", "reason": "HTTP rejection before response body consumption"}
                self._save("http_error", body)
            else:
                self._save("partial", {"format": "sse_lines_base64", "lines": self._lines})
        self._response.close()
