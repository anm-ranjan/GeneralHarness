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
- `web_runs.py` — **provider dispatch and run orchestration.** Every run flows
  through here: it picks the provider from `SessionMeta.provider`, builds the
  system prompt, streams events, records change manifests, computes run metrics,
  and implements the idle-only slash commands (`/clear`, `/chdir`, `/approve`,
  `/verbose`, `/maxiters`, `/thinking`, `/model`).
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
  override iterations, reasoning effort, approval behavior, and verbosity
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
  only in verbose mode.
- **Search, export, and metrics** read persisted events as the source of truth —
  keep event changes compatible with all three.
- **File editing** is desktop-only: `PUT /api/workspace/file` requires the
  desktop header, re-checks allowed paths, and 409s when the file changed on
  disk. The browser UI keeps the read-only paged preview.

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
| GET | `/api/health` | Status, models, app name/splash, provider availability, audio config |
| POST | `/api/shutdown` | Stop the server |
| GET | `/api/codex/status` | Codex app-server availability |
| GET | `/api/claude/status` | Claude provider availability |
| GET | `/api/project` | Default project and allowed paths |
| POST | `/api/browse` | Directory picker |
| GET | `/api/files/image` | Serve an allowed local image |
| POST | `/api/audio/transcribe` | Transcribe a composer recording |
| GET | `/api/sessions` | Project/task/session tree |
| GET | `/api/search` | Cross-session transcript/tool/file search |
| POST/PATCH/DELETE | `/api/projects[/{id}]` | Manage projects |
| POST/PATCH/DELETE | `/api/tasks`, `/api/projects/{id}/tasks/{task_id}` | Manage tasks |
| POST | `/api/sessions` | Create a session |
| POST | `/api/chats` | Create a sandboxed chat |
| GET/PATCH/DELETE | `/api/sessions/{id}` | Read, rename, delete a session |
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
