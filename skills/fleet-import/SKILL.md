# Fleet import

Copy a folder from another MyHarness host (for example the Macbook or Jarvis) into a new folder in this session's workspace. Use it when the user asks to bring code or files over from another machine.

## How to call it

The request goes to this machine's own MyHarness backend, which does the copy over SSH. You do not need SSH access yourself, and you must not run `ssh` or `rsync` directly for this.

Your prompt gives the session id and the local API address ("MyHarness session id: ... Local API: ..."). A native session has its id as `session_id` in the MYHARNESS SESSION IDENTITY line; if no API address is given, use `http://127.0.0.1:8420`. Use them:

```bash
curl -sS -X POST "$API/api/sessions/$SESSION_ID/fleet-import" \
  -H 'Content-Type: application/json' \
  -d '{"host": "mac", "source_path": "/Users/animesh/Documents/Scripting/MyHarness", "dest_name": "Electron_App"}'
```

- `host` is the fleet host id: `mac` for the Macbook, `jarvis` for Jarvis. List the other hosts and whether they are online with `curl -sS "$API/api/fleet/bridge"`.
- `source_path` is an absolute path on that host. It must be inside the paths that host allows.
- `dest_name` is a single new folder name. It is created inside this session's workspace and must not exist yet. Nothing is ever overwritten.
- `include_everything` (optional, default `false`): by default only source files are copied, meaning the git-tracked files for a repository, otherwise everything except `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `dist` and `target`. Set it to `true` only if the user asks for the full folder.

The user is asked to approve every import, with the host, source, destination and size shown. Wait for the answer. The call returns when the copy is done:

```json
{"path": ".../Electron_App", "source": "...", "host": "mac", "file_count": 245, "size": "3.0 MB", "mode": "git"}
```

## Errors

An error response has a `detail` message. The common ones:

- The other host is Off or Unreachable: ask the user to bring it online in Settings -> App -> Hosts. Do not try to start it yourself.
- The source is outside that host's allowed paths, or is not a folder: tell the user and ask for another path.
- The destination folder already exists: choose another `dest_name` or ask.
- "The import was declined": the user said no. Do not retry.

Pull only. This cannot send files to another host.
