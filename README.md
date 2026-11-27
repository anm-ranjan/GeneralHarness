# MyHarness

MyHarness is a self-hosted harness for coding agents. It runs entirely on your
own infrastructure and gives one persistent workspace — projects, labelled
threads, and transcripts — to whichever agent backend you prefer: an
OpenAI-compatible API, the Codex CLI, or Claude Code.

The same backend drives four clients: a React web UI, an Electron desktop shell,
a Rust terminal UI, and a plain CLI.

```
npx .            # setup wizard; can also build the desktop installer
./run.sh         # http://127.0.0.1:8420
```

See [Installation](#installation) for the desktop installer and the
from-source setup.

## Installation

There are two ways to install MyHarness:

- **Desktop installer** (recommended): a DMG, Windows installer or DEB that you
  build once per operating system and can copy to any machine. On first launch
  it asks for an install folder and your settings, and sets everything up.
- **From a source checkout**: a terminal wizard that configures this checkout
  in place. Use it for development, a browser-only or headless server, or the
  Rust TUI on its own.

### Requirements

On the machine where MyHarness runs:

- Python 3.10 or newer
- Node.js 20 or newer with npm, to install the Codex or Claude CLIs (and to
  build the installer or the web UI)
- Rust via [rustup](https://rustup.rs), only for the Rust TUI
- The `codex`, `claude` or `agy` CLI, only for those providers; both installers
  can install them for you

### Option 1: Desktop installer

**1. Build the installer** on the operating system you want it for
(cross-OS builds are not supported):

```bash
npx .        # run the setup wizard and answer "yes" to the desktop package
```

or build it directly:

```bash
(cd frontend && npm ci --legacy-peer-deps && npm run build)
cd electron && npm ci
MYHARNESS_PORTABLE_BUILD=1 npm run dist:mac      # or dist:win, dist:linux
```

The installer lands in `electron/dist/`: a `.dmg` on macOS, an NSIS `.exe` on
Windows, and a `.deb` plus an AppImage on Linux (prefer the DEB on Ubuntu).
Set `MYHARNESS_PRODUCT_NAME="My Name"` to change the app's name.
`MYHARNESS_PORTABLE_BUILD=1` matters only on a machine whose own config sets
`desktop.backend_repo`: without it, that path is baked into the package so the
build machine's app keeps running from its checkout and skips setup.

The package carries the backend, skills, agents, built UI and TUI sources. It
carries no Python environment and no configuration, so it holds nothing
specific to the machine that built it.

**2. Install and launch it.** Open the DMG and drag the app to Applications,
run the `.exe`, or `sudo dpkg -i <package>.deb`. On first launch a setup
window walks you through:

1. **Install**: choose an install folder (suggested: a folder named after the
   app in its per-user data directory) and a Python 3.10 or newer. It copies the
   backend there, creates `.venv` and installs `requirements.txt`.
2. **App name** and splash-screen ASCII art.
3. **Providers**: Codex and Claude (install with npm, log in, enabled only once
   the login is verified), the Native API key, and Antigravity. Antigravity runs
   on your personal Google sign-in, so the step first asks you to check Google's
   terms, and states that MyHarness accepts no responsibility if your account is
   restricted or blocked; it installs and enables Antigravity only after you
   confirm.
4. **Voice dictation**: local, remote (SSH) or API speech to text.
5. **Server and workspaces**: allowed folders, approval mode, bind address and
   port, storage and logging.
6. **Fleet** (optional): other machines to switch between.
7. **Terminal UI** (optional): builds the Rust TUI. This step needs `cargo`;
   when it is missing it says so and links to rustup.rs, and it can be skipped.
8. **Review and save**: writes `backend/agent/agent_config.yaml` in the install
   folder and stores API keys in the encrypted credential file
   (`~/.myharness`). Then the app starts.

**Afterwards.** File → Setup… reopens the window; saved changes apply after a
restart. When a newer build of the app starts, it first copies its files into
the install folder, keeping `agent_config.yaml`, `.venv`, `data/`, `logs/`, your
own agents and skills, and a built TUI. The chosen folder is recorded in
`install-<app name>.json` in the app's per-user data directory, so differently
named builds keep separate installs. If you install a second copy on the same
machine, give it a different port in step 5: the app connects to a backend
that is already running on its port instead of starting its own.

### Option 2: From a source checkout

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
   It then offers Antigravity (Google's `agy` CLI): it first shows a notice to
   check Google's terms, since Antigravity runs on a personal Google sign-in,
   and that MyHarness accepts no responsibility if the account is restricted
   or blocked. Only after you confirm does it install `agy` (Homebrew on
   macOS, Google's install script on Linux), run `agy acp --login` and enable
   it.
3. Native and STT API keys. Blank answers keep them disabled or preserve existing
   values; entered keys go into this host's encrypted credential file.
4. Native base URL, model, timeout and iteration limit, followed by the voice
   dictation backend, model, language, device, timeout, upload limit, and
   either direct SSH or API configuration.
5. Browser UI, the desktop installer for the current OS (Option 1), and the
   Rust TUI.
6. Trusted-LAN bind address, port, allowed workspaces, approvals, data directory,
   SQLite database filename, logging policy, verbose tools, and Git writes.

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

### Linux notes

Building on Linux produces both an AppImage and a DEB in `electron/dist/`.
Prefer the DEB on Ubuntu and other Debian-based systems.

Ubuntu 24.04 and newer can restrict the unprivileged user namespaces Electron
normally uses for Chromium sandboxing. When the terminal wizard (`npx .`)
detects that restriction, it:

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

### Manual setup

If you would rather not use the installer:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt

cp backend/agent/agent_config.example.yaml backend/agent/agent_config.yaml
# edit it: models.*, permissions.allowed_paths, provider settings
# run npm run setup or use Electron Settings to create encrypted API credentials

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

## Features

**Three agent providers, switchable per thread**

- `native` — any OpenAI-compatible `/chat/completions` endpoint (OpenRouter,
  OpenAI, a local llama.cpp/vLLM server, …). This is the built-in agent loop:
  MyHarness owns the tools, the context window, compaction, and approvals.
- `codex-app-server` — spawns the local `codex` CLI and drives it over its
  app-server protocol. Authenticates with your Codex subscription.
- `claude-agent` — drives the local `claude` CLI through the Claude Agent SDK.
  Authenticates with your Claude Code subscription login (or `ANTHROPIC_API_KEY`).

Threads remember their provider, and `/model native|codex|claude` switches an
existing thread over, carrying the completed context across.

**Reusable Harness skills**

- Skills live at `skills/<name>/SKILL.md` and are shared by all providers.
- `/skills` lists the installed collection and `/skills <name>` displays the
  complete instructions. The Native agent also has `skill_list` and
  `skill_read` tools for progressive disclosure.

**Persistent workspace**

- **Projects** map to real directories on disk (inside your allowed paths),
  **labels** organize threads within a project, and **threads** hold a durable
  event stream that survives restarts. Every thread has a primary colour-coded
  label; deleting a label reassigns its threads instead of deleting them.
- **Chats** are project-less threads in a sandboxed `data/workspaces/<id>/`
  workspace, for questions that should not touch a real repository.
- Cross-session search over transcripts, tool calls, and touched files.
- Portable thread backup/import as a single `.myharness.zip` (metadata, events,
  attachments, and change manifests).
- SQLite stores application-owned state, including deduplicated image, audio,
  and file BLOBs; only active workspaces and disposable materializations remain
  as ordinary files.
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
  `local` (faster-whisper on the backend host), `remote` (faster-whisper on an
  SSH compute host), and `api` (any OpenAI-compatible
  `/audio/transcriptions`).

**Clients**

- **Web UI** — React 19 + Vite + Tailwind CSS v4, fully themeable at runtime.
- **Desktop** — Electron shell around the same app, which additionally unlocks
  in-app file editing. It doubles as the installer: NSIS on Windows, DMG on
  macOS, and AppImage and DEB packages on Linux, each with a first-run setup
  window (see [Installation](#installation)).
- **Rust TUI** — `tui-rs/`, a ratatui client that speaks only the public REST and
  WebSocket API, so it can attach to a backend on a remote host.
- **CLI** — `./run.sh --cli`, standalone or attached to a running backend with
  `--backend-url`.

## Configuration

Everything lives in `backend/agent/agent_config.yaml`, which is gitignored.
`backend/agent/agent_config.example.yaml` is the canonical template — every key
exists there with a comment. The config is read once at process start, so a
change needs a restart.

Runtime storage defaults to:

```text
data/
  myharness.sqlite3
  workspaces/
  temporary-materializations/
```

Set `storage.database_filename` to choose another filename inside `data/`; the
`.sqlite3` suffix is added when omitted. SQLite may create `-wal` and `-shm`
sidecars while the backend is running. Back up a live store through SQLite's
backup mechanism rather than copying only the main file.

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
| `storage` | Unified persistent data directory and SQLite database filename (empty directory means `<repo>/data`). |
| `ui` | `app_name`, `splash_ascii`, `rich`, `verbose_tools`, `git_writes_enabled`. |
| `audio` | Voice dictation: `enabled` and the `transcription` block below. |
| `codex_app_server` | Codex provider: `enabled`, `binary`, sandbox and approval policy, timeout, model, reasoning effort. |
| `claude_agent` | Claude provider: `enabled`, `binary`, `model`, `permission_mode`, `timeout_seconds`, `max_turns`. |
| `desktop` | Electron shell: `enabled`, `backend_url`, backend reuse/fallback, origin gating, GPU workaround. |

### Fleet host switching

A fleet lets one web or Electron UI switch between independent harness
backends. Each host keeps its own projects, labels, threads, credentials,
and provider processes; switching replaces the whole workspace rather than
merging data between hosts.

Install and configure MyHarness on every host first. Keep each backend on
loopback because the API has no authentication:

```yaml
server:
  host: 127.0.0.1
  port: 8420
```

The browser connects directly to every URL in `fleet.hosts`. A safe setup uses
an SSH local forward for each remote backend while the local backend remains at
`127.0.0.1:8420`. For a two-host fleet, run this on host A:

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 8421:127.0.0.1:8420 user@host-b.example
```

Run the reciprocal command on host B, changing the SSH destination to host A.
Configure key-based SSH authentication so the tunnels do not require
interactive passwords, and enable the SSH server on each host
that accepts a tunnel. The setup wizard can generate
`scripts/fleet-tunnels.sh`, which supervises the configured forwards and
restarts them after network interruptions; verify that each generated entry
names the remote host rather than the host running the script.

Use the same stable ids and labels everywhere, but define URLs from the browser
running on that particular host. Loopback URLs therefore swap between the
two configurations:

```yaml
# Host A
fleet:
  enabled: true
  self: host-a
  poll_seconds: 10
  hosts:
    - id: host-a
      label: Host A
      url: http://127.0.0.1:8420
    - id: host-b
      label: Host B
      url: http://127.0.0.1:8421
```

```yaml
# Host B
fleet:
  enabled: true
  self: host-b
  poll_seconds: 10
  hosts:
    - id: host-a
      label: Host A
      url: http://127.0.0.1:8421
    - id: host-b
      label: Host B
      url: http://127.0.0.1:8420
```

For three or more hosts, reserve a different local forwarded port for each
remote host on every viewing host. Start all backends and tunnels before
opening the client; the host switcher reports an unreachable peer when its
backend or forward is down.

#### Bringing hosts online and offline

Add `ssh` (for example `user@host-b.example`) and `repo` (the checkout on that
host) to a peer in `fleet.hosts`, and give `url` a loopback address. This
machine then keeps its own supervised SSH forward to that peer, so
`fleet-tunnels.sh` is no longer needed for it, and **Settings → App → Hosts**
lists the peer as Online, Off (the machine answers, its backend does not), or
Unreachable. *Bring online* starts the peer's backend over SSH, detached from
the session; *Take offline* shuts it down through `/api/shutdown`, after a
confirmation that names any running or approval-waiting sessions.

A host is simply online while its backend answers. Nothing records who started
it, so any client may take it offline, and it disappears for every client at
once. A peer's backend keeps running until someone takes it offline. Peers are
started with `.venv/bin/python backend/web_app.py` from `repo` on a loopback
port (`remote_port`, default 8420), logging to `logs/bridge-backend.log`
there. The requests always go to the machine serving the page, not to the host
currently being viewed. A peer whose url is already served by a hand-run tunnel
is left alone, and Windows peers are not supported.

A packaged desktop app carries its own copy of the backend, skills, config and
Python environment. Set `desktop.backend_repo` to an absolute checkout path (for
example `/home/me/Applications/MyHarness`) and the shell runs that checkout's
backend, config, skills, data and UI instead, so updating the checkout updates
the app without reinstalling it. The checkout needs a `.venv` and a built
`frontend/dist`; if the path is relative or has no `backend/web_app.py`, the
shell logs why and uses the bundled copy. The setting is read from the packaged
YAML, so changing it takes one rebuild. `MYHARNESS_BACKEND_REPO` overrides it.

Electron normally reuses a healthy backend at `desktop.backend_url` when
`desktop.prefer_existing_backend` is true. Set
`desktop.start_local_backend_fallback: false` if Electron must never start its
own sidecar when that backend is unavailable. Unpackaged Electron reads the
deployment YAML at startup; packaged applications contain an embedded YAML, so
make this change before rebuilding and installing the package.

Every launcher uses `storage.data_dir` as the single source of truth for
persistent state, including an Electron-started backend. The setup wizard writes
an explicit data directory before packaging, so Electron does not create a
second store under its own application-data directory.

### Providers

The native provider needs `api.base_url` and a Native key in this host's encrypted
credential file. `MYHARNESS_API_KEY` remains a higher-priority environment override.
Settings → Native Settings → Endpoint can instead point Native at a local
OpenAI-compatible server such as llama-server: choose *Local server*, enter the
model id its `/v1/models` lists (the `--alias` or GGUF name) and its base URL
(for example `http://127.0.0.1:8081/v1`). It is checked against the server, needs
no key (the stored key is never sent there), takes the context window from the
server's `n_ctx`, and is kept per host in `data/native_settings.json`.
"Check endpoint" asks the server for its models without saving. For a
llama-server on a Linux machine of the network, **LAN model server** takes the
path of a start script there (default `/archive/models/run-inkling.sh`; "Find
scripts" lists the `.sh` files in its folder). "Launch server" runs it over SSH
(`ssh` in batch mode, logging in as `user@host`, empty: your user name at the
base URL host, with your SSH agent, `~/.ssh` keys and `~/.ssh/config`), logs to
`~/.cache/myharness/llama-server.log` on that machine, and waits until the
endpoint answers. "Stop server" stops what was started from here (its whole
process group), also after an app restart.
The native provider also has per-session thinking levels (`low`, `medium`, `high`),
set from the model menu or with `/thinking`. They are sent as OpenRouter's unified
`reasoning.effort` (to OpenRouter endpoints only); a session starts at `high`
(`agent.native_reasoning_effort`; blank sends nothing and leaves each model on its own default).
On a local endpoint (Native Settings → Endpoint → Local server) the same level is
sent as `chat_template_kwargs.reasoning_effort` instead, which llama-server passes
to the chat template; OpenRouter never receives that field. The variable and the
value per level are set under Native Settings → Reasoning map, shown only for a
local endpoint; the defaults suit Inkling 2 (`reasoning_effort` = `low` /
`medium` / `high`), and an on/off template such as Qwen3 can use
`enable_thinking` = `false` / `true` / `true`. The level sits near
the start of the prompt, so changing it mid-thread makes the next request
re-read the whole thread once.
Whether a thinking model's own plain-text reasoning (`reasoning_content`,
`reasoning` or `<think>` text) is sent back to it is `reasoning_replay.mode`:
`off` (shown only, the default), `strip_always` (within one task, between tool
calls), `keep` (until compaction) or `strip_on_compact` (like `keep`, and
compaction also clears it from the messages it keeps). `reasoning_replay.models`
overrides it per model ID or `prefix*`. Settings → Native Settings → Agent loop
changes the mode on one host from the next request on; OpenRouter
`reasoning_details` are passed back either way.
The CLI providers need no key when the CLI is already logged in:

```bash
npm install -g @openai/codex && codex login
npm install -g @anthropic-ai/claude-code && claude auth login
```

Codex follows the session's approval mode: `always_ask` asks for every command and
file change, `shell_only` asks for commands and permission grants only, and
`auto_approve` answers every Codex request itself.

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

### Native prompt caching and working history

Native requests use provider-side prefix caching by default. This is separate
from the local tool-result cache: tools still execute and need their normal
approvals, and cached tokens still occupy the context window.

```yaml
native_caching:
  mode: auto        # auto | off; off cannot disable a provider's implicit caching
  ttl: 5m           # 5m | 1h (1h applies to Anthropic only)
  explicit_models: []
```

- OpenRouter receives a stable session/agent routing key; configured provider
  ordering stays authoritative. Anthropic models receive cache controls; other
  models keep their provider's implicit caching. Selected Alibaba Qwen models
  get explicit breakpoints only on direct Alibaba or Alibaba-pinned OpenRouter
  routes. Direct xAI requests get a conversation header. Unrecognized
  OpenAI-compatible servers receive no extra cache fields. See
  `backend/agent/prompt_cache.py`.
- Cache hints are applied to a request copy, never to stored history.
- The Usage tab keeps per-run API totals (input, output, cache reads/writes,
  reported cost) including child agents; the composer shows generation speed
  only.

Compaction keeps a bounded task state (`memory.task_state_tokens`, default
4096, with a model synthesis budget of `memory.summary_target_tokens`) instead
of a free-form summary. User requests are never silently truncated. Exact tool
results are archived per session under `data/native_history/` and stay
retrievable through the `myharness_context` tool (`action: evidence`); loaded
`SKILL.md` instructions survive compaction.

### Environment overrides

| Variable | Overrides |
|----------|-----------|
| `MYHARNESS_API_KEY` | Native-provider secret; overrides the encrypted host credential |
| `MYHARNESS_STT_API_KEY` | API transcription secret; overrides the encrypted host credential |
| `MYHARNESS_CREDENTIALS_DIR` | encrypted credential directory (default `~/.myharness`) |
| `MYHARNESS_WEB_HOST`, `MYHARNESS_WEB_PORT` | `server.host`, `server.port` |
| `MYHARNESS_APPROVAL_MODE` | `permissions.approval_mode` |
| `MYHARNESS_VERBOSE_TOOLS` | `ui.verbose_tools` |
| `MYHARNESS_PYTHON` | interpreter chosen by the launchers |
| `MYHARNESS_BACKEND_URL` | backend the TUI and remote CLI attach to |
| `MYHARNESS_WEB_DATA_DIR`, `MYHARNESS_WEB_STATIC_DIR` | `data/` and `frontend/dist` locations |
| `MYHARNESS_ELECTRON_LOG` | Electron log path |

The setup wizard never stores either API key in YAML.

## Copy Codex desktop history

Run the converter with the Python environment and Codex CLI on the same machine
as your Codex desktop data:

```bash
./.venv/bin/python backend/codex_history_import.py --target-data-dir data
```

The default is a dry run. It converts the history into a temporary copy of the
MyHarness database and reports what it found. Use `--codex-home` for a custom
Codex data directory, or `--database-filename` for a custom MyHarness database
name. After reviewing the dry run, close MyHarness and add `--apply` to write to
the selected data directory. A rerun skips previously imported threads.

Projects use their primary Codex folder and reuse existing MyHarness projects
with that folder. Threads without a project go under Chats. The converter keeps
readable messages, archive status, timestamps, and available images. Missing
images are marked in the transcript and counted in the report. New
messages use a separate Codex provider session with recent completed messages
as context. Workspace files and Codex settings stay in place. The MyHarness
provider and allowed paths still determine where you can run a new turn.

## Security

MyHarness is designed for a trusted local network.

- **The API has no authentication.** Anyone who can reach the port can read and
  write every directory in `permissions.allowed_paths` and run shell commands
  through the agent. The installer defaults to `0.0.0.0` only after you confirm
  that the host is on a trusted LAN. Firewall the configured port from
  untrusted networks; use `127.0.0.1` for host-local access.
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

# Setup wizards (config writer, overwrite/backup paths, desktop install/update copy)
node --test installer/setup.test.mjs installer/lib/config.test.cjs electron/*.test.cjs

# Rust TUI
cargo test --manifest-path tui-rs/Cargo.toml

# Launcher and Node syntax
bash -n run.sh
node --check electron/main.js
node --check electron/app-preload.js
node --check installer/setup.mjs
node --check electron/setup.js
```

## Layout

```text
backend/          FastAPI app, session store, run orchestration
  agent/          agent loop, tools, config, provider adapters
  web_routes/     REST/WebSocket route groups
frontend/         React 19 + Vite web UI
electron/         desktop shell and installer (setup/ is the first-run window)
tui-rs/           Rust terminal client
installer/        npx setup wizard
  lib/            config writer shared with the desktop setup
tests/            backend unit tests
data/             runtime state (gitignored)
```

## License

MIT. See [LICENSE](LICENSE).
