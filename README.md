# MyHarness

MyHarness is a self-hosted harness for coding agents. It runs entirely on your
own machine and gives one persistent workspace — projects, tasks, sessions, and
transcripts — to whichever agent backend you prefer: an OpenAI-compatible API,
the Codex CLI, or Claude Code.

The same backend drives four clients: a React web UI, an Electron desktop shell,
a Rust terminal UI, and a plain CLI.

```
npx .            # interactive setup wizard
./run.sh         # http://127.0.0.1:8420
```

## Features

**Three agent providers, switchable per session**

- `native` — any OpenAI-compatible `/chat/completions` endpoint (OpenRouter,
  OpenAI, a local llama.cpp/vLLM server, …). This is the built-in agent loop:
  MyHarness owns the tools, the context window, compaction, and approvals.
- `codex-app-server` — spawns the local `codex` CLI and drives it over its
  app-server protocol. Authenticates with your Codex subscription.
- `claude-agent` — drives the local `claude` CLI through the Claude Agent SDK.
  Authenticates with your Claude Code subscription login (or `ANTHROPIC_API_KEY`).

Sessions remember their provider, and `/model native|codex|claude` switches an
existing session over, carrying the completed context across.

**Persistent workspace**

- **Projects** map to real directories on disk (inside your allowed paths),
  **tasks** group sessions within a project, and **sessions** hold a durable
  event stream that survives restarts.
- **Chats** are project-less sessions in a sandboxed `data/chats/<id>/`
  workspace, for questions that should not touch a real repository.
- Cross-session search over transcripts, tool calls, and touched files.
- Portable session backup/import as a single `.myharness.zip` (metadata, events,
  attachments, chat files, change manifests).
- Run-level change manifests, so a single file or an entire run can be reverted
  later — with conflict checks against edits made outside the agent.

**Working with the agent**

- Approval prompts before file writes and shell commands, with three modes.
- Live token streaming, collapsible thinking traces, and per-session overrides
  for iterations, reasoning effort, approval behavior, and verbosity.
- FIFO message queue: type ahead while a run is active; queued messages stay out
  of model context until they become the active turn.
- Image attachments by paste, drop, or file picker.
- Workspace panel: file tree, paged read-only previews, diffs, activity, usage
  history, and read-only git status/diff (writes are opt-in).
- Voice dictation in the composer with three speech-to-text backends:
  `local` (faster-whisper on this machine), `remote` (faster-whisper on an SSH
  compute host), and `api` (any OpenAI-compatible `/audio/transcriptions`).

**Clients**

- **Web UI** — React 19 + Vite + Tailwind CSS v4, fully themeable at runtime.
- **Desktop** — Electron shell around the same app, which additionally unlocks
  in-app file editing.
- **Rust TUI** — `tui-rs/`, a ratatui client that speaks only the public REST and
  WebSocket API, so it can attach to a backend on another machine.
- **CLI** — `./run.sh --cli`, standalone or attached to a running backend with
  `--backend-url`.

## Requirements

- Python 3.10 or newer
- Node.js 20 or newer, with npm
- Rust via [rustup](https://rustup.rs) — only for the Rust TUI
- A `codex` and/or `claude` CLI on PATH — only for those providers

## Quick start

From the repository root:

```bash
npx .                             # installs the wizard's deps, then runs it
# or, if you prefer to install them yourself:
npm install && npm run setup
```

The interactive installer asks for everything it needs and does the rest:

1. A display name, and generates ASCII art for the splash screen.
2. Whether to enable the Codex and Claude providers, probing for each CLI and
   offering to `npm install -g` the missing ones.
3. The native provider's base URL, API key, and default model.
4. Whether to enable voice dictation, and which of the three STT backends.
5. Which frontends to set up — it installs dependencies and builds the frontend.
6. Bind address, port, allowed workspace directories, approval mode, and the
   verbose-tools and git-writes toggles.

It then creates `./.venv`, installs `requirements.txt` into it, and writes
`backend/agent/agent_config.yaml`. Re-running it is safe: it offers to back the
existing config up to `agent_config.yaml.bak` before overwriting.

Then launch:

```bash
./run.sh                # web UI (builds the frontend if it is stale)
./run.sh --dev          # backend + Vite dev server with hot reload
./run.sh --electron     # Electron desktop shell
./run.sh --tui          # Rust TUI
./run.sh --cli          # terminal CLI
```

On Windows, `run.cmd` takes the same flags (and defaults to the Electron shell).

## Manual setup

If you would rather not use the installer:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt

cp backend/agent/agent_config.example.yaml backend/agent/agent_config.yaml
# edit it: api.api_key, models.*, permissions.allowed_paths

(cd frontend && npm ci --legacy-peer-deps && npm run build)
(cd electron && npm ci)      # only for the desktop shell

./run.sh
```

`run.sh` and `run.cmd` look for an interpreter in this order: `$MYHARNESS_PYTHON`,
`./.venv/bin/python` (`.venv\Scripts\python.exe` on Windows), `python3`, `python`.

For the Rust TUI, install Rust from [rustup.rs](https://rustup.rs) — on Windows
accept the default stable/MSVC toolchain and let rustup install the Visual Studio
C++ Build Tools, or builds fail with `link.exe not found`. Then `./run.sh --tui`
compiles and runs it; keybindings are documented in `tui-rs/README.md`.

## Configuration

Everything lives in `backend/agent/agent_config.yaml`, which is gitignored.
`backend/agent/agent_config.example.yaml` is the canonical template — every key
exists there with a comment. The config is read once at process start, so a
change needs a restart.

| Section | Purpose |
|---------|---------|
| `api` | `base_url` and `api_key` for the native provider, plus `streaming` and an optional OpenRouter `provider` allowlist. |
| `models` | Model ids for `default`, `read`, `write`, and `summary` roles. |
| `permissions` | `approval_mode` and `allowed_paths` — the directories the agent may touch. |
| `server` | `host` and `port` for the FastAPI backend. |
| `memory` | Context limit, compaction threshold, retained recent messages, tool-output compression. |
| `limits` | `max_file_size`, `max_tool_output`. |
| `search` | Result cap and an optional explicit `rg` path. |
| `gather` | Worker count for batched `gather_context` calls. |
| `python` | Interpreter the agent should prefer for `shell_run` python calls. |
| `agent` | `max_iterations`, `tool_call_checkpoint`. |
| `shell` | `default_timeout` for shell commands. |
| `logging` | `enabled`, and `log_dir` (empty means `<repo>/logs`). |
| `ui` | `app_name`, `splash_ascii`, `rich`, `verbose_tools`, `git_writes_enabled`. |
| `audio` | Voice dictation: `enabled` and the `transcription` block below. |
| `codex_app_server` | Codex provider: `enabled`, `binary`, sandbox and approval policy, timeout, model, reasoning effort. |
| `claude_agent` | Claude provider: `enabled`, `binary`, `model`, `permission_mode`, `timeout_seconds`, `max_turns`. |
| `desktop` | Electron shell: `enabled`, `backend_url`, backend reuse/fallback, origin gating, GPU workaround. |

### Providers

The native provider needs `api.base_url` and `api.api_key`. The CLI providers
need no key at all when the CLI is already logged in:

```bash
npm install -g @openai/codex && codex login
npm install -g @anthropic-ai/claude-code && claude   # complete the login once
```

Then set `codex_app_server.enabled: true` and/or `claude_agent.enabled: true`.
Both are probed at runtime, so an enabled-but-missing CLI degrades to a clear
error rather than a crash.

### Speech to text

```yaml
audio:
  enabled: true
  transcription:
    processor: api          # local | remote | api
    api_base_url: "https://api.openai.com/v1"
    api_key: ""             # or export MYHARNESS_STT_API_KEY
    model: whisper-1
```

- `local` — needs `faster-whisper` in the backend's environment; `model` is a
  size (`tiny`, `base`, `small`, `medium`, `large-v3`).
- `remote` — uploads over SSH to a host listed in
  `utils/Qsub_Windows/server_config.yaml` (copy it from the shipped
  `server_config.yaml.template`) and runs faster-whisper there on GPU. Set
  `server` and `app_dir`.
- `api` — POSTs multipart `file` + `model` to
  `{api_base_url}/audio/transcriptions` with a bearer token; `model` is the
  remote model id.

### Environment overrides

| Variable | Overrides |
|----------|-----------|
| `MYHARNESS_API_KEY` | `api.api_key` (highest precedence) |
| `MYHARNESS_STT_API_KEY` | `audio.transcription.api_key` (highest precedence) |
| `MYHARNESS_WEB_HOST`, `MYHARNESS_WEB_PORT` | `server.host`, `server.port` |
| `MYHARNESS_APPROVAL_MODE` | `permissions.approval_mode` |
| `MYHARNESS_VERBOSE_TOOLS` | `ui.verbose_tools` |
| `MYHARNESS_PYTHON` | interpreter chosen by the launchers |
| `MYHARNESS_BACKEND_URL` | backend the TUI and remote CLI attach to |
| `MYHARNESS_WEB_DATA_DIR`, `MYHARNESS_WEB_STATIC_DIR` | `data/` and `frontend/dist` locations |
| `MYHARNESS_ELECTRON_LOG` | Electron log path |

Keeping the two API keys in the environment rather than the YAML file is the
recommended production setup.

## Security

MyHarness is designed to run on your own machine, for you.

- **The API has no authentication.** Anyone who can reach the port can read and
  write every directory in `permissions.allowed_paths` and run shell commands
  through the agent. The default bind address is therefore `127.0.0.1`. Setting
  `server.host: 0.0.0.0` exposes all of that to your whole network — only do it
  behind a trusted reverse proxy that adds authentication, or a firewall.
- **Approval modes** decide what the agent can do unprompted:
  - `always_ask` — confirm before file writes, patches, and shell commands. Default.
  - `shell_only` — confirm before shell commands only.
  - `auto_approve` — never confirm. The agent writes files and runs shell
    commands on its own. Use it only in a disposable sandbox.
- **`permissions.allowed_paths` is the real boundary.** Keep it to the specific
  repositories you want the agent working in; do not list `/` or your home
  directory.
- **Git writes** from the Workspace panel stay disabled unless
  `ui.git_writes_enabled: true`. Status and diffs are always read-only-available.
- **Never commit** `backend/agent/agent_config.yaml`, `.env` files, or anything
  under `data/` — all are gitignored.

The backend prints warnings at startup for an empty API key with no CLI provider,
missing or empty allowed paths, `auto_approve`, and a `0.0.0.0` bind.

## Verification

```bash
# Backend tests and an import/syntax sweep
./.venv/bin/python -m unittest discover -s tests
./.venv/bin/python -m compileall backend

# Frontend tests and production build
(cd frontend && npm test)
(cd frontend && npm run build)

# Setup wizard (config writer, overwrite/backup paths)
node --test installer/setup.test.mjs

# Rust TUI
cargo test --manifest-path tui-rs/Cargo.toml

# Launcher and Node syntax
bash -n run.sh
node --check electron/main.js
node --check electron/app-preload.js
node --check installer/setup.mjs
```

## Layout

```text
backend/          FastAPI app, session store, run orchestration
  agent/          agent loop, tools, config, provider adapters
  web_routes/     REST/WebSocket route groups
frontend/         React 19 + Vite web UI
electron/         desktop shell
tui-rs/           Rust terminal client
installer/        npx setup wizard
tests/            backend unit tests
data/             runtime state (gitignored)
```

## License

MIT. See [LICENSE](LICENSE).
