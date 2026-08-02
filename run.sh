#!/usr/bin/env bash
# Launch the MyHarness
#
# Usage:
#   ./run.sh              Start the web UI (serves built frontend)
#   ./run.sh --prod       Start the web UI explicitly
#   ./run.sh --dev        Start backend + Vite dev server (hot reload)
#   ./run.sh --electron   Start the Electron desktop shell
#   ./run.sh --tui        Start the Rust TUI (needs cargo; falls back to the
#                         legacy Textual TUI when cargo is not installed)
#   ./run.sh --tui-legacy Start the legacy Python Textual TUI explicitly
#   ./run.sh --cli        Start the CLI

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$SCRIPT_DIR/backend/agent"
CONFIG_FILE="$AGENT_DIR/agent_config.yaml"
CONFIG_READER="$SCRIPT_DIR/tui-rs/read_backend_url.py"

# Find a working Python, in order: $MYHARNESS_PYTHON, the repo venv, system.
PYTHON=""
for _candidate in "${MYHARNESS_PYTHON:-}" "$SCRIPT_DIR/.venv/bin/python" python3 python; do
    [ -n "$_candidate" ] || continue
    if [ -x "$_candidate" ] || command -v "$_candidate" &>/dev/null; then
        PYTHON="$_candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "Error: No Python interpreter found. Run 'npm run setup' or install Python 3.10+." >&2
    exit 1
fi

# Read one dotted key from agent_config.yaml; prints nothing when unavailable.
read_config() {
    [ -f "$CONFIG_FILE" ] || return 0
    [ -f "$CONFIG_READER" ] || return 0
    "$PYTHON" "$CONFIG_READER" "$CONFIG_FILE" "$1" 2>/dev/null
}

RUN_LEGACY_TUI=""
if [[ "${1:-}" == "--tui" ]]; then
    shift
    if command -v cargo &>/dev/null; then
        # Point the Rust TUI at the configured desktop.backend_url so it can
        # reach a remote backend (e.g. workstation.local) instead of assuming the
        # backend runs on this machine. An explicit MYHARNESS_BACKEND_URL or a
        # --backend-url flag still wins (clap precedence: arg > env > default),
        # and localhost remains the fallback when nothing is configured.
        if [[ -z "${MYHARNESS_BACKEND_URL:-}" && -f "$CONFIG_FILE" ]]; then
            _tui_backend_url="$(read_config desktop.backend_url)"
            if [ -n "$_tui_backend_url" ]; then
                export MYHARNESS_BACKEND_URL="$_tui_backend_url"
                echo "Using backend URL from desktop.backend_url: $_tui_backend_url"
            else
                echo "Could not read desktop.backend_url from config; using TUI default http://127.0.0.1:8420." >&2
                echo "Pass --backend-url http://HOST:8420 to override." >&2
            fi
        elif [[ -n "${MYHARNESS_BACKEND_URL:-}" ]]; then
            echo "Using backend URL from MYHARNESS_BACKEND_URL: $MYHARNESS_BACKEND_URL"
        fi
        echo "Starting the Rust TUI (first build may take a few minutes)..."
        exec cargo run --manifest-path "$SCRIPT_DIR/tui-rs/Cargo.toml" -- "$@"
    fi
    echo "WARNING: cargo was not found on PATH. Install Rust from https://rustup.rs" >&2
    echo "         (see README.md). Falling back to the legacy Textual TUI." >&2
    RUN_LEGACY_TUI=1
elif [[ "${1:-}" == "--tui-legacy" ]]; then
    shift
    RUN_LEGACY_TUI=1
fi

if [[ -n "$RUN_LEGACY_TUI" ]]; then
    export MYHARNESS_ORIGINAL_CWD="$PWD"
    cd "$AGENT_DIR"
    exec "$PYTHON" harness_agent.py --tui "$@"
fi

export MYHARNESS_WEB_DATA_DIR="${MYHARNESS_WEB_DATA_DIR:-$SCRIPT_DIR/data}"
export MYHARNESS_WEB_STATIC_DIR="${MYHARNESS_WEB_STATIC_DIR:-$SCRIPT_DIR/frontend/dist}"
# Bind address precedence: MYHARNESS_WEB_HOST/PORT > server.* in the config >
# loopback. 0.0.0.0 is never the default: the agent API is unauthenticated.
if [ -z "${MYHARNESS_WEB_HOST:-}" ]; then
    export MYHARNESS_WEB_HOST="$(read_config server.host)"
    export MYHARNESS_WEB_HOST="${MYHARNESS_WEB_HOST:-127.0.0.1}"
fi
if [ -z "${MYHARNESS_WEB_PORT:-}" ]; then
    export MYHARNESS_WEB_PORT="$(read_config server.port)"
    export MYHARNESS_WEB_PORT="${MYHARNESS_WEB_PORT:-8420}"
fi

LOGFILE="$SCRIPT_DIR/myharness.log"
NPM_CACHE="${NPM_CACHE:-/tmp/npm-cache}"

install_frontend_deps() {
    if [ -f package-lock.json ]; then
        npm ci --cache "$NPM_CACHE" --legacy-peer-deps
    else
        npm install --cache "$NPM_CACHE" --legacy-peer-deps
    fi
}

install_electron_deps() {
    if [ -f package-lock.json ]; then
        npm ci --cache "$NPM_CACHE"
    else
        npm install --cache "$NPM_CACHE"
    fi
}

# Build frontend if dist/ is missing or source is newer than the last build
_needs_build=false
if [ -f "$SCRIPT_DIR/frontend/package.json" ]; then
    if [ ! -d "$SCRIPT_DIR/frontend/dist" ]; then
        _needs_build=true
    elif [ -d "$SCRIPT_DIR/frontend/src" ]; then
        _stale=$(find "$SCRIPT_DIR/frontend/src" -newer "$SCRIPT_DIR/frontend/dist/index.html" -type f -print -quit 2>/dev/null)
        [ -n "$_stale" ] && _needs_build=true
    fi
fi
if [ "$_needs_build" = true ]; then
    echo "Building frontend..."
    (cd "$SCRIPT_DIR/frontend" && install_frontend_deps && npx vite build)
fi

if [[ "${1:-}" == "--cli" ]]; then
    shift
    export MYHARNESS_ORIGINAL_CWD="$PWD"
    cd "$AGENT_DIR"
    exec "$PYTHON" harness_agent.py "$@"
elif [[ "${1:-}" == "--electron" || "${1:-}" == "--desktop" ]]; then
    shift
    if [ ! -d "$SCRIPT_DIR/electron/node_modules" ]; then
        echo "Installing Electron dependencies..."
        (cd "$SCRIPT_DIR/electron" && install_electron_deps)
    fi
    echo "Starting Electron desktop shell..."
    cd "$SCRIPT_DIR/electron"
    export MYHARNESS_PYTHON="${MYHARNESS_PYTHON:-$PYTHON}"
    exec npm start -- "$@"
elif [[ "${1:-}" == "--dev" ]]; then
    # Install frontend deps if needed
    if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
        echo "Installing frontend dependencies..."
        (cd "$SCRIPT_DIR/frontend" && install_frontend_deps)
    fi

    cleanup() {
        echo ""
        echo "Shutting down..."
        kill $BACKEND_PID $VITE_PID 2>/dev/null
        wait $BACKEND_PID $VITE_PID 2>/dev/null
    }
    trap cleanup EXIT INT TERM

    # Start backend
    cd "$SCRIPT_DIR/backend"
    "$PYTHON" -u web_app.py 2>&1 | tee "$LOGFILE" &
    BACKEND_PID=$!

    # Start Vite dev server
    cd "$SCRIPT_DIR/frontend"
    npx vite --host 2>&1 &
    VITE_PID=$!

    echo ""
    echo "  Backend:  http://$MYHARNESS_WEB_HOST:$MYHARNESS_WEB_PORT"
    echo "  Frontend: http://localhost:5173  (hot reload)"
    echo "  Press Ctrl+C to stop both."
    echo ""

    wait $BACKEND_PID $VITE_PID
else
    if [[ "${1:-}" == "--prod" ]]; then
        shift
    fi
    cd "$SCRIPT_DIR/backend"
    exec "$PYTHON" -u web_app.py "$@" 2>&1 | tee "$LOGFILE"
fi
