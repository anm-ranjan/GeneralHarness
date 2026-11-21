---
name: thread-handoff
description: >-
  Start a new thread in a project and give it its first instructions, for example to hand the next milestone to a fresh agent. Use it when the user asks to start, open or hand work to a new thread.
---

# Thread handoff

Start a new thread in one of the app's projects and send it its first message. The thread then runs on its own, like one the user started.

## How the app shows threads

- The sidebar lists each **project** with its **threads** directly under it. There is no folder level in between.
- **Labels** are tags on threads; the sidebar can filter by them. The API stores them as a project's `tasks`, and `task_id` on a thread is its main label (`General` by default). They are not folders: never tell the user to expand a label to find a thread.
- A new thread appears in the sidebar by itself, at the top of its project. The user does not need to reload.

## How to call it

The calls go to this machine's local agent backend at `API`: the "Local API" address in your instructions, else `$AGENT_HOST_API` when it is set, else `http://127.0.0.1:8420`. If your shell commands run in a sandbox that blocks local network access, run these calls outside it from the first attempt.

1. Find the project id. Use the project of the current thread unless the user names another one:

```bash
curl -sS "$API/api/sessions" | python3 -c '
import json,sys
for p in json.load(sys.stdin)["projects"]: print(p["id"], "|", p["name"], "|", p.get("root",""))'
```

2. Create the thread. `provider` is `codex-app-server` (Codex), `claude-agent` (Claude) or `native`. Use the current thread's provider unless the user asks for another; ACP providers such as Antigravity are refused here. Leave `task_id` empty for the default label.

```bash
curl -sS -X POST "$API/api/sessions" -H 'Content-Type: application/json' \
  -d '{"project_id": "<project id>", "task_id": "", "title": "<short title>", "provider": "codex-app-server"}'
```

The reply is the new thread; its `id` looks like `ses_20261121_212021_b292`.

3. Send the first message. Write it as complete instructions: the new thread starts with none of this conversation.

```bash
python3 - "$API" "<thread id>" <<'PY'
import json, sys, urllib.request
api, sid = sys.argv[1], sys.argv[2]
text = """<the instructions>"""
req = urllib.request.Request(f"{api}/api/sessions/{sid}/message",
    data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req, timeout=60)))
PY
```

A reply with `"status": "started"` or `"queued"` means it is running.

4. Tell the user the thread's title and project, for example: "Started **M4 implementation** under **<project name>**; it's at the top of that project in the sidebar."

## Limits

- Approvals and questions from the new thread go to the user. Do not answer them through the API; those calls are refused.
- A `403` reply means this kind of thread may not be started from here. Tell the user and offer the instructions so they can start it themselves.
