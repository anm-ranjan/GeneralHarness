"""A scripted ACP agent for tests: JSON-RPC over stdio, shaped like `agy acp` 1.5.13.

Behaviour comes from argv: ``fake_acp_agent.py acp [--no-auth] [--log FILE]``.
Prompts select the scenario by keyword:
- "edit"    asks permission for an edit, then reports a diff (or stops if denied)
- "slow"    streams until session/cancel, then resolves cancelled
- "crash"   exits mid-turn
- otherwise thinks, replies "Hello from fake" and ends the turn.
Every received message is appended to the --log file as one JSON line.
"""
import json
import os
import sys
import threading
import time
import uuid

args = sys.argv[1:]
NO_AUTH = "--no-auth" in args
NO_RESUME = "--no-resume" in args  # advertise load only
RESUME_FAILS = "--resume-fails" in args  # resume errors, load works
LOG = args[args.index("--log") + 1] if "--log" in args else ""
write_lock = threading.Lock()
sessions: dict[str, dict] = {}
cancelled: set[str] = set()
pending: dict[int, dict] = {}
next_id = [1000]
MODELS = [
    ("gemini-4-neon-high", "Gemini 4 Neon (High)"),
    ("gemini-4-neon-low", "Gemini 4 Neon (Low)"),
    ("gemini-4-argon-medium", "Gemini 4 Argon (Medium)"),
    ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
    ("claude-opus-5-5-high", "Claude Opus 5.5 (High)"),
]


def log(message):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps(message) + "\n")


def send(message):
    with write_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def update(session_id, payload):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": payload}})


def options(state):
    return [
        {"id": "model", "currentValue": state["model"], "options": []},
        {"id": "thought_level", "currentValue": state["thought_level"], "options": []},
        {"id": "mode", "currentValue": state["mode"], "options": []},
    ]


def ask(session_id, tool_call):
    """Send a permission request and wait for its answer."""
    next_id[0] += 1
    request_id = next_id[0]
    event = {"done": threading.Event()}
    pending[request_id] = event
    send({"jsonrpc": "2.0", "id": request_id, "method": "session/request_permission", "params": {
        "sessionId": session_id, "toolCall": tool_call,
        "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "deny", "name": "Deny", "kind": "reject_once"}],
    }})
    event["done"].wait(30)
    return event.get("result") or {}


def prompt(request_id, params):
    session_id = params["sessionId"]
    text = "".join(block.get("text", "") for block in params.get("prompt", []))
    state = sessions[session_id]
    state["prompts"].append(text)
    usage = {"inputTokens": 100, "outputTokens": 20, "cachedReadTokens": 50, "cachedWriteTokens": 0,
             "thoughtTokens": 5, "totalTokens": 170}
    update(session_id, {"sessionUpdate": "available_commands_update", "availableCommands": [
        {"name": "goal", "description": "g"}, {"name": "schedule", "description": "s"}]})
    if "crash" in text:
        os._exit(3)
    if "slow" in text:
        update(session_id, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Counting"}})
        for _ in range(200):
            if session_id in cancelled:
                cancelled.discard(session_id)
                send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "cancelled", "usage": usage}})
                return
            time.sleep(0.05)
        send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn", "usage": usage}})
        return
    if "picture" in text:
        # Produce a file by a shell command, not the edit tool (as agy did with sips).
        call = {"sessionUpdate": "tool_call", "toolCallId": "call_img", "status": "in_progress", "kind": "execute",
                "title": "sips", "rawInput": {"CommandLine": "sips -z 512 512 media.jpg --out fox.png"}}
        update(session_id, call)
        with open(os.path.join(state["cwd"], "fox.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\nfox")
        update(session_id, {**call, "sessionUpdate": "tool_call_update", "status": "completed"})
        update(session_id, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Saved fox.png"}})
        send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn", "usage": usage}})
        return
    if "question" in text:
        # Ask through MyHarness's MCP server, as Antigravity would: permission, then tools/call.
        server = next((m for m in state["mcp"] if m.get("name") == "myharness"), None)
        call = {"sessionUpdate": "tool_call", "toolCallId": "call_q", "status": "pending", "kind": "other",
                "name": "mcp_myharness_ask_user", "title": "Run mcp_myharness_ask_user",
                "rawInput": {"question": "Which colour?"}}
        update(session_id, call)
        answer_text = "no server"
        if server and (ask(session_id, call).get("outcome") or {}).get("optionId") == "allow":
            import subprocess
            env = dict(os.environ, **{e["name"]: e["value"] for e in server.get("env", [])})
            proc = subprocess.Popen([server["command"], *server["args"]], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, text=True, env=env)
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "ask_user", "arguments": {"question": "Which colour?", "options": ["red", "blue"]}}}) + "\n")
            proc.stdin.flush()
            proc.stdout.readline()
            answer_text = json.loads(proc.stdout.readline())["result"]["content"][0]["text"]
            proc.terminate()
        update(session_id, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": answer_text}})
        send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn", "usage": usage}})
        return
    if "edit" in text:
        call = {"sessionUpdate": "tool_call", "toolCallId": "call_1", "status": "in_progress", "kind": "edit",
                "title": "File ../../x/notes.txt", "locations": [{"path": "/tmp/fake/notes.txt"}],
                "rawInput": {"TargetFile": "/tmp/fake/notes.txt"}}
        update(session_id, call)
        update(session_id, {**call, "sessionUpdate": "tool_call_update", "status": "pending"})
        answer = ask(session_id, {**call, "status": "pending"})
        outcome = answer.get("outcome") or {}
        if outcome.get("optionId") != "allow":
            update(session_id, {**call, "sessionUpdate": "tool_call_update", "status": "failed"})
            send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn", "usage": usage}})
            return
        update(session_id, {**call, "sessionUpdate": "tool_call_update", "status": "completed",
                            "content": [{"type": "diff", "path": "/tmp/fake/notes.txt",
                                         "oldText": "a\n", "newText": "a\nb\n"}]})
    update(session_id, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "Thinking it over."}})
    update(session_id, {"sessionUpdate": "plan", "entries": [
        {"content": "Say hello", "status": "completed", "priority": "high"}]})
    for piece in ("Hello ", "from fake"):
        update(session_id, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": piece}})
    update(session_id, {"sessionUpdate": "usage_update", "used": 2000, "size": 100000})
    send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn", "usage": usage}})


def new_state(params):
    return {"cwd": params.get("cwd"), "model": "gemini-4-neon", "thought_level": "high", "mode": "default",
            "meta": params.get("_meta"), "prompts": [], "mcp": params.get("mcpServers") or []}


def handle(message):
    log(message)
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if method is None and request_id in pending:
        pending[request_id]["result"] = message.get("result")
        pending[request_id]["done"].set()
        return
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": 1,
            "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": True}, "sessionCapabilities": {
                "load": {}, "close": {}, **({} if NO_RESUME else {"resume": {}})}},
            "agentInfo": {"name": "Fake ACP", "version": "9.9.9"},
            "authMethods": [{"id": "oauth-personal", "name": "Log in with Google"}],
        }})
    elif method == "session/list":
        if NO_AUTH:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "Authentication required"}})
        else:
            send({"jsonrpc": "2.0", "id": request_id, "result": {"sessions": [
                {"sessionId": sid, "cwd": st["cwd"]} for sid, st in sessions.items()]}})
    elif method == "session/resume":
        session_id = params["sessionId"]
        if NO_RESUME:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
        elif RESUME_FAILS or session_id.startswith("gone"):
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Session not found"}})
        else:
            sessions.setdefault(session_id, new_state(params))
            update(session_id, {"sessionUpdate": "available_commands_update", "availableCommands": [
                {"name": "goal", "description": "g"}, {"name": "swarm", "description": "s"}]})
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "sessionId": session_id, "configOptions": options(sessions[session_id])}})
    elif method in ("session/new", "session/load"):
        if NO_AUTH:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "Authentication required"}})
            return
        if method == "session/load":
            session_id = params["sessionId"]
            if session_id.startswith("gone"):
                send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Session not found"}})
                return
            sessions.setdefault(session_id, new_state(params))
            update(session_id, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "old"}})
            update(session_id, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "replayed"}})
            send({"jsonrpc": "2.0", "id": request_id, "result": {"configOptions": options(sessions[session_id])}})
            return
        session_id = f"s-{uuid.uuid4().hex[:8]}"
        sessions[session_id] = new_state(params)
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "sessionId": session_id,
            "models": {"availableModels": [{"modelId": m, "name": n} for m, n in MODELS],
                       "currentModelId": "gemini-4-neon-high"},
            "configOptions": options(sessions[session_id]),
        }})
    elif method == "session/set_config_option":
        state = sessions[params["sessionId"]]
        state[params["configId"]] = params["value"]
        send({"jsonrpc": "2.0", "id": request_id, "result": {"configOptions": options(state)}})
    elif method == "session/prompt":
        threading.Thread(target=prompt, args=(request_id, params), daemon=True).start()
    elif method == "session/cancel":
        cancelled.add(params.get("sessionId"))
    elif method == "session/close":
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    elif request_id is not None:
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})


for line in sys.stdin:
    try:
        handle(json.loads(line))
    except json.JSONDecodeError:
        continue
