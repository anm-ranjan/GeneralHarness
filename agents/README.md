# Routine agents

An **agent** is a reusable definition in `agents/<name>/` (bundled with the checkout) or `data/agents/<name>/` (yours, installed by `/create_agent`); a **run** is an ordinary native session with `kind: agent_run`. These are separate from MyHarness's native subagents (`spawn_agent`, `agent_event`, `AgentsPanel`). Agents cannot start agents autonomously or schedule jobs.

Open **Agents** using the third sidebar view button. Choose a definition, enter parameters or describe the task, then select **Start run** or **Dry run**. `@site-check public` starts a separate run from any session, even while that session is busy. A linked card offers approvals, Stop, Open run, and Add result to chat. Card content is never inserted into the parent model's context; adding a result fills a draft for the user to send.

`/create_agent <intent>` in an idle native session creates a draft for review. Inspect its files and validation errors, revise it, optionally dry-run it, and save it. Dry runs always ask approval with the code shown. Saved drafts go to `data/agents/<name>/`, which deployment syncs keep (the checkout's `agents/` folder is replaced by every sync). A user definition with the same name as a bundled one shadows it, and saving one needs the same explicit replace confirmation. Replacing an existing user definition first copies it to `data/agent_backups/<name>/<timestamp>/`. Browser users can view installed files; Electron users can edit them with conflict detection. No restart is needed.

## Definition format

A definition contains `AGENT.md`, optionally `run.py`, and any local resources. `_lib/` and folders starting with `_` or `.` are not definitions. `MYHARNESS_AGENTS_DIR` overrides the bundled root and `MYHARNESS_USER_AGENTS_DIR` the user root, for tests or another installation. Invalid definitions remain visible with errors.

```yaml
---
name: example-agent
version: 1
description: Describe one reusable task in one line
entry: run.py
dry_run: true
approval: shell_only
tools: [run_agent_script, file_list]
max_iterations: 12
timeout_seconds: 3600
on_stop: {grace_seconds: 5}
params:
  paths: {type: paths, required: true, kind: dir, label: Input folders}
  stagger: {type: float, default: 2, min: 0, max: 60}
---
Turn the request into script parameters. Report only observed results.
```

| Field | Rules / default |
|---|---|
| `name` | Required; equals folder; `^[a-z0-9][a-z0-9-]{0,63}$` |
| `description` | Required, one line |
| `version` | Positive integer; default 1; recorded on runs |
| `entry` | Optional Python file inside this folder; no traversal or symlink escape |
| `python` | Optional interpreter; default backend `sys.executable` |
| `params` | Ordered parameter map; default `{}` |
| `dry_run` | Boolean; default false |
| `approval` | `inherit`, `always_ask`, `shell_only` (default), `auto_approve` |
| `max_iterations` | Default 12 |
| `reasoning_effort` | Optional native per-run setting |
| `tools` | Allowlist; default `run_agent_script`, `file_read`, `file_list`, `content_search`; unknown names are errors; script tool removed without entry |
| `working_directory` | Optional default; must be allowed and existing |
| `timeout_seconds` | Default 3600; maximum 86400 |
| `on_stop.grace_seconds` | Default 5; range 0–30 |
| `uses_skills` | Optional skill-name list; only these skills can be listed/read |
| `platform` | Optional list of `windows`, `macos`, `linux` |
| `requires` | Preflight checks described below |

Working directory: explicit caller directory, then manifest default, then private `data/agent_runs/<session_id>/`. A linked invocation inherits the calling session's effective directory. Runs receive no project instructions or memory. They cannot move into projects or change providers. Archiving, labels, search, export and follow-ups work normally. Backups include the sandbox; importing agent-run backups is rejected.

## Parameters

| Type | JSON / form | Extra rules |
|---|---|---|
| `string` | String / input | `pattern` (full regex match), `max_length` |
| `text` | String / textarea | `max_length` |
| `int`, `float` | Number / input | `min`, `max`; booleans are not numbers |
| `bool` | Boolean / checkbox | |
| `enum` | String / select | Required `options` list of strings |
| `path` | String / input and picker | `kind: file\|dir\|any`, `must_exist: true` by default |
| `paths` | String list / one path per line and picker | Path rules; `max_items` default 200 |
| `strings` | String list / one per line | `pattern`, `max_items` default 200 |

Common keys: `required` (false), `default`, `label`, `help`.

**Choices from the script.** A `string` parameter can declare `options_from: script` (requires `entry`) and optionally `options_depends: [other, params]` (non-path parameters only). When the form opens, and again when a listed value changes, the backend runs `[python, entry, "--params", tempfile, "--options", <param>]` with no shell, a 15 s limit and a temp working directory, passing only the `options_depends` values. The script calls `options_request()` to see which parameter is asked for and replies with `options([...])` (strings or `{value, label}`), then exits; it must be fast and read-only, because it runs without approval. Up to 500 choices are kept. The form shows a select with Any/Choose… and Reload; if listing fails it falls back to a text input with the error (from the last `log` record or stderr). The value stays free text: the list is a convenience, never validation, so the script should check the value itself. Manual use: `python run.py --options <param>` prints the choices. The backend is authoritative: types, bounds, required values, enum membership, patterns, allowed paths and existence are checked. Paths become absolute. Anything stricter (for example a string that must parse as JSON) is the script's job.

## Python helper and protocol

The standard-library-only helper `_lib/myharness_agent.py` supports Python 3.8+. The backend prepends `_lib` to `PYTHONPATH`. For manual invocation, bundled scripts add it themselves.

```python
from myharness_agent import load_params, dry_run, progress, item, result, log, stopping, on_stop
params = load_params(defaults={"stagger": 2})
progress(0, 1, "Starting")
item("folder", "running", label="Run A")
# Do the deterministic work, checking stopping() between units.
item("folder", "ok", detail="Observed outcome", data={"job_id": "123"})
result("1 of 1 completed", table={"columns": ["Folder", "Status"], "rows": [["A", "ok"]]})
```

Invocation: `[python, entry, "--params", tempfile]`, optionally `--dry-run`, with no shell. Manual use supports repeated `--set key=value`; JSON values are decoded, other values stay strings. `MYHARNESS_AGENT=1` selects flushed JSON lines; otherwise output is readable text.

Each JSON record has a string `wj` discriminator:

- `progress`: `done` integer, `total` integer or null, `label`; latest wins, at most four emitted events per second plus the final update.
- `item`: required string `key`, `status` (`pending`, `running`, `ok`, `failed`, `skipped`, `info`), optional `label`, `detail`, object `data`. Same key replaces the earlier item.
- `result`: string `summary`, optional `table: {columns: [...], rows: [[...]]}`, optional `markdown`. Latest wins; emitted after process termination with status and counts. Markdown can use the normal sandboxed HTML/Chart.js renderer.
- `log`: `text`, `level: info|warn|error`. Other stdout and all stderr are logs.

Limits: 64 KB per line, 500 distinct items, 1000 table rows, 100 KB markdown, 2 MB log ring. Keep large outputs in files. The model receives compact status, exit code, item details, summary and the last 40 log lines; tables/Markdown are not repeated in full.

Environment includes `MYHARNESS_AGENT_NAME`, `MYHARNESS_AGENT_DIR`, `MYHARNESS_RUN_ID`, `MYHARNESS_SKILL_DIRS` (JSON skill-name → directory), `MYHARNESS_ALLOWED_PATHS` (JSON roots), unbuffered UTF-8 output, and `MYHARNESS_CHART_JS_URL` (the bundled offline Chart.js URL). Use `allowed_path()` for expanded output paths and keep application-specific inputs unchanged.

Cancellation sends SIGTERM to the POSIX process group or CTRL_BREAK_EVENT to the Windows process group. The helper sets `stopping()` and calls registered `on_stop(callback)` functions once; keep callbacks short. The runner continues reading through the grace period, then kills the tree (SIGKILL / Windows Job Object). Outcomes: `completed`, `failed`, `stopped`, `timed_out`. Pending/running items remain as last reported. Stopping a run does not stop its parent or undo externally submitted jobs. Windows Job Object assignment has a small process-spawn race; test on deployment.

## Preflight

`GET /api/agents/<name>/preflight` returns `{check, ok, message}` records. Failed checks block creation and follow-ups and appear on the agent home page. Checks:

```yaml
requires:
  - {python_module: numpy}           # import with the agent's interpreter
  - {env: MY_TOOL_HOME}
  - {file: '${MY_TOOL_HOME}/bin/tool'}
  - {url: 'http://127.0.0.1:8420/api/health'}   # bounded HTTP GET
  - {python: '3.13', arch: x64}
```

## Bundled examples

- `folder-summary`: file counts, sizes, common types and largest files of one or more folders. Read-only, with a dry run.
- `site-check`: status codes and response times of web addresses. The Saved group field is filled from `sites.json` through `options_from: script`.

`/create_agent` shows both to the model as worked examples.
