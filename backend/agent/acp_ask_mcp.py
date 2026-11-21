"""Stdio MCP server that lets an ACP agent ask the MyHarness user a question.

The ACP agent (e.g. Antigravity) starts this script for a thread, given in
``session/new``/``resume`` as the ``myharness`` MCP server. Its one tool,
``ask_user``, forwards the question to the MyHarness backend over a local
socket and returns the user's answer. The socket's port and token are read
from MYHARNESS_ASK_FILE at call time, so the tool keeps working after the
backend restarts; MYHARNESS_SESSION_ID names the thread to ask in.

Standard library only: it runs under the backend's own Python.
"""
from __future__ import annotations

import json
import os
import socket
import sys

TOOL = {
    "name": "ask_user",
    "description": (
        "Ask the user one question in MyHarness and wait for the answer. Use it only when the "
        "request is genuinely ambiguous and the readings lead to materially different work; "
        "never for anything you can settle by reading the code. Give 2-5 short options when "
        "the choices are known; the user can also type their own answer."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "One concrete question."},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Suggested answers."},
        },
        "required": ["question"],
    },
}
NO_ANSWER = ("No answer: the user did not respond. Proceed on your best judgement and state the "
             "assumption you made.")


def ask(question: str, options: list[str]) -> str:
    try:
        with open(os.environ["MYHARNESS_ASK_FILE"], encoding="utf-8") as fh:
            endpoint = json.load(fh)
        with socket.create_connection(("127.0.0.1", int(endpoint["port"])), timeout=10) as conn:
            conn.settimeout(None)  # the user may take a while
            request = {
                "token": endpoint["token"],
                "session_id": os.environ.get("MYHARNESS_SESSION_ID", ""),
                "question": question,
                "options": options,
            }
            conn.sendall((json.dumps(request) + "\n").encode("utf-8"))
            reply = b""
            while not reply.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                reply += chunk
        answer = json.loads(reply or b"{}").get("answer")
    except (OSError, ValueError, KeyError):
        return "MyHarness could not show the question. " + NO_ANSWER
    return f"The user answered: {answer}" if answer and str(answer).strip() else NO_ANSWER


def respond(message_id, result=None, error=None) -> None:
    message = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        method, message_id = message.get("method"), message.get("id")
        if message_id is None:
            continue  # notifications such as notifications/initialized
        params = message.get("params") or {}
        if method == "initialize":
            respond(message_id, {
                "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "myharness", "version": "1"},
            })
        elif method == "tools/list":
            respond(message_id, {"tools": [TOOL]})
        elif method == "tools/call" and params.get("name") == TOOL["name"]:
            arguments = params.get("arguments") or {}
            question = str(arguments.get("question") or "").strip()
            raw = arguments.get("options")
            options = [str(o) for o in raw if str(o).strip()] if isinstance(raw, list) else []
            text = ask(question, options) if question else "ERROR: a question is required."
            respond(message_id, {"content": [{"type": "text", "text": text}]})
        elif method == "ping":
            respond(message_id, {})
        else:
            respond(message_id, error={"code": -32601, "message": f"{method} is not supported"})


if __name__ == "__main__":
    main()
