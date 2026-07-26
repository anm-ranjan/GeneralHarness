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

**Reusable Harness skills**

- Skills live at `skills/<name>/SKILL.md` and are shared by all providers.
- `/skills` lists the installed collection and `/skills <name>` displays the
  complete instructions. The Native agent also has `skill_list` and
  `skill_read` tools for progressive disclosure.

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
  in-app file editing. Setup builds NSIS on Windows, DMG on macOS, and
  AppImage and DEB packages on Linux; cross-OS builds should run on their
  target OS. On Ubuntu, the DEB is preferred. When AppArmor restricts
  unprivileged user namespaces, setup offers to configure the Electron SUID
  helpers and install an AppImage-specific AppArmor profile; it never disables
  Chromium's sandbox.
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
2. Whether to enable Codex and Claude. It installs missing CLIs, runs their
   subscription login flows, and enables them only after authentication passes.
3. Native base URL, model, timeout, and iteration limit. Native is enabled only
   when `MYHARNESS_API_KEY` is already exported; the key is never written.
4. Voice dictation backend, model, language, device, timeout, upload limit, and
   either direct SSH or API configuration.
5. Browser UI, an installable Electron package for the current OS, and the Rust TUI.
6. Trusted-LAN bind address, port, allowed workspaces, approvals, data directory,
   logging policy, verbose tools, and Git writes.

It then creates `./.venv`, installs `requirements.txt` into it, and writes
`backend/agent/agent_config.yaml`. Re-running it is safe: it offers to back the
existing config up to `agent_config.yaml.bak` before overwriting.

### Linux desktop installation note

Linux setup produces both an AppImage and a DEB in `electron/dist/`. Prefer the
DEB on Ubuntu and other Debian-based systems.

Ubuntu 24.04 and newer can restrict the unprivileged user namespaces Electron
normally uses for Chromium sandboxing. When setup detects that restriction, it:

1. Checks the `chrome-sandbox` helpers created under Electron's dependencies and
   the unpacked application.
2. Offers to use `sudo` to set those helpers to owner `root:root` and mode
   `4755`, then verifies the result.
3. Offers to install and load a per-application AppArmor profile allowing
   user namespaces for generated AppImages.

Both privileged operations require explicit confirmation and an interactive
sudo password. Setup never adds `--no-sandbox`, disables AppArmor, or changes
the system-wide user-namespace setting. Running `npm ci` or rebuilding Electron
can replace the sandbox helpers, so rerun setup if the SUID sandbox error
returns.

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
# edit it: models.*, permissions.allowed_paths, provider settings
export MYHARNESS_API_KEY="..."       # only when using Native

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
| `api` | Native enablement, `base_url`, timeout, streaming, and an optional OpenRouter `provider` allowlist. |
| `models` | Model ids for `default`, `read`, `write`, and `summary` roles. |
| `permissions` | `approval_mode` and `allowed_paths` — the directories the agent may touch. |
| `server` | `host` and `port` for the FastAPI backend. |
| `memory` | Context limit, compaction threshold, retained recent messages, tool-output compression. |
| `limits` | `max_file_size`, `max_tool_output`. |
| `search` | Result cap and an optional explicit `rg` path. |
| `gather` | Worker count for batched `gather_context` calls. |
| `python` | Interpreter the agent should prefer for `shell_run` python calls. |
| `agent` | Default provider, `max_iterations`, and `tool_call_checkpoint`. |
| `shell` | `default_timeout` for shell commands. |
| `logging` | Enablement, level, directory, and retention period. |
| `storage` | Persistent data directory (empty means `<repo>/data`). |
| `ui` | `app_name`, `splash_ascii`, `rich`, `verbose_tools`, `git_writes_enabled`. |
| `audio` | Voice dictation: `enabled` and the `transcription` block below. |
| `codex_app_server` | Codex provider: `enabled`, `binary`, sandbox and approval policy, timeout, model, reasoning effort. |
| `claude_agent` | Claude provider: `enabled`, `binary`, `model`, `permission_mode`, `timeout_seconds`, `max_turns`. |
| `desktop` | Electron shell: `enabled`, `backend_url`, backend reuse/fallback, origin gating, GPU workaround. |

### Providers

The native provider needs `api.base_url` and `MYHARNESS_API_KEY`. The CLI providers
need no key at all when the CLI is already logged in:

```bash
npm install -g @openai/codex && codex login
npm install -g @anthropic-ai/claude-code && claude auth login
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
    model: whisper-1
```

- `local` — needs `faster-whisper` in the backend's environment; `model` is a
  size (`tiny`, `base`, `small`, `medium`, `large-v3`).
- `remote` — uploads over SSH to the configured `server`, using `username` and
  `key_file` when supplied, and runs faster-whisper from `app_dir`.
- `api` — POSTs multipart `file` + `model` to
  `{api_base_url}/audio/transcriptions` with a bearer token; `model` is the
  remote model id.

### Environment overrides

| Variable | Overrides |
|----------|-----------|
| `MYHARNESS_API_KEY` | Native-provider secret; required for Native availability |
| `MYHARNESS_STT_API_KEY` | API transcription secret |
| `MYHARNESS_WEB_HOST`, `MYHARNESS_WEB_PORT` | `server.host`, `server.port` |
| `MYHARNESS_APPROVAL_MODE` | `permissions.approval_mode` |
| `MYHARNESS_VERBOSE_TOOLS` | `ui.verbose_tools` |
| `MYHARNESS_PYTHON` | interpreter chosen by the launchers |
| `MYHARNESS_BACKEND_URL` | backend the TUI and remote CLI attach to |
| `MYHARNESS_WEB_DATA_DIR`, `MYHARNESS_WEB_STATIC_DIR` | `data/` and `frontend/dist` locations |
| `MYHARNESS_ELECTRON_LOG` | Electron log path |

The setup wizard never stores either API key in YAML.

## Security

MyHarness is designed for a trusted local network.

- **The API has no authentication.** Anyone who can reach the port can read and
  write every directory in `permissions.allowed_paths` and run shell commands
  through the agent. The installer defaults to `0.0.0.0` only after you confirm
  that the machine is on a trusted LAN. Firewall the configured port from
  untrusted networks; use `127.0.0.1` for machine-local access.
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
