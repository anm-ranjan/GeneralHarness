# MyHarness Agent Guide

Repository guide for coding agents working on MyHarness itself. For what the
product does and how to install it, see `README.md`.

MyHarness is a self-hosted harness for coding agents: a FastAPI/WebSocket
backend with a persistent session store, a provider-independent agent loop, and
four clients (React web UI, Electron shell, Rust TUI, CLI).

## Architecture

### Backend (`backend/`)

- `web_app.py` — application startup, shared runtime state (`_store`,
  `_DATA_DIR`, `_STATIC_DIR`), router registration, and `main()`. `main()`
  resolves the bind address as `MYHARNESS_WEB_HOST/PORT` → `server.host/port` →
  `127.0.0.1:8420`, and prints the startup warnings from
  `utils.print_startup_warnings()`.
- `web_routes/` — route groups, all mounted onto the same app:
  - `system.py` — `/api/health`, `/api/shutdown`, `/api/codex/status`,
    `/api/claude/status`, `/api/project`, `/api/browse`, `/api/files/image`,
    `/api/audio/transcribe`.
  - `sessions.py` — the project/task/session/chat tree, search, export, backup
    and import, metrics, attachments, rename/move/delete.
  - `runs.py` — the global `/api/events` socket, the per-session event socket,
    message submission, approvals, queue management, cancellation.
  - `workspace.py` — file tree, previews, saves, diffs, reverts, git status/diff
    and (opt-in) git stage/commit.
  - `project_knowledge.py` — project instructions and memory notes
    (`/api/projects/{id}/instructions`, `/api/projects/{id}/memory`) and
    "Save to memory" (`/api/sessions/{id}/memory/from-reply`).
- `web_runs.py` — **provider dispatch and run orchestration.** Every run flows
  through here: it picks the provider from `SessionMeta.provider`, builds the
  system prompt, streams events, records change manifests, computes run metrics,
  and implements the idle-only slash commands (`/help`, `/compact`, `/clear`,
  `/chdir`, `/approve`, `/verbose`, `/maxiters`, `/thinking`, `/model`,
  `/remember`).
  `backend/slash_commands.py` is the single command registry (served by
  `GET /api/commands`, printed by `/help`); keep it, `_handle_slash_command`,
  `SLASH_CMDS` in `frontend/src/constants.js` and `command_help_lines` in
  `tui-rs/src/ui.rs` in step.
- `web_session.py` — active-run registry, cancellation, approval futures, and
  WebSocket connection bookkeeping.
- `web_ui_adapter.py` — translates agent UI protocol events into persisted,
  broadcastable WebSocket events.
- `session_store.py` — persistence for projects, tasks, sessions, and events.
- `session_search.py`, `session_export.py`, `session_backup.py` — cross-session
  search, Markdown/HTML export, portable `.myharness.zip` backup and import.
- `audio_transcription.py` — speech to text. `config_from_utils()` builds an
  `AudioConfig` from the `utils` module constants; `transcribe_audio()`
  dispatches to `_transcribe_local` (faster-whisper), `_transcribe_remote`
  (direct paramiko + SSH config), or `_transcribe_api` (OpenAI-compatible endpoint). Errors
  are raised as `HTTPException` with a specific status code.
- `git_status.py` — workspace source-control helpers.
- `web_helpers.py`, `web_models.py`, `web_desktop.py` — shared helpers, Pydantic
  request/response models, and Electron-only gating.

### Agent (`backend/agent/`)

- `harness_agent.py` — the native agent loop: message history, compaction,
  context restore, tool execution, and the CLI entry point.
- `utils.py` — **config loading and tool implementations.** Loads
  `agent_config.yaml` once at import and exposes every setting as a
  module-level constant. Also owns path permissions (`ALLOWED_PATHS`,
  `register_allowed_path`), file/search/shell tools, caching, and
  `validate_startup_config()` / `print_startup_warnings()`.
- `skill_registry.py` — discovers `skills/<name>/SKILL.md`, returns the skills
  catalog, and safely reads one skill for the Native tools and `/skills`.
- `tool_defs.py` — tool JSON schemas.
- `codex_app_server_provider.py` — spawns the `codex` CLI and speaks its
  app-server protocol.
- `claude_agent_provider.py` — drives the `claude` CLI through the Claude Agent
  SDK. Exports `CLAUDE_PROVIDER_ID` (`"claude-agent"`), which is what
  `web_runs.py` compares against — never hardcode the string elsewhere.
- `remote_cli.py` — attaches the CLI to an already-running backend.
- `agent_ui_protocol.py`, `cli_ui.py`, `tui_app.py` — non-web UIs. `tui_app.py`
  is the legacy Textual TUI kept as the `--tui-legacy` fallback.

### Providers

Three provider ids appear in `SessionMeta.provider`:

| id | Module | Auth |
|----|--------|------|
| `native` | `harness_agent.py` | `MYHARNESS_API_KEY` |
| `codex-app-server` | `codex_app_server_provider.py` | `codex login` (subscription) |
| `claude-agent` | `claude_agent_provider.py` | `claude` login (subscription) or `ANTHROPIC_API_KEY` |

Legacy sessions may still carry `codex-cli`; `web_runs.py` refuses to run them
and asks the user to migrate with `/model`. Availability of the two CLI
providers is checked at runtime via `web_app._codex_app_server_available()` and
`web_app._claude_agent_available()` — both require the config flag *and* the
binary on PATH.

### Session store (`data/`, gitignored)

```text
data/
  project_index.json          projects, tasks, session ids (incl. "__chats__")
  sessions/<id>.json          session metadata, queued messages, overrides
  events/<id>.jsonl           append-only event stream
  attachments/                uploaded and pasted images
  chats/<chat_id>/            sandboxed workspace for a project-less chat
  audio/<session>/<turn>/     recorded voice input
  scratchpad.json             global scratch note (Workspace > Notes)
  projects/<project_id>/      project instructions.json and memory.json
```

### Frontend (`frontend/src/`)

- `main.jsx` (bootstraps theming before first render), `App.jsx`,
  `context/AppContext.jsx` for global state, `api.js` for REST/WebSocket calls.
- `eventHandlers.js` is the single place where both replayed and live session
  events become UI state. Any change to stored or streamed events must be
  reflected here *and* in its test, in `tui-rs/src/events.rs`, and in
  `backend/agent/remote_cli.py`.
- Components are grouped by area: `Composer/`, `Sidebar/`, `Stage/`,
  `Workspace/`, `Modals/`, plus `TopBar.jsx`.
- Pure helpers with colocated `*.test.js` files: `search.js`, `utils.js`,
  `runStates.js`, `recentSessions.js`.
- `theme/` derives every CSS custom property from a compact spec and writes it
  as inline styles on `:root`. **Never hardcode a color in a component or in
  CSS** — add a token, or theme switching will strand that element.
- Vite proxies `/api` to `http://127.0.0.1:8420` in dev mode. `frontend/dist/`
  is build output and is gitignored.

### Electron (`electron/`)

- `main.js` connects to an existing backend or starts one, and hosts the app
  window. `app-preload.js` exposes desktop actions to React.
- The window sends `X-MyHarness-Desktop: 1`; desktop-only backend behavior (such
  as `PUT /api/workspace/file`) gates on that header.
- When touching startup or backend-spawn logic, check `run.cmd` too.

### Rust TUI (`tui-rs/`)

- `myharness-tui`, built on ratatui + tokio. It is a **client** of the public
  REST/WebSocket API and never imports the agent.
- `src/api.rs` wraps REST, `src/ws.rs` owns the event stream, `src/events.rs`
  renders events into transcript entries, `src/app.rs` holds state, `src/ui.rs`
  draws, `src/args.rs` parses arguments (precedence: flag > env > default).
- `read_backend_url.py` prints one dotted key from `agent_config.yaml`; the
  launchers use it for `desktop.backend_url`, `server.host`, and `server.port`.
  It parses with PyYAML when available and falls back to a stdlib line scan.
- `target/` is build output and is gitignored.

### Installer (`installer/setup.mjs`)

ESM, run via `npm run setup` or `npx .`. It asks its questions, installs
  dependencies, creates `./.venv`, writes `agent_config.yaml` by string-editing,
  builds the selected clients, and can package Electron for the current OS
`agent_config.example.yaml` so every comment survives. `ConfigEditor`, `findKey`,
and `dedent` are exported for testing; `main()` only runs when the file is
executed directly. When you add a config key, the installer only needs a change
if the user should be asked about it.

## Runtime concepts

- **Projects** map to filesystem roots and must be inside `permissions.allowed_paths`.
- **Tasks** group sessions inside a project.
- **Chats** are project-less sessions (`SessionMeta.kind == "chat"`) in the
  reserved `__chats__` bucket. They use a lighter system prompt, run inside
  `data/chats/<chat_id>/`, and otherwise reuse the whole session pipeline. The
  reserved bucket cannot be renamed or deleted.
- **Per-session overrides** persist provider and working directory; run settings
  override model, iterations, reasoning effort, approval behavior, and verbosity
  without touching process-wide defaults.
- **Approvals** follow `permissions.approval_mode`: `always_ask`, `shell_only`,
  `auto_approve`.
- **Queued messages** are persisted FIFO while a run is active and stay out of
  model context until they become the active turn. Slash commands are idle-only
  and are never queued.
- **Change manifests** record run-level file mutations plus before-content, so a
  file or a whole run can be reverted after a restart, with conflict checks
  against later external edits.
- **Interrupted runs** roll native in-memory context back to the last completed
  turn. Transcript events stay visible, but interrupted user/tool/partial
  assistant content must not re-enter model context. Startup resets sessions
  left as `running` back to `idle`; replay must not recreate live thinking or
  progress indicators for idle sessions.
- **Thinking traces** are stripped from context and shown as collapsible blocks
  only in verbose mode. Plain-text thinking streamed as `reasoning_content` or
  `reasoning` is collected under a private `_reasoning_text` key for display
  only and never sent back to the model.
- **Context accounting** (`backend/agent/history_tokens.py`): native context
  usage is the gateway's own `prompt_tokens` from the latest response (a private
  `_usage_anchor` on the last sent message) plus a local estimate of what
  changed since. Locally, inline images count by pixel size (not base64
  length), `reasoning_details` are not counted, and tool definitions are
  included. This one figure drives the context % display, end-of-turn
  compaction (75% of the window, see `native_settings.py`), the mid-turn guard
  (`memory.context_guard_percent`: compress read tool output, then compact
  completed turns; root thread only) and each request's `max_tokens` =
  min(`limits.max_output_tokens`, window − input − margin). Inconsistent memory
  settings are startup warnings and `/api/health` `config_warnings`. Automatic
  compaction keeps older images; `/compact` (native only) compacts on demand
  and replaces older images with a text note. Streamed requests ask every
  endpoint for usage (`stream_options.include_usage`) and retry once without it
  when a gateway rejects it with 400/422. The root agent's `api_metrics` carry
  `usage_str` after every model call, so the display moves mid-turn (subagents
  and the compaction summary call never move it).
- **Native compaction memory** (`build_native_task_state`): user requests and
  `ask_user` answers are kept verbatim; a valid model summary is kept by
  trimming facts to make room; facts are trimmed reads first, then shell runs
  and writes, with turn reports and failures last. The tool-call checkpoint
  nudge (private `_system_nudge` flag) is never a request. The `compaction`
  event carries the resulting block as `memory`: a collapsible notice in the
  web UI (`CompactionNotice.jsx`), counts in the TUI and CLI.
- **Auto previews**: "View <file>" links and images a run produces are held
  until the turn's reply and added below it only when the reply does not link
  the file or embed the image. A run that ends without a reply still shows them.
- **Display-only formatting**: composer B/I/U/S buttons and Ctrl/Cmd+B/I/U,
  Ctrl/Cmd+Shift+X wrap the selection in `**`, `*`, `++`, `~~`
  (`frontend/src/textFormat.js`). Markers are stripped before sending; the
  marked version travels as optional `display_text` on the request, queued and
  steering items and the `user_message` event and is used only to render the
  web transcript. Shift+Enter continues a list line in the same style.
- **Scratch note**: Workspace > Notes is one note shared by every thread and
  client, stored in `data/scratchpad.json`. Its "Save as Project Memory" /
  "Save as Project Instructions" buttons send the selection (or the whole note)
  to the current project and offer Undo/View/Clear.
- **Project instructions and memory** (`backend/project_knowledge.py`, stored
  in `data/projects/<folder>/instructions.json` and `memory.json`, removed with
  the project; chats have neither). A project id that is not a plain slug maps
  to a readable folder name plus a stable hash. Instructions (a project
  description and/or rules) go into every thread's prompt: native and subagents
  through the `_project_knowledge` key of `web_helpers._session_identity`
  (appended by `session_context.prompt_fragment`, never serialized as
  identity), Claude through `build_system_prompt_append` (rebuilt on every run),
  Codex in the first/fallback prompt and again on resume only when its hash in
  `codex_state.project_knowledge_hash` changed. Memory notes are possibly stale
  reference data, not instructions: native sees pinned bodies plus an index and
  reads the rest with `myharness_context` `memory_list`/`memory_read` (current
  project only); Codex and Claude (`inline` mode) get bodies within a budget.
  Notes are saved only by the user: `/remember [focus]` (the summary model
  drafts one note from the persisted transcript; emits `memory_saved`), "Save to
  memory" under a finished reply (summary model, 30 s timeout, nothing added to
  the transcript), the Notes tab's "Instructions - <project>" and
  "Memory - <project>" views, or the REST API.
- **Transcript images** (user attachments and assistant Markdown images)
  collapse to a name chip. Replayed messages start collapsed, live ones
  expanded; a user toggle is remembered per image in the browser
  (`frontend/src/imageCollapse.js`).
- **Search, export, and metrics** read persisted events as the source of truth —
  keep event changes compatible with all three.
- **File editing** is desktop-only: `PUT /api/workspace/file` requires the
  desktop header, re-checks allowed paths, and 409s when the file changed on
  disk. The browser UI keeps the read-only paged preview.
- **Routine agents** (`AgentProfile`, `backend/agent_profiles.py`) are reusable
  definitions in tracked `agents/<name>/AGENT.md` plus an optional Python entry
  script, and in `data/agents/<name>/` for the user's own (`/create_agent`
  installs there, so deployment syncs keep them; a user definition shadows a
  bundled one of the same name). `MYHARNESS_AGENTS_DIR` and
  `MYHARNESS_USER_AGENTS_DIR` override the two roots; `agents/_lib/` is shared
  helper code (`myharness_agent.py`), not a definition. They are distinct from
  native orchestration subagents (`spawn_agent`, `agent_event`,
  `Stage/AgentsPanel.jsx`); do not reuse those identifiers. See
  `agents/README.md` for the manifest, parameter and JSON-line protocol
  contract. A run is a native-only session with `kind=agent_run` under the
  reserved `__agents__` project (one task per agent name); `agent`,
  `agent_version`, `agent_run_number`, `parent_session_id` and
  `agent_last_result` are stored together in the `agent_run_json` column. Runs
  get no project instructions or memory, cannot move or switch provider, and
  work in the caller's folder, the manifest's `working_directory`, or
  `data/agent_runs/<session_id>/` (deleted with the run). The reserved bucket
  cannot be renamed or deleted, export/backup include runs and their folder,
  and run backups cannot be imported. `backend/agent_runner.py` validates
  parameters, builds argv without a shell, drains bounded output, and stops the
  process tree (POSIX groups; Windows Job Object). The manifest's tool
  allowlist is enforced both when tools are offered and when they run,
  subagents included; `run_agent_script` follows shell approval rules. Readiness
  checks (`agent_preflight.py`) are cached 30 s; starting a run re-checks.
  `@name request` in any session starts an independent run, even while the
  session is busy; the parent shows a display-only card (`agent_run_link`).
  `/create_agent <intent>` drafts files in `data/agent_drafts/` for review;
  nothing is installed automatically, and draft dry runs always ask approval.
  Events `agent_run_progress`, `agent_run_item`, `agent_run_result`,
  `agent_run_link` and `agent_draft` are persisted but never model history;
  `session_updated` on the global stream carries `agent_last_result`. Frontend
  folding is in `agentRunState.js`, display helpers in `agentUi.js`, components
  in `components/Agents/`; the Agents sidebar view is the third view button.

## Config conventions

- `backend/agent/agent_config.example.yaml` is **canonical**. Every key must
  exist there with a comment. `backend/agent/agent_config.yaml` is the real,
  gitignored config; keep the two structurally in sync when adding keys.
- If `backend/agent/agent_config.yaml` exists, do not read its contents — it
  holds real credentials.
- Config is loaded **once at import** in `backend/agent/utils.py` and exposed as
  module-level constants. Adding a key means: add it to the example yaml with a
  comment, read it in `utils.py` with `nested_get`/`config_int`, and consume the
  constant elsewhere. Changing config requires a restart.
- Secrets support environment overrides that win over the file:
  `MYHARNESS_API_KEY` for `api.api_key`, `MYHARNESS_STT_API_KEY` for
  `audio.transcription.api_key`.
- Repository skills live under `skills/<name>/SKILL.md`. Keep them
  provider-independent; the Native provider reads them through skill tools and
  Codex/Claude receive the same catalog in their appended instructions.
- Defaults must be safe: loopback bind, `always_ask` approvals, git writes off,
  empty `allowed_paths` in the example.
- Tests patch `utils.ALLOWED_PATHS` per test, so an empty `allowed_paths` in the
  local config is fine.

## Coding conventions

- Match the surrounding style; prefer focused edits over rewrites.
- Backend is plain Python with type hints where they help; `from __future__
  import annotations` where the module already uses it.
- Comments explain *why*, not *what*. Do not narrate obvious code.
- Errors that reach an HTTP client are `HTTPException` with a specific status
  code and a message a user can act on.
- No machine-specific absolute paths anywhere — launchers resolve Python as
  `$MYHARNESS_PYTHON` → `./.venv` → `python3` → `python`.
- Keep `run.sh` and `run.cmd` behaviorally aligned where both platforms support
  the feature.
- Do not commit secrets or runtime state. Gitignored: `agent_config.yaml`(+`.bak`),
  `.env*`, `data/`, `logs/`, `*.log`,
  `CLAUDE.md`, Python caches, `.venv/`, root and per-package `node_modules/`,
  `frontend/dist/`, `electron/dist/`, `tui-rs/target/`. The root
  `package-lock.json` **is** committed.

## Backend API surface

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Status, models, app name/splash, provider availability, audio config, `config_warnings` |
| GET | `/api/commands` | Slash command reference (`backend/slash_commands.py`), shown in the Workspace Commands tab |
| POST | `/api/shutdown` | Stop the server |
| GET | `/api/codex/status` | Codex app-server availability |
| GET | `/api/claude/status` | Claude provider availability |
| GET | `/api/project` | Default project and allowed paths |
| POST | `/api/browse` | Directory picker |
| GET | `/api/files/image` | Serve an allowed local image |
| POST | `/api/audio/transcribe` | Transcribe a composer recording |
| GET | `/api/sessions` | Project/task/session tree |
| GET | `/api/search` | Cross-session transcript/tool/file search (`session_id` limits it to one thread) |
| GET/PUT | `/api/scratchpad` | Global scratch note (Workspace > Notes) |
| GET/PUT | `/api/projects/{id}/instructions` | Project instructions |
| POST | `/api/projects/{id}/instructions/append` | Append a section (returns `previous_text` for undo) |
| GET/POST | `/api/projects/{id}/memory` | List (pinned first, newest first) or add a memory note (`describe: true` asks the summary model for the description) |
| POST | `/api/projects/{id}/memory/restore` | Re-insert a deleted note (undo) |
| PATCH/DELETE | `/api/projects/{id}/memory/{memory_id}` | Edit/pin or delete a note (delete returns it for undo) |
| POST | `/api/sessions/{id}/memory/from-reply` | Condense one assistant reply into a project memory note |
| POST/PATCH/DELETE | `/api/projects[/{id}]` | Manage projects |
| POST/PATCH/DELETE | `/api/tasks`, `/api/projects/{id}/tasks/{task_id}` | Manage tasks |
| POST | `/api/sessions` | Create a session |
| POST | `/api/chats` | Create a sandboxed chat |
| GET/PATCH/DELETE | `/api/sessions/{id}` | Read, rename, delete a session |
| DELETE | `/api/sessions/{id}/last-prompt` | Delete the latest prompt when it never got a reply, native sessions only. Allowed when the run is idle and ended `interrupted`, `error` or `api timeout` (or never finished), the cases where `run_agent` rolls the context back; `max iterations reached`, `no content returned`, `completed` and answered prompts are refused with 409. Its events move to the `deleted_events` table. Rules in `backend/turn_delete.py` |
| GET | `/api/sessions/{id}/agents` | Native child agents of the active run |
| POST | `/api/sessions/{id}/agents/{agent_path}/interrupt` | Stop one native child agent (not the root) |
| GET | `/api/sessions/{id}/model-options` | Provider model catalog and supported thinking levels |
| PATCH | `/api/sessions/{id}/run-settings` | Persist the session model and thinking level |
| POST | `/api/sessions/{id}/move` | Move a session to another task |
| GET | `/api/sessions/{id}/events` | Paginated events |
| GET | `/api/sessions/{id}/export` | Markdown or HTML transcript |
| GET | `/api/sessions/{id}/backup` | Portable backup archive |
| POST | `/api/sessions/import` | Import a backup |
| GET | `/api/sessions/{id}/metrics` | Run and context metrics |
| GET | `/api/sessions/{id}/attachments/{filename}` | Serve an attachment |
| POST | `/api/sessions/{id}/message` | Send, queue, or run a slash command |
| POST | `/api/sessions/{id}/approval` | Resolve a pending approval |
| POST | `/api/sessions/{id}/cancel` | Cancel the active run |
| DELETE | `/api/sessions/{id}/queue/{message_id}` | Drop a queued message |
| POST | `/api/sessions/{id}/queue/reorder` | Reorder the queue |
| WS | `/api/events` | Global run status stream |
| WS | `/api/sessions/{id}/events` | Per-session event stream |
| GET/PATCH | `/api/workspace/tree`, `/api/workspace/entry` | Browse, rename |
| GET/PUT | `/api/workspace/file` | Paged preview; desktop-only save |
| POST | `/api/workspace/diff` | Diff a before-write snapshot |
| POST | `/api/workspace/revert`, `/api/workspace/revert_run` | Revert from a manifest |
| GET | `/api/workspace/git/status`, `/api/workspace/git/diff` | Read-only git |
| POST | `/api/workspace/git/stage`, `/api/workspace/git/commit` | Git writes, opt-in |
| GET | `/api/agents` | Routine-agent catalog with readiness |
| GET | `/api/agents/{name}`, `/api/agents/{name}/preflight` | Definition and dependency checks |
| POST | `/api/agents/{name}/options/{param}` | Script-listed choices (`options_from: script`) |
| POST | `/api/agents/{name}/runs` | Start a native run (text and/or params; optional parent/cwd/dry_run) |
| GET/PUT | `/api/agents/{name}/files/{path}` | Read/edit definition files; desktop-only save with base_hash |
| GET/DELETE | `/api/agent-drafts/{id}` | Inspect or discard a /create_agent draft |
| POST | `/api/agent-drafts/{id}/revise`, `/dry-run`, `/save` | Revise, test (always asks), install |

## Running

```bash
./run.sh             # web UI (127.0.0.1:8420 by default)
./run.sh --prod      # explicit production web mode
./run.sh --dev       # backend + Vite dev server on http://localhost:5173
./run.sh --electron  # Electron desktop shell
./run.sh --tui       # Rust TUI
./run.sh --cli       # plain CLI
./run.sh --cli --backend-url http://host:8420   # remote CLI
```

`run.cmd` mirrors these on Windows and defaults to the Electron shell.

## Verification

Keep this checkout for development only. The local testing environment is:

```text
/Users/animesh/Software/Harness_2BOrNot2B
```

Before testing, sync the current source into that directory and build there.
Old files and folders in the testing environment may be removed during the
sync, but always preserve its `data/` directory. Also preserve machine-local
configuration and credentials rather than copying development secrets.
Do not retain generated build output in this development checkout. After
testing, remove local `frontend/dist/`, `electron/dist/`, `tui-rs/target/`,
Python `__pycache__/` directories, and test caches; builds belong only in the
testing environment.

Use the smallest check that covers the change.

```bash
# Backend tests
./.venv/bin/python -m unittest discover -s tests

# Backend import/syntax sweep
./.venv/bin/python -m compileall backend

# Frontend tests and production build
(cd frontend && npm test)
(cd frontend && npm run build)

# Rust TUI
cargo test --manifest-path tui-rs/Cargo.toml
cargo clippy --manifest-path tui-rs/Cargo.toml

# Node and shell syntax
node --check electron/main.js
node --check electron/app-preload.js
node --check installer/setup.mjs
bash -n run.sh
```

For frontend behavior changes, run the app and check it in a browser:

```bash
./run.sh --dev   # frontend http://localhost:5173, backend http://127.0.0.1:8420
```

## Jarvis Linux builds

The build and deployment checkout on Jarvis is:

```text
/home/animesh/Applications/Harness_2BOrNot2B
```

For a fresh Jarvis build, sync the current development source into that
directory and build there. Old files and folders may be removed during the
sync, but always preserve `data/`, machine-local configuration, credentials,
virtual environments, and installed dependencies. Do not create or retain
Jarvis build output in this development checkout.

After producing the Linux `.deb`, read its exact package name with
`dpkg-deb -f <package.deb> Package`. Remove the installed version with
`sudo dpkg -r <package-name>`, then install the fresh package with
`sudo dpkg -i <package.deb>`. Do not guess the Debian package name or remove a
package until it has been resolved from the newly built artifact.
