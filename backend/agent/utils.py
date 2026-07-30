"""Tool implementations, caching, compression, diff helpers, and config loading."""

import atexit
import hashlib
import base64
import json
import os
import re
import difflib
import fnmatch
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from urllib.parse import urlparse

import requests
import yaml
import skill_registry

try:
    import fitz
except ImportError:
    fitz = None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_yaml_config(path: str) -> dict:
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def nested_get(data: dict, keys: list, default=None):
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def config_int(config: dict, keys: list, default: int) -> int:
    return int(nested_get(config, keys, default))


def normalize_allowed_paths(value) -> list:
    if isinstance(value, list):
        return [str(path).strip() for path in value if str(path).strip()]
    if isinstance(value, str):
        return [path.strip() for path in value.split("|") if path.strip()]
    return []


# ---------------------------------------------------------------------------
# Config constants
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "agent_config.yaml")
CONFIG = load_yaml_config(CONFIG_PATH)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_URL = nested_get(CONFIG, ["api", "base_url"], "https://openrouter.ai/api/v1")
NATIVE_CONFIG_ENABLED = str(
    nested_get(CONFIG, ["api", "enabled"], "true")
).lower() in ("true", "1", "yes")
# MYHARNESS_API_KEY wins over the config file so keys can stay out of YAML.
API_KEY = os.environ.get("MYHARNESS_API_KEY", "").strip() or nested_get(CONFIG, ["api", "api_key"], "")
NATIVE_ENABLED = NATIVE_CONFIG_ENABLED and bool(str(API_KEY or "").strip()) and str(API_KEY).strip() != "YOUR_API_KEY_HERE"
NATIVE_API_TIMEOUT = config_int(CONFIG, ["api", "timeout_seconds"], 120)
API_PROVIDER = nested_get(CONFIG, ["api", "provider"], None)
# Token-level SSE streaming for assistant output (UIs that support deltas).
# Providers that reject stream=True fall back to a plain request per call.
API_STREAMING = str(nested_get(CONFIG, ["api", "streaming"], "true")).lower() in ("true", "1", "yes")
MODEL = nested_get(CONFIG, ["models", "default"], "MiniMaxAI/MiniMax-M2.5")
READ_MODEL = nested_get(CONFIG, ["models", "read"], MODEL)
WRITE_MODEL = nested_get(CONFIG, ["models", "write"], MODEL)
SUMMARY_MODEL = nested_get(CONFIG, ["models", "summary"], READ_MODEL)
COMPACT_THRESHOLD_TOKENS = config_int(CONFIG, ["memory", "compact_threshold_tokens"], 50000)
KEEP_RECENT_MESSAGES = config_int(CONFIG, ["memory", "keep_recent_messages"], 8)
CONTEXT_LIMIT_TOKENS = config_int(CONFIG, ["memory", "context_limit_tokens"], 200000)
MEMORY_SUMMARY_MARKER = "SESSION_MEMORY_SUMMARY"

ALLOWED_PATHS = normalize_allowed_paths(nested_get(CONFIG, ["permissions", "allowed_paths"], []))
if not ALLOWED_PATHS:
    ALLOWED_PATHS = [os.getcwd()]

MAX_FILE_SIZE = config_int(CONFIG, ["limits", "max_file_size"], 8000000)
# An empty logging.log_dir means "<repo>/logs"; relative paths resolve against
# the repo root so the working directory of the caller does not matter.
LOG_DIR = str(nested_get(CONFIG, ["logging", "log_dir"], "") or "").strip()
LOG_DIR = os.path.join(REPO_ROOT, "logs") if not LOG_DIR else os.path.abspath(os.path.join(REPO_ROOT, LOG_DIR))
LOG_ENABLED = str(nested_get(CONFIG, ["logging", "enabled"], "true")).lower() in ("true", "1", "yes")
LOG_LEVEL = str(nested_get(CONFIG, ["logging", "level"], "info") or "info").strip().lower()
LOG_RETENTION_DAYS = config_int(CONFIG, ["logging", "retention_days"], 30)
DATA_DIR = str(nested_get(CONFIG, ["storage", "data_dir"], "") or "").strip()
DATA_DIR = os.path.join(REPO_ROOT, "data") if not DATA_DIR else os.path.abspath(os.path.join(REPO_ROOT, DATA_DIR))
SESSION_ID = uuid.uuid4().hex[:12]


def prune_old_logs(now: float | None = None) -> int:
    """Delete expired regular log files under the configured log directory."""
    if not LOG_ENABLED or LOG_RETENTION_DAYS <= 0 or not os.path.isdir(LOG_DIR):
        return 0
    cutoff = (time.time() if now is None else now) - LOG_RETENTION_DAYS * 86400
    removed = 0
    for root, _dirs, files in os.walk(LOG_DIR):
        for filename in files:
            path = os.path.join(root, filename)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                continue
    return removed

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def build_openrouter_provider_preferences(
    base_url: str | None = None,
    configured_provider=None,
) -> dict | None:
    """Build exclusive OpenRouter provider routing preferences.

    A scalar provider and a provider list both become an ordered list. The
    configured order is preserved, and fallbacks outside that list are
    disabled. Non-OpenRouter endpoints intentionally ignore this setting so
    generic OpenAI-compatible servers never receive an unsupported field.
    """
    raw_url = str(BASE_URL if base_url is None else base_url).strip()
    if not raw_url:
        return None
    parsed = urlparse(raw_url if "://" in raw_url else f"//{raw_url}")
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if hostname != "openrouter.ai" and not hostname.endswith(".openrouter.ai"):
        return None

    value = API_PROVIDER if configured_provider is None else configured_provider
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple)):
        candidates = value
    else:
        return None

    providers = []
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        provider = candidate.strip()
        if provider and provider not in seen:
            providers.append(provider)
            seen.add(provider)
    if not providers:
        return None

    return {
        "order": providers,
        "allow_fallbacks": False,
    }

_approval_env = os.environ.get("MYHARNESS_APPROVAL_MODE", "").strip()
_APPROVAL_MODE_DEFAULT = str(
    nested_get(CONFIG, ["permissions", "approval_mode"], "always_ask")
).lower()
APPROVAL_MODE = _APPROVAL_MODE_DEFAULT
MAX_TOOL_OUTPUT = config_int(CONFIG, ["limits", "max_tool_output"], 12000)
DEFAULT_SHELL_TIMEOUT = config_int(CONFIG, ["shell", "default_timeout"], 120)
MAX_BACKGROUND_JOBS = config_int(CONFIG, ["shell", "max_background_jobs"], 10)
BACKGROUND_OUTPUT_MAX_LINES = config_int(CONFIG, ["shell", "background_output_max_lines"], 2000)
MAX_AGENT_ITERATIONS = config_int(CONFIG, ["agent", "max_iterations"], 20)
TOOL_CALL_CHECKPOINT = config_int(CONFIG, ["agent", "tool_call_checkpoint"], 20)
MAX_SEARCH_RESULTS = config_int(CONFIG, ["search", "max_results"], 100)

# Absolute Python interpreter recommended for shell_run python invocations.
# Defaults to the interpreter running the backend, which is already the one
# run.sh/run.cmd selected.
PYTHON_INTERPRETER = str(nested_get(CONFIG, ["python", "interpreter"], "")).strip() or sys.executable
MANAGED_TOOLS = str(nested_get(CONFIG, ["agent", "managed_tools"], "false")).lower() in ("true", "1", "yes")
DEFAULT_PROVIDER = str(nested_get(CONFIG, ["agent", "default_provider"], "native") or "native").strip()
RG_PATH = str(nested_get(CONFIG, ["search", "rg_path"], "")).strip()
UI_USE_RICH = str(nested_get(CONFIG, ["ui", "rich"], "auto")).lower()
_verbose_env = os.environ.get("MYHARNESS_VERBOSE_TOOLS", "").strip()
UI_VERBOSE_TOOLS = str(
    nested_get(CONFIG, ["ui", "verbose_tools"], "true")
).lower() in ("true", "1", "yes")
APP_NAME = str(nested_get(CONFIG, ["ui", "app_name"], "MyHarness")).strip() or "MyHarness"
SPLASH_ASCII = str(nested_get(CONFIG, ["ui", "splash_ascii"], "") or "")
GIT_WRITES_ENABLED = str(
    nested_get(CONFIG, ["ui", "git_writes_enabled"], "false")
).lower() in ("true", "1", "yes")

# Browser microphone transcription.
AUDIO_ENABLED = str(
    nested_get(CONFIG, ["audio", "enabled"], "false")
).lower() in ("true", "1", "yes")
AUDIO_TRANSCRIPTION_PROCESSOR = str(
    nested_get(CONFIG, ["audio", "transcription", "processor"], "local")
).strip().lower()
AUDIO_TRANSCRIPTION_SERVER = str(
    nested_get(CONFIG, ["audio", "transcription", "server"], "")
).strip()
AUDIO_TRANSCRIPTION_USERNAME = str(
    nested_get(CONFIG, ["audio", "transcription", "username"], "")
).strip()
AUDIO_TRANSCRIPTION_KEY_FILE = str(
    nested_get(CONFIG, ["audio", "transcription", "key_file"], "")
).strip()
AUDIO_TRANSCRIPTION_APP_DIR = str(
    nested_get(CONFIG, ["audio", "transcription", "app_dir"], "/opt/apps/whisperAudio")
).strip()
AUDIO_TRANSCRIPTION_MODEL = str(
    nested_get(CONFIG, ["audio", "transcription", "model"], "small")
).strip() or "small"
AUDIO_TRANSCRIPTION_LANGUAGE = str(
    nested_get(CONFIG, ["audio", "transcription", "language"], "")
).strip()
AUDIO_TRANSCRIPTION_DEVICE = str(
    nested_get(CONFIG, ["audio", "transcription", "device"], "cpu")
).strip().lower() or "cpu"
AUDIO_TRANSCRIPTION_API_BASE_URL = str(
    nested_get(CONFIG, ["audio", "transcription", "api_base_url"], "") or ""
).strip()
# MYHARNESS_STT_API_KEY wins over the config file so keys can stay out of YAML.
AUDIO_TRANSCRIPTION_API_KEY = (
    os.environ.get("MYHARNESS_STT_API_KEY", "").strip()
    or str(nested_get(CONFIG, ["audio", "transcription", "api_key"], "") or "").strip()
)
AUDIO_TRANSCRIPTION_TIMEOUT = config_int(CONFIG, ["audio", "transcription", "timeout_seconds"], 1800)
AUDIO_MAX_UPLOAD_MB = config_int(CONFIG, ["audio", "transcription", "max_upload_mb"], 500)

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".tool_cache")
CACHE_ENABLED = str(nested_get(CONFIG, ["cache", "enabled"], "true")).lower() in ("true", "1", "yes")
CACHE_MAX_AGE = config_int(CONFIG, ["cache", "max_age_seconds"], 3600)

CACHEABLE_TOOLS = frozenset({
    "file_read", "file_search", "file_list", "content_search",
    "skill_list", "skill_read",
})

TOOL_COMPRESS_THRESHOLD = config_int(CONFIG, ["memory", "tool_compress_threshold_chars"], 500)

# Codex app-server provider config
CODEX_APP_SERVER_ENABLED = str(
    nested_get(CONFIG, ["codex_app_server", "enabled"], "false")
).lower() in ("true", "1", "yes")
CODEX_APP_SERVER_BINARY = str(nested_get(CONFIG, ["codex_app_server", "binary"], "codex")).strip()
CODEX_APP_SERVER_LISTEN = str(nested_get(CONFIG, ["codex_app_server", "listen"], "stdio://")).strip()
CODEX_APP_SERVER_SANDBOX = str(nested_get(CONFIG, ["codex_app_server", "sandbox"], "workspace-write")).strip()
CODEX_APP_SERVER_APPROVAL_POLICY = str(
    nested_get(CONFIG, ["codex_app_server", "approval_policy"], "on-request")
).strip()
CODEX_APP_SERVER_TIMEOUT = config_int(CONFIG, ["codex_app_server", "timeout_seconds"], 1800)
CODEX_APP_SERVER_MODEL = nested_get(CONFIG, ["codex_app_server", "model"], None)
CODEX_APP_SERVER_REASONING_EFFORT = str(
    nested_get(CONFIG, ["codex_app_server", "reasoning_effort"], "low")
).strip()
CODEX_APP_SERVER_EXPERIMENTAL_API = str(
    nested_get(CONFIG, ["codex_app_server", "experimental_api"], "false")
).lower() in ("true", "1", "yes")

# Claude provider (Claude Agent SDK driving the local `claude` CLI)
CLAUDE_AGENT_ENABLED = str(
    nested_get(CONFIG, ["claude_agent", "enabled"], "false")
).lower() in ("true", "1", "yes")
CLAUDE_AGENT_BINARY = str(nested_get(CONFIG, ["claude_agent", "binary"], "claude")).strip()
CLAUDE_AGENT_MODEL = nested_get(CONFIG, ["claude_agent", "model"], None)
CLAUDE_AGENT_PERMISSION_MODE = str(
    nested_get(CONFIG, ["claude_agent", "permission_mode"], "") or ""
).strip()
CLAUDE_AGENT_TIMEOUT = config_int(CONFIG, ["claude_agent", "timeout_seconds"], 1800)
CLAUDE_AGENT_MAX_TURNS = config_int(CONFIG, ["claude_agent", "max_turns"], 0)

# Electron desktop shell config
DESKTOP_ENABLED = str(
    nested_get(CONFIG, ["desktop", "enabled"], "false")
).lower() in ("true", "1", "yes")
DESKTOP_BACKEND_URL = str(
    nested_get(CONFIG, ["desktop", "backend_url"], "http://127.0.0.1:8420")
).strip()
DESKTOP_PREFER_EXISTING_BACKEND = str(
    nested_get(CONFIG, ["desktop", "prefer_existing_backend"], "true")
).lower() in ("true", "1", "yes")
DESKTOP_START_LOCAL_BACKEND_FALLBACK = str(
    nested_get(CONFIG, ["desktop", "start_local_backend_fallback"], "true")
).lower() in ("true", "1", "yes")
DESKTOP_ELECTRON_ONLY = str(
    nested_get(CONFIG, ["desktop", "electron_only"], "false")
).lower() in ("true", "1", "yes")
DESKTOP_ALLOWED_FRONTEND_ORIGINS = [
    str(origin).strip()
    for origin in nested_get(CONFIG, ["desktop", "allowed_frontend_origins"], [])
    if str(origin).strip()
] if isinstance(nested_get(CONFIG, ["desktop", "allowed_frontend_origins"], []), list) else []

# Web server bind address. The MYHARNESS_WEB_HOST/MYHARNESS_WEB_PORT environment
# variables take precedence over these (run.sh, run.cmd, and web_app.main()).
SERVER_HOST = str(nested_get(CONFIG, ["server", "host"], "127.0.0.1") or "127.0.0.1").strip()
SERVER_PORT = config_int(CONFIG, ["server", "port"], 8420)


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

def validate_startup_config(host: str = "", port: int = 0) -> list:
    """Return human-readable warnings about a risky or incomplete config.

    Never raises: an unusual setup should still start, just loudly.
    """
    warnings = []
    resolved_host = (host or SERVER_HOST or "").strip()

    if not str(API_KEY or "").strip() or str(API_KEY).strip() in {"YOUR_API_KEY_HERE", "ll"}:
        if not (CODEX_APP_SERVER_ENABLED or CLAUDE_AGENT_ENABLED):
            warnings.append(
                "No api.api_key is set and neither codex_app_server nor claude_agent is "
                "enabled, so no agent provider can run. Set api.api_key in "
                "backend/agent/agent_config.yaml or export MYHARNESS_API_KEY."
            )

    # Validate the list the agent actually enforces, not the raw file: an
    # embedder (or register_allowed_path) can widen it after import, and
    # warning about paths that are not in effect would be misleading.
    configured_paths = ALLOWED_PATHS or normalize_allowed_paths(
        nested_get(CONFIG, ["permissions", "allowed_paths"], [])
    )
    if not configured_paths:
        warnings.append(
            "permissions.allowed_paths is empty, so the agent falls back to the current "
            f"working directory ({os.getcwd()}). List the workspaces you want to allow."
        )
    else:
        for path in configured_paths:
            if not os.path.isdir(os.path.expanduser(path)):
                warnings.append(f"permissions.allowed_paths entry does not exist: {path}")

    if APPROVAL_MODE == "auto_approve":
        warnings.append(
            "permissions.approval_mode is auto_approve: the agent may write files and run "
            "shell commands without asking. Use always_ask or shell_only unless you are sandboxed."
        )

    if resolved_host in {"0.0.0.0", "::"}:
        warnings.append(
            f"The backend binds {resolved_host}, exposing an UNAUTHENTICATED agent API to "
            "every host on your network. Bind 127.0.0.1 unless it is behind a trusted proxy."
        )

    return warnings


def print_startup_warnings(host: str = "", port: int = 0) -> list:
    warnings = validate_startup_config(host, port)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return warnings


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_key(tool_name: str, arguments: dict) -> str:
    extra = {}
    for key in ("file_path", "directory"):
        path = arguments.get(key)
        if path and os.path.exists(path):
            try:
                extra[f"_{key}_mtime"] = os.path.getmtime(path)
            except OSError:
                pass
    raw = json.dumps({"tool": tool_name, "args": arguments, **extra}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(tool_name: str, arguments: dict):
    if not CACHE_ENABLED or tool_name not in CACHEABLE_TOOLS:
        return None
    key = _cache_key(tool_name, arguments)
    path = os.path.join(CACHE_DIR, key)
    if not os.path.isfile(path):
        return None
    age = datetime.now().timestamp() - os.path.getmtime(path)
    if age > CACHE_MAX_AGE:
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _cache_put(tool_name: str, arguments: dict, result: str):
    if not CACHE_ENABLED or tool_name not in CACHEABLE_TOOLS:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _cache_key(tool_name, arguments)
    path = os.path.join(CACHE_DIR, key)
    with open(path, "w", encoding="utf-8") as f:
        f.write(result)


def _invalidate_cache():
    if not CACHE_ENABLED or not os.path.isdir(CACHE_DIR):
        return
    for fname in os.listdir(CACHE_DIR):
        try:
            os.remove(os.path.join(CACHE_DIR, fname))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def _resolve_path(p: str) -> str:
    """Resolve a path to an absolute canonical form.

    Uses abspath only — realpath mangles UNC paths on Windows by resolving
    mapped drives or changing prefix forms, causing path comparisons to fail.
    """
    return os.path.normpath(os.path.abspath(p))


def register_allowed_path(path: str) -> str:
    """Add a runtime-managed directory to the allowed paths.

    Used for directories the deployment provisions itself (for example the
    managed scripts directory under the web data dir) so file_write and
    shell_run may use them even when they sit outside the configured
    permissions.allowed_paths. Idempotent; returns the resolved path.
    """
    resolved = _resolve_path(path)
    if resolved not in ALLOWED_PATHS:
        ALLOWED_PATHS.append(resolved)
    return resolved


def is_path_allowed(path: str) -> bool:
    resolved = _resolve_path(path)
    for allowed in ALLOWED_PATHS:
        allowed_resolved = _resolve_path(allowed)
        try:
            if os.path.commonpath([resolved, allowed_resolved]) == allowed_resolved:
                return True
        except ValueError:
            continue
    return False


def validate_directory(directory: str):
    if not is_path_allowed(directory):
        return f"ERROR: Access denied. Directory '{directory}' is not in allowed paths."
    if not os.path.isdir(directory):
        return f"ERROR: Directory '{directory}' does not exist."
    return None


def ensure_allowed_file(file_path: str) -> str:
    resolved = _resolve_path(file_path)
    if not is_path_allowed(resolved):
        raise ValueError(f"Access denied. Path '{file_path}' is not in allowed paths.")
    return resolved


# ---------------------------------------------------------------------------
# Read-only tool implementations
# ---------------------------------------------------------------------------

def tool_file_search(directory: str, pattern: str, recursive: bool = True) -> str:
    error = validate_directory(directory)
    if error:
        return error

    matches = []
    max_results = 50
    if recursive:
        for root, dirs, files in os.walk(directory):
            for filename in fnmatch.filter(files, pattern):
                matches.append(os.path.join(root, filename))
                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break
    else:
        for filename in os.listdir(directory):
            full_path = os.path.join(directory, filename)
            if os.path.isfile(full_path) and fnmatch.fnmatch(filename, pattern):
                matches.append(full_path)
                if len(matches) >= max_results:
                    break

    if not matches:
        return f"No files matching '{pattern}' found in '{directory}'."
    return "Found {count} file(s):\n{items}".format(
        count=len(matches),
        items="".join(f"  {m} ({os.path.getsize(m)} bytes)\n" for m in matches),
    )


def get_rg_path() -> str:
    if RG_PATH:
        return RG_PATH if os.path.isfile(RG_PATH) else ""
    return shutil.which("rg") or ""


def _format_file_list(directory: str, matches: list, source: str) -> str:
    if not matches:
        return f"No files found in '{directory}'."

    top_files = []
    sub_groups: dict[str, list] = {}
    for full_path in matches:
        rel = os.path.relpath(full_path, directory)
        parts = rel.replace("\\", "/").split("/")
        if len(parts) == 1:
            top_files.append(parts[0])
        else:
            sub_dir = parts[0]
            sub_groups.setdefault(sub_dir, []).append("/".join(parts[1:]))

    lines = [f"Found {len(matches)} file(s) in '{directory}' ({source}):"]
    if top_files:
        for f in top_files:
            lines.append(f"  {f}")
    for sub_dir in sorted(sub_groups):
        items = sub_groups[sub_dir]
        lines.append(f"  {sub_dir}/  ({len(items)} files)")
        for f in items:
            lines.append(f"    {f}")
    return "\n".join(lines) + "\n"


def tool_file_list(directory: str, glob: str = None, max_results: int = MAX_SEARCH_RESULTS) -> str:
    error = validate_directory(directory)
    if error:
        return error

    limit = max(1, min(int(max_results or MAX_SEARCH_RESULTS), 1000))
    rg_path = get_rg_path()
    if rg_path:
        command = [rg_path, "--files"]
        if glob:
            command.extend(["--glob", glob])
        command.append(".")
        try:
            completed = subprocess.run(command, cwd=directory, text=True, capture_output=True, timeout=30, encoding="utf-8", errors="replace")
            if completed.returncode in (0, 1):
                files = completed.stdout.splitlines()[:limit]
                full_paths = [os.path.join(directory, item) for item in files]
                return _format_file_list(directory, full_paths, "rg")
        except Exception:
            pass

    matches = []
    for root, dirs, files in os.walk(directory):
        for filename in files:
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, directory)
            if glob and not fnmatch.fnmatch(rel_path, glob) and not fnmatch.fnmatch(filename, glob):
                continue
            matches.append(full_path)
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break

    return _format_file_list(directory, matches, "Python fallback")


def tool_content_search(
    directory: str,
    query: str,
    glob: str = None,
    case_sensitive: bool = False,
    max_results: int = MAX_SEARCH_RESULTS,
) -> str:
    error = validate_directory(directory)
    if error:
        return error

    limit = max(1, min(int(max_results or MAX_SEARCH_RESULTS), 1000))
    rg_path = get_rg_path()
    if rg_path:
        command = [rg_path, "--line-number", "--color", "never", "--max-count", str(limit)]
        if not case_sensitive:
            command.append("--ignore-case")
        if glob:
            command.extend(["--glob", glob])
        command.extend(["--", query, "."])
        try:
            completed = subprocess.run(command, cwd=directory, text=True, capture_output=True, timeout=30, encoding="utf-8", errors="replace")
            if completed.returncode == 0:
                lines = completed.stdout.splitlines()[:limit]
                return "Found {count} matching line(s) using rg:\n{items}".format(
                    count=len(lines),
                    items="\n".join(lines),
                )
            if completed.returncode == 1:
                return f"No matches for '{query}' in '{directory}'."
            return f"ERROR: rg failed with exit code {completed.returncode}: {completed.stderr}"
        except Exception:
            pass

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query, flags)
    except re.error:
        pattern = re.compile(re.escape(query), flags)

    matches = []
    for root, dirs, files in os.walk(directory):
        for filename in files:
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, directory)
            if glob and not fnmatch.fnmatch(rel_path, glob) and not fnmatch.fnmatch(filename, glob):
                continue
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    for line_number, line in enumerate(f, 1):
                        if pattern.search(line):
                            matches.append(f"{full_path}:{line_number}:{line.rstrip()}")
                            if len(matches) >= limit:
                                break
            except Exception:
                continue
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break

    if not matches:
        return f"No matches for '{query}' in '{directory}'."
    return "Found {count} matching line(s) using Python fallback:\n{items}".format(
        count=len(matches),
        items="\n".join(matches),
    )


# ---------------------------------------------------------------------------
# gather_context: parallel read-only context gathering
# ---------------------------------------------------------------------------

GATHER_JOB_TYPES = frozenset({"search", "test_discovery", "read_slices", "inventory"})
# Jobs that scan directories vs. jobs that read specific files.
GATHER_DIR_JOB_TYPES = frozenset({"search", "test_discovery", "inventory"})
GATHER_FILE_JOB_TYPES = frozenset({"read_slices"})
GATHER_DEFAULT_MAX_MATCHES = 24
GATHER_DEFAULT_TIMEOUT = 12
GATHER_DEFAULT_MAX_TOTAL_CHARS = 20_000
GATHER_DEFAULT_MAX_JOBS = 8
GATHER_DEFAULT_SLICE_CONTEXT = 6
GATHER_HARD_MAX_SLICE_CONTEXT = 30
# Hard ceilings so a single tool call cannot exhaust resources or context even
# if the model asks for a large budget.
GATHER_HARD_MAX_JOBS = 16
GATHER_HARD_MAX_TIMEOUT = 60
GATHER_HARD_MAX_TOTAL_CHARS = 60_000
# Max concurrent gather_context workers. Configurable; clamped so a bad config
# cannot spawn an unbounded thread pool. The effective count is further bounded
# by the number of runnable jobs in a batch.
GATHER_MAX_WORKERS = max(1, min(config_int(CONFIG, ["gather", "max_workers"], 8), 32))
GATHER_PER_JOB_TIMEOUT = 30

_TEST_FILENAME_RE = re.compile(r"(?:^test_.*\.py$|.*_test\.py$|.*\.test\.[jt]sx?$|.*\.spec\.[jt]sx?$)", re.IGNORECASE)

# Deterministic inventory extractors. Each kind maps a file-glob predicate to a
# regex whose first non-empty group is the inventory item.
_INVENTORY_ROUTE_RE = re.compile(r"@\w+\.(get|post|put|patch|delete|websocket)\(\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_INVENTORY_ENV_RE = re.compile(
    r"(?:os\.environ(?:\.get)?\(\s*[\"']([A-Z0-9_]+)[\"']"
    r"|os\.environ\[\s*[\"']([A-Z0-9_]+)[\"']"
    r"|getenv\(\s*[\"']([A-Z0-9_]+)[\"']"
    r"|(?:process|import\.meta)\.env\.([A-Za-z0-9_]+))"
)
_INVENTORY_EVENT_RE = re.compile(r"(?:type:\s*[\"']([A-Z0-9_]+)[\"']|case\s+[\"']([A-Za-z0-9_.]+)[\"']\s*:)")
_INVENTORY_SLASH_RE = re.compile(r"[\"'](/[a-z][a-z0-9_-]*)[\"']", re.IGNORECASE)
_INVENTORY_CONFIG_RE = re.compile(r"nested_get\(\s*\w+,\s*\[([^\]]+)\]")
_INVENTORY_KINDS = ("routes", "env", "events", "slash", "config")


def _gather_clamp(value, default, low, high) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def _gather_compile_patterns(patterns: list) -> list:
    """Compile patterns as case-insensitive regex, falling back to literal."""
    compiled = []
    for pattern in patterns:
        text = str(pattern)
        try:
            compiled.append(re.compile(text, re.IGNORECASE))
        except re.error:
            compiled.append(re.compile(re.escape(text), re.IGNORECASE))
    return compiled


def _gather_search_rg(directory: str, patterns: list, glob: str, max_matches: int) -> list | None:
    rg_path = get_rg_path()
    if not rg_path:
        return None
    command = [rg_path, "--line-number", "--color", "never", "--ignore-case", "--max-count", str(max_matches)]
    if glob:
        command.extend(["--glob", glob])
    for pattern in patterns:
        command.extend(["-e", str(pattern)])
    command.append(".")
    try:
        completed = subprocess.run(
            command, cwd=directory, text=True, capture_output=True,
            timeout=GATHER_PER_JOB_TIMEOUT, encoding="utf-8", errors="replace",
        )
    except Exception:
        return None
    if completed.returncode not in (0, 1):
        return None
    findings = []
    for line in completed.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        rel, line_no, text = parts
        try:
            line_number = int(line_no)
        except ValueError:
            continue
        findings.append({
            "file": os.path.normpath(os.path.join(directory, rel)),
            "line": line_number,
            "kind": "match",
            "text": text.strip()[:240],
        })
        if len(findings) >= max_matches:
            break
    return findings


def _gather_search_python(directory: str, patterns: list, glob: str, max_matches: int) -> list:
    compiled = _gather_compile_patterns(patterns)
    findings = []
    for root, _dirs, files in os.walk(directory):
        for filename in files:
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, directory)
            if glob and not fnmatch.fnmatch(rel_path, glob) and not fnmatch.fnmatch(filename, glob):
                continue
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as handle:
                    for line_number, line in enumerate(handle, 1):
                        if any(pattern.search(line) for pattern in compiled):
                            findings.append({
                                "file": os.path.normpath(full_path),
                                "line": line_number,
                                "kind": "match",
                                "text": line.strip()[:240],
                            })
                            if len(findings) >= max_matches:
                                return findings
            except OSError:
                continue
    return findings


def _gather_run_search(job: dict) -> dict:
    name = str(job.get("name") or "search")
    patterns = [str(p) for p in (job.get("patterns") or []) if str(p)]
    glob = job.get("glob")
    max_matches = _gather_clamp(job.get("max_matches"), GATHER_DEFAULT_MAX_MATCHES, 1, 200)
    findings: list = []
    warnings: list = []
    for directory in job.get("_valid_paths", []):
        remaining = max_matches - len(findings)
        if remaining <= 0:
            warnings.append("Match cap reached; some paths were not fully searched.")
            break
        results = _gather_search_rg(directory, patterns, glob, remaining)
        if results is None:
            results = _gather_search_python(directory, patterns, glob, remaining)
        findings.extend(results)
    return {"name": name, "type": "search", "status": "ok", "findings": findings, "warnings": warnings}


def _gather_run_test_discovery(job: dict) -> dict:
    name = str(job.get("name") or "test_discovery")
    compiled = _gather_compile_patterns([str(p) for p in (job.get("patterns") or []) if str(p)])
    max_matches = _gather_clamp(job.get("max_matches"), GATHER_DEFAULT_MAX_MATCHES, 1, 200)
    findings: list = []
    warnings: list = []
    commands: set = set()
    for directory in job.get("_valid_paths", []):
        for root, _dirs, files in os.walk(directory):
            for filename in files:
                if not _TEST_FILENAME_RE.match(filename):
                    continue
                full_path = os.path.join(root, filename)
                reason = "test file name"
                relevant = not compiled
                if compiled and any(pattern.search(filename) for pattern in compiled):
                    relevant = True
                    reason = "name matches pattern"
                elif compiled:
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="replace") as handle:
                            head = handle.read(40_000)
                        if any(pattern.search(head) for pattern in compiled):
                            relevant = True
                            reason = "content matches pattern"
                    except OSError:
                        continue
                if not relevant:
                    continue
                findings.append({
                    "file": os.path.normpath(full_path),
                    "line": 1,
                    "kind": "test_file",
                    "text": reason,
                })
                if filename.endswith(".py"):
                    commands.add("python -m unittest discover -s tests")
                elif _TEST_FILENAME_RE.match(filename):
                    commands.add("npm test")
                if len(findings) >= max_matches:
                    warnings.append("Match cap reached; some test files were not reported.")
                    break
            if len(findings) >= max_matches:
                break
    return {
        "name": name,
        "type": "test_discovery",
        "status": "ok",
        "findings": findings,
        "warnings": warnings,
        "candidate_commands": sorted(commands),
    }


def _read_file_lines(path: str) -> list | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines()
    except OSError:
        return None


def _gather_run_read_slices(job: dict) -> dict:
    """Read narrow windows around symbol matches or explicit line numbers in files."""
    name = str(job.get("name") or "read_slices")
    patterns = _gather_compile_patterns([str(p) for p in (job.get("patterns") or []) if str(p)])
    at_lines = [int(n) for n in (job.get("at_lines") or []) if isinstance(n, (int, float)) and not isinstance(n, bool)]
    context = _gather_clamp(job.get("context"), GATHER_DEFAULT_SLICE_CONTEXT, 0, GATHER_HARD_MAX_SLICE_CONTEXT)
    max_matches = _gather_clamp(job.get("max_matches"), GATHER_DEFAULT_MAX_MATCHES, 1, 200)
    findings: list = []
    warnings: list = []

    def add_slice(path, lines, anchor):
        lo = max(0, anchor - 1 - context)
        hi = min(len(lines), anchor + context)
        window = "\n".join(f"{i + 1}: {lines[i]}" for i in range(lo, hi))
        findings.append({"file": os.path.normpath(path), "line": anchor, "kind": "slice", "text": window})

    for path in job.get("_valid_paths", []):
        if len(findings) >= max_matches:
            warnings.append("Slice cap reached; some files were not read.")
            break
        lines = _read_file_lines(path)
        if lines is None:
            warnings.append(f"Could not read '{path}'.")
            continue
        anchors: list = []
        for ln in at_lines:
            if 1 <= ln <= len(lines):
                anchors.append(ln)
            else:
                warnings.append(f"Line {ln} out of range in '{os.path.basename(path)}'.")
        if patterns:
            for idx, text in enumerate(lines, 1):
                if any(p.search(text) for p in patterns):
                    anchors.append(idx)
        for anchor in sorted(set(anchors)):
            if len(findings) >= max_matches:
                break
            add_slice(path, lines, anchor)
    return {"name": name, "type": "read_slices", "status": "ok", "findings": findings, "warnings": warnings}


def _gather_run_inventory(job: dict) -> dict:
    """Extract structured inventories (routes, env vars, events, slash commands, config keys)."""
    name = str(job.get("name") or "inventory")
    requested = {str(p).strip().lower() for p in (job.get("patterns") or []) if str(p).strip()}
    kinds = [k for k in _INVENTORY_KINDS if k in requested] if requested else list(_INVENTORY_KINDS)
    glob = job.get("glob")
    max_matches = _gather_clamp(job.get("max_matches"), GATHER_DEFAULT_MAX_MATCHES, 1, 200)
    findings: list = []
    warnings: list = []
    if requested - set(_INVENTORY_KINDS):
        warnings.append(f"Ignored unknown inventory kinds: {', '.join(sorted(requested - set(_INVENTORY_KINDS)))}.")

    def first_group(match):
        return next((g for g in match.groups() if g), None)

    def scan(path, line_no, text):
        for kind in kinds:
            if kind == "routes" and path.endswith(".py"):
                m = _INVENTORY_ROUTE_RE.search(text)
                if m:
                    findings.append({"file": os.path.normpath(path), "line": line_no, "kind": "route", "text": f"{m.group(1).upper()} {m.group(2)}"})
            elif kind == "env":
                m = _INVENTORY_ENV_RE.search(text)
                if m:
                    findings.append({"file": os.path.normpath(path), "line": line_no, "kind": "env", "text": first_group(m)})
            elif kind == "events" and path.endswith((".js", ".jsx", ".ts", ".tsx")):
                m = _INVENTORY_EVENT_RE.search(text)
                if m:
                    findings.append({"file": os.path.normpath(path), "line": line_no, "kind": "event", "text": first_group(m)})
            elif kind == "slash":
                m = _INVENTORY_SLASH_RE.search(text)
                if m:
                    findings.append({"file": os.path.normpath(path), "line": line_no, "kind": "slash", "text": m.group(1)})
            elif kind == "config" and path.endswith(".py"):
                m = _INVENTORY_CONFIG_RE.search(text)
                if m:
                    findings.append({"file": os.path.normpath(path), "line": line_no, "kind": "config", "text": " ".join(m.group(1).split())})

    for directory in job.get("_valid_paths", []):
        for root, _dirs, files in os.walk(directory):
            for filename in files:
                if glob and not fnmatch.fnmatch(filename, glob):
                    continue
                full_path = os.path.join(root, filename)
                lines = _read_file_lines(full_path)
                if lines is None:
                    continue
                for line_no, text in enumerate(lines, 1):
                    scan(full_path, line_no, text)
                    if len(findings) >= max_matches:
                        break
                if len(findings) >= max_matches:
                    warnings.append("Match cap reached; inventory is partial.")
                    break
            if len(findings) >= max_matches:
                break
    return {"name": name, "type": "inventory", "status": "ok", "findings": findings, "warnings": warnings}


def _gather_run_job(job: dict) -> dict:
    job_type = job.get("type")
    if job_type == "search":
        return _gather_run_search(job)
    if job_type == "test_discovery":
        return _gather_run_test_discovery(job)
    if job_type == "read_slices":
        return _gather_run_read_slices(job)
    if job_type == "inventory":
        return _gather_run_inventory(job)
    return {
        "name": str(job.get("name") or "job"),
        "type": str(job_type),
        "status": "error",
        "findings": [],
        "warnings": [],
        "error": f"Unsupported job type '{job_type}'.",
    }


def _gather_validate_job(job, index: int) -> dict:
    """Normalize one job, attaching validated paths or an error placeholder."""
    if not isinstance(job, dict):
        return {"name": f"job_{index}", "type": "unknown", "status": "error",
                "findings": [], "warnings": [], "error": "Job must be an object.", "_invalid": True}
    name = str(job.get("name") or f"job_{index}")
    job_type = job.get("type")
    result = {"name": name, "type": str(job_type), "status": "error", "findings": [], "warnings": []}
    if job_type not in GATHER_JOB_TYPES:
        result["error"] = f"Unsupported job type '{job_type}'. Allowed: {', '.join(sorted(GATHER_JOB_TYPES))}."
        result["_invalid"] = True
        return result
    raw_paths = job.get("paths")
    patterns = job.get("patterns")
    has_patterns = isinstance(patterns, list) and bool([p for p in patterns if str(p)])
    has_lines = bool(job.get("at_lines"))
    if not isinstance(raw_paths, list) or not raw_paths:
        result["error"] = "Job requires a non-empty 'paths' list."
        result["_invalid"] = True
        return result
    # Pattern requirements differ by job type:
    #  - search/test_discovery need patterns
    #  - read_slices needs patterns OR at_lines
    #  - inventory: patterns optional (they select inventory kinds)
    if job_type == "read_slices" and not has_patterns and not has_lines:
        result["error"] = "read_slices requires a non-empty 'patterns' list or 'at_lines'."
        result["_invalid"] = True
        return result
    if job_type in ("search", "test_discovery") and not has_patterns:
        result["error"] = "Job requires a non-empty 'patterns' list."
        result["_invalid"] = True
        return result

    wants_files = job_type in GATHER_FILE_JOB_TYPES
    valid_paths = []
    warnings = []
    for path in raw_paths:
        candidate = str(path)
        if not is_path_allowed(candidate):
            warnings.append(f"Path '{candidate}' is not in allowed paths; skipped.")
        elif wants_files and not os.path.isfile(candidate):
            warnings.append(f"Path '{candidate}' is not a file; skipped.")
        elif not wants_files and not os.path.isdir(candidate):
            warnings.append(f"Path '{candidate}' is not a directory; skipped.")
        else:
            valid_paths.append(candidate)
    if not valid_paths:
        kind = "files" if wants_files else "directories"
        result["error"] = f"No valid allowed {kind} in 'paths'."
        result["warnings"] = warnings
        result["_invalid"] = True
        return result
    normalized = dict(job)
    normalized["name"] = name
    normalized["_valid_paths"] = valid_paths
    normalized["_path_warnings"] = warnings
    return {"_normalized": normalized}


def _gather_apply_char_budget(jobs: list, max_total_chars: int) -> bool:
    """Trim findings across jobs so total finding text stays within budget."""
    truncated = False
    used = 0
    for job in jobs:
        kept = []
        for finding in job.get("findings", []):
            cost = len(str(finding.get("text", ""))) + len(str(finding.get("file", ""))) + 16
            if used + cost > max_total_chars:
                truncated = True
                job.setdefault("warnings", []).append("Output truncated to satisfy total character budget.")
                break
            used += cost
            kept.append(finding)
        job["findings"] = kept
    return truncated


def tool_gather_context(jobs: list, budget: dict | None = None) -> str:
    if not isinstance(jobs, list) or not jobs:
        return "ERROR: gather_context requires a non-empty 'jobs' list."
    budget = budget if isinstance(budget, dict) else {}
    max_jobs = _gather_clamp(budget.get("max_jobs"), GATHER_DEFAULT_MAX_JOBS, 1, GATHER_HARD_MAX_JOBS)
    timeout_seconds = _gather_clamp(budget.get("timeout_seconds"), GATHER_DEFAULT_TIMEOUT, 1, GATHER_HARD_MAX_TIMEOUT)
    max_total_chars = _gather_clamp(
        budget.get("max_total_chars"), GATHER_DEFAULT_MAX_TOTAL_CHARS, 2_000, GATHER_HARD_MAX_TOTAL_CHARS
    )

    top_warnings = []
    if len(jobs) > max_jobs:
        top_warnings.append(f"Received {len(jobs)} jobs; only the first {max_jobs} were run.")
        jobs = jobs[:max_jobs]

    results: list = [None] * len(jobs)
    runnable: list = []  # (index, normalized_job)
    for index, job in enumerate(jobs):
        validated = _gather_validate_job(job, index)
        if "_normalized" in validated:
            runnable.append((index, validated["_normalized"]))
        else:
            validated.pop("_invalid", None)
            results[index] = validated

    if runnable:
        deadline = time.monotonic() + timeout_seconds
        executor = ThreadPoolExecutor(max_workers=min(len(runnable), GATHER_MAX_WORKERS))
        future_map = {executor.submit(_gather_run_job, job): (index, job) for index, job in runnable}
        try:
            for future, (index, job) in future_map.items():
                remaining = deadline - time.monotonic()
                try:
                    job_result = future.result(timeout=max(0.0, remaining))
                except FuturesTimeoutError:
                    job_result = {
                        "name": job.get("name", f"job_{index}"), "type": job.get("type"),
                        "status": "timeout", "findings": [], "warnings": [],
                        "error": f"Job exceeded the {timeout_seconds}s batch budget.",
                    }
                except Exception as exc:  # localize a worker failure to its own job
                    job_result = {
                        "name": job.get("name", f"job_{index}"), "type": job.get("type"),
                        "status": "error", "findings": [], "warnings": [],
                        "error": f"Worker failed: {exc}",
                    }
                path_warnings = job.get("_path_warnings") or []
                if path_warnings:
                    job_result.setdefault("warnings", []).extend(path_warnings)
                results[index] = job_result
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    job_results = [r for r in results if r is not None]
    truncated = _gather_apply_char_budget(job_results, max_total_chars)

    suggested = []
    seen = set()
    for job in job_results:
        for finding in job.get("findings", []):
            ref = f"{finding.get('file')}:{finding.get('line')}"
            if ref not in seen:
                seen.add(ref)
                suggested.append(ref)
            if len(suggested) >= 12:
                break
        if len(suggested) >= 12:
            break

    ok_jobs = sum(1 for j in job_results if j.get("status") == "ok")
    total_findings = sum(len(j.get("findings", [])) for j in job_results)
    summary = f"Ran {len(job_results)} job(s); {ok_jobs} ok, {total_findings} finding(s)."

    payload = {
        "summary": summary,
        "jobs": job_results,
        "suggested_next_reads": suggested,
        "truncated": truncated,
    }
    if top_warnings:
        payload["warnings"] = top_warnings
    return json.dumps(payload, ensure_ascii=False, indent=2)


def tool_file_read(file_path: str, read_mode: str = "full", lines: int = 200, offset: int = 0, query: str = "") -> str:
    if not is_path_allowed(file_path):
        return f"ERROR: Access denied. Path '{file_path}' is not in allowed paths."
    if not os.path.isfile(file_path):
        return f"ERROR: File '{file_path}' does not exist."

    file_size = os.path.getsize(file_path)
    is_partial = read_mode in ("head", "tail", "range")
    is_search = read_mode == "search"
    ext = os.path.splitext(file_path)[1].lower()

    image_mime = _detect_supported_image_mime(file_path)
    if image_mime:
        return (
            f"ERROR: '{file_path}' is a {image_mime} binary image and cannot be read as text. "
            "Use image_read to attach it for visual inspection."
        )
    if ext != ".pdf" and _looks_like_binary_file(file_path):
        return f"ERROR: Binary file '{file_path}' is not supported by file_read."

    if is_search and not query:
        return "ERROR: read_mode='search' requires a 'query' parameter."

    try:
        if ext == ".pdf":
            if fitz is None:
                return "ERROR: PDF reading requires PyMuPDF (fitz), which is not installed."
            doc = fitz.open(file_path)
            num_pages = len(doc)
            max_pdf_pages = 20
            if is_search:
                matching_pages = []
                query_lower = query.lower()
                for page_num in range(num_pages):
                    page = doc[page_num]
                    if query_lower in page.get_text().lower():
                        matching_pages.append(page_num)
                if not matching_pages:
                    doc.close()
                    return f"No matches for '{query}' in '{file_path}' ({num_pages} pages)."
                capped = matching_pages[:max_pdf_pages]
                text = ""
                for page_num in capped:
                    text += f"\n--- Page {page_num + 1} ---\n{doc[page_num].get_text()}"
                doc.close()
                page_list = ", ".join(str(p + 1) for p in matching_pages)
                header = (
                    f"--- {file_path} ({num_pages} pages) ---\n"
                    f"Found '{query}' on {len(matching_pages)} page(s): {page_list}\n"
                    f"Showing content of {'first ' if len(matching_pages) > max_pdf_pages else ''}"
                    f"{len(capped)} matching page(s):\n"
                )
                return truncate_output(header + text, spill=True)
            if is_partial:
                capped_lines = min(lines, max_pdf_pages)
                start_page = offset if read_mode == "range" else (max(0, num_pages - capped_lines) if read_mode == "tail" else 0)
                end_page = min(num_pages, start_page + capped_lines)
                text = ""
                for page_num in range(start_page, end_page):
                    text += f"\n--- Page {page_num + 1} ---\n{doc[page_num].get_text()}"
                doc.close()
                if not text.strip():
                    return f"WARNING: PDF '{file_path}' contains no extractable text. Pages: {num_pages}"
                header = f"--- {file_path} ({num_pages} pages, showing {start_page + 1}-{end_page}) ---\n"
                return truncate_output(header + text, spill=True)
            if file_size > MAX_FILE_SIZE:
                doc.close()
                return (
                    f"PDF too large for full read ({file_size} bytes, {num_pages} pages). "
                    f"Re-call with read_mode='tail' or 'head' and lines=N to read specific pages, "
                    f"or read_mode='search' with a query to find specific content."
                )
            text = ""
            for page_num, page in enumerate(doc, 1):
                text += f"\n--- Page {page_num} ---\n{page.get_text()}"
            doc.close()
            if not text.strip():
                return f"WARNING: PDF '{file_path}' contains no extractable text. Pages: {num_pages}"
            return f"--- Contents of {file_path} ({num_pages} pages) ---\n{text}"

        if not is_partial and not is_search and file_size > MAX_FILE_SIZE:
            return (
                f"File too large for full read ({file_size} bytes, {MAX_FILE_SIZE} max). "
                f"Re-call with read_mode='tail' or 'head' and a lines count to read a portion, "
                f"or read_mode='search' with a query to find specific content."
            )

        content = _read_text_file(file_path)

        if is_search:
            context_lines = lines if lines != 200 else 5
            all_lines = content.splitlines(keepends=True)
            total = len(all_lines)
            query_lower = query.lower()
            match_indices = [i for i, line in enumerate(all_lines) if query_lower in line.lower()]
            if not match_indices:
                return f"No matches for '{query}' in '{file_path}' ({total} lines)."
            chunks = []
            seen = set()
            for idx in match_indices:
                start = max(0, idx - context_lines)
                end = min(total, idx + context_lines + 1)
                for i in range(start, end):
                    if i not in seen:
                        seen.add(i)
                        marker = " >> " if i == idx else "    "
                        chunks.append(f"{marker}{i + 1}: {all_lines[i].rstrip()}")
                chunks.append("")
            header = (
                f"--- {file_path} ({file_size} bytes, {total} lines) ---\n"
                f"Found '{query}' on {len(match_indices)} line(s) (showing {context_lines} context lines):\n\n"
            )
            return truncate_output(header + "\n".join(chunks), spill=True)

        _SEARCH_HINT_THRESHOLD = 500

        if is_partial:
            all_lines = content.splitlines(keepends=True)
            total = len(all_lines)
            if read_mode == "head":
                selected = all_lines[:lines]
                desc = f"first {len(selected)} of {total} lines"
            elif read_mode == "tail":
                selected = all_lines[-lines:]
                desc = f"last {len(selected)} of {total} lines"
            else:
                selected = all_lines[offset:offset + lines]
                desc = f"lines {offset}-{offset + len(selected) - 1} of {total}"
            content = "".join(selected)
            hint = ""
            if total > _SEARCH_HINT_THRESHOLD:
                hint = (
                    f"\n\nHint: This file has {total} lines. If you are looking for specific content, "
                    "use read_mode='search' with a query instead of reading chunks sequentially."
                )
            return f"--- {file_path} ({file_size} bytes, {desc}) ---\n{content}{hint}"

        total_lines = content.count("\n") + 1
        hint = ""
        if total_lines > _SEARCH_HINT_THRESHOLD:
            hint = (
                f"\n\nHint: This file has {total_lines} lines. Next time, use read_mode='search' "
                "with a query to jump directly to relevant content instead of reading the full file."
            )
        return f"--- Contents of {file_path} ({file_size} bytes) ---\n{content}{hint}"
    except Exception as e:
        return f"ERROR: Could not read file: {e}"


LOCAL_IMAGE_RESULT_MARKER = "MYHARNESS_LOCAL_IMAGE_JSON:"
MAX_LOCAL_IMAGE_BYTES = 10_000_000
SUPPORTED_LOCAL_IMAGE_MIMES = {"image/gif", "image/jpeg", "image/png", "image/webp"}


def _detect_supported_image_mime(file_path: str) -> str | None:
    try:
        with open(file_path, "rb") as handle:
            header = handle.read(16)
    except OSError:
        return None
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def _looks_like_binary_file(file_path: str) -> bool:
    try:
        with open(file_path, "rb") as handle:
            sample = handle.read(4096)
    except OSError:
        return False
    if sample.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return False
    if b"\x00" in sample:
        return True
    text = _decode_text_bytes(sample)
    if text is None:
        return True
    disallowed_controls = sum(char < " " and char not in "\b\t\n\f\r" for char in text)
    return bool(text) and disallowed_controls / len(text) > 0.01


def _decode_text_bytes(data: bytes) -> str | None:
    """Decode supported text encodings without treating Windows text as binary."""
    if data.startswith(b"\xef\xbb\xbf"):
        encodings = ("utf-8-sig",)
    elif data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encodings = ("utf-32",)
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8", "cp1252")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _read_text_file(file_path: str) -> str:
    with open(file_path, "rb") as handle:
        data = handle.read()
    text = _decode_text_bytes(data)
    if text is None:
        raise UnicodeError("File is not valid UTF-8, UTF-16, UTF-32, or Windows-1252 text.")
    return text


def tool_image_read(file_path: str) -> str:
    if not is_path_allowed(file_path):
        return f"ERROR: Access denied. Path '{file_path}' is not in allowed paths."
    if not os.path.isfile(file_path):
        return f"ERROR: File '{file_path}' does not exist."
    mime = _detect_supported_image_mime(file_path)
    if mime not in SUPPORTED_LOCAL_IMAGE_MIMES:
        return "ERROR: image_read supports only GIF, JPEG, PNG, and WebP files."
    size = os.path.getsize(file_path)
    if size > MAX_LOCAL_IMAGE_BYTES:
        return f"ERROR: Image is too large ({size} bytes, {MAX_LOCAL_IMAGE_BYTES} max)."
    metadata = {"path": os.path.abspath(file_path), "mime": mime, "name": os.path.basename(file_path), "size": size}
    return (
        f"Image ready for visual inspection: {metadata['path']} ({mime}, {size} bytes).\n"
        f"{LOCAL_IMAGE_RESULT_MARKER}{json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))}"
    )


def image_attachment_from_tool_result(result: str) -> tuple[str, dict[str, str] | None]:
    text = str(result or "")
    marker_index = text.rfind(LOCAL_IMAGE_RESULT_MARKER)
    if marker_index < 0:
        return text, None
    visible = text[:marker_index].rstrip()
    try:
        metadata = json.loads(text[marker_index + len(LOCAL_IMAGE_RESULT_MARKER):].strip())
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("The local image tool returned invalid metadata.") from exc
    path = str(metadata.get("path") or "")
    if not is_path_allowed(path) or not os.path.isfile(path):
        raise ValueError("The local image is missing or outside the allowed paths.")
    mime = _detect_supported_image_mime(path)
    size = os.path.getsize(path)
    if mime not in SUPPORTED_LOCAL_IMAGE_MIMES or size > MAX_LOCAL_IMAGE_BYTES:
        raise ValueError("The local image is no longer a supported bounded image.")
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return visible, {
        "data": f"data:{mime};base64,{encoded}",
        "mime": mime,
        "name": os.path.basename(path),
    }


_PLAN_STATUSES = {"pending", "in_progress", "completed"}
MAX_PLAN_ITEMS = 50


def normalize_plan_items(items) -> list[dict]:
    """Validate and normalize plan_update's items; raises ValueError with a
    caller-facing message on anything malformed."""
    if not isinstance(items, list) or not items:
        raise ValueError("'items' must be a non-empty list of {content, status} objects.")
    if len(items) > MAX_PLAN_ITEMS:
        raise ValueError(f"Too many plan items (max {MAX_PLAN_ITEMS}).")
    normalized = []
    for i, raw in enumerate(items):
        if not isinstance(raw, dict) or not str(raw.get("content", "")).strip():
            raise ValueError(f"Item {i} is missing non-empty 'content'.")
        status = raw.get("status", "pending")
        if status not in _PLAN_STATUSES:
            raise ValueError(f"Item {i} has invalid status '{status}'. Use one of: {', '.join(sorted(_PLAN_STATUSES))}.")
        normalized.append({"content": str(raw["content"]).strip(), "status": status})
    return normalized


def tool_plan_update(items) -> str:
    try:
        normalized = normalize_plan_items(items)
    except ValueError as e:
        return f"ERROR: {e}"
    done = sum(1 for item in normalized if item["status"] == "completed")
    summary = f"Plan updated: {len(normalized)} step(s), {done} completed."
    if done < len(normalized):
        summary += (
            " Call plan_update again as soon as a step finishes — mark it completed before you"
            " start the next one, and keep at most one step in_progress."
        )
    return summary


# How many tool calls may go by after a plan_update before the agent is
# reminded that its published plan is stale.
PLAN_REMINDER_AFTER_TOOL_CALLS = 6


def plan_reminder_message(items: list[dict]) -> str | None:
    """Nudge text for a published plan with unfinished steps, or None if the
    plan is already fully completed."""
    pending = [item for item in items if item["status"] != "completed"]
    if not pending:
        return None
    lines = "\n".join(f"- [{item['status']}] {item['content']}" for item in items)
    return (
        "[PLAN REMINDER] Your visible plan still shows unfinished steps:\n"
        f"{lines}\n"
        "If any of them are actually done, call plan_update now with the full updated plan so the "
        "user sees the real progress. Mark the step you are working on as in_progress. Do not "
        "mention this reminder in your reply."
    )


def tool_web_request(url: str, method: str = "GET") -> str:
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return "ERROR: Invalid URL format. Must include http:// or https://"
    except Exception as e:
        return f"ERROR: Invalid URL: {e}"

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, timeout=15)
        else:
            return "ERROR: Method must be GET or POST"
        response.raise_for_status()
        content = response.text[:MAX_FILE_SIZE]
        if len(response.text) > MAX_FILE_SIZE:
            content += f"\n... (truncated, total {len(response.text)} bytes)"
        return f"--- Content from {url} ---\n{content}"
    except Exception as e:
        return f"ERROR: Could not fetch URL: {e}"


# ---------------------------------------------------------------------------
# Patch parsing
# ---------------------------------------------------------------------------

def split_patch_hunks(lines: list) -> list:
    """Split an Update File section into hunks at @@ separators.

    Each @@ marks a new, possibly non-contiguous region of the target file, so
    hunks must be matched and applied independently rather than joined into one
    contiguous context block.
    """
    hunks = []
    current = []
    for line in lines:
        if line.startswith("@@"):
            if current:
                hunks.append(current)
            current = []
        else:
            current.append(line)
    if current:
        hunks.append(current)
    return [hunk for hunk in hunks if any(item.strip() for item in hunk)]


def parse_patch_lines(lines: list) -> tuple:
    old_parts = []
    new_parts = []
    changed = False
    for line in lines:
        if line.startswith("@@"):
            continue
        if line.startswith("-"):
            old_parts.append(line[1:])
            changed = True
        elif line.startswith("+"):
            new_parts.append(line[1:])
            changed = True
        elif line.startswith(" "):
            old_parts.append(line[1:])
            new_parts.append(line[1:])
        elif line == "":
            old_parts.append("")
            new_parts.append("")
        else:
            old_parts.append(line)
            new_parts.append(line)
    old_text = "\n".join(old_parts)
    new_text = "\n".join(new_parts)
    return old_text, new_text, changed


def build_add_file_content(lines: list) -> str:
    content_lines = [line[1:] if line.startswith("+") else line for line in lines]
    content = "\n".join(content_lines)
    if content_lines:
        content += "\n"
    return content


def parse_patch_sections(patch_text: str) -> list:
    lines = patch_text.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch" or lines[-1].strip() != "*** End Patch":
        raise ValueError("Patch must start with *** Begin Patch and end with *** End Patch.")

    sections = []
    current = None
    for line in lines[1:-1]:
        if line.startswith("*** Add File: "):
            if current:
                sections.append(current)
            current = {"action": "add", "path": line[len("*** Add File: "):].strip(), "lines": []}
        elif line.startswith("*** Update File: "):
            if current:
                sections.append(current)
            current = {"action": "update", "path": line[len("*** Update File: "):].strip(), "lines": []}
        elif line.startswith("*** Delete File: "):
            if current:
                sections.append(current)
            sections.append({"action": "delete", "path": line[len("*** Delete File: "):].strip(), "lines": []})
            current = None
        elif current is not None:
            current["lines"].append(line)
        elif line.strip():
            raise ValueError(f"Unexpected patch line outside a file section: {line}")

    if current:
        sections.append(current)
    return sections


# ---------------------------------------------------------------------------
# Write tool implementations
# ---------------------------------------------------------------------------

def tool_file_write(file_path: str, content: str, overwrite: bool = False) -> str:
    resolved = ensure_allowed_file(file_path)
    parent = os.path.dirname(resolved)
    if not os.path.isdir(parent):
        return f"ERROR: Parent directory '{parent}' does not exist."
    if os.path.exists(resolved) and not overwrite:
        return "ERROR: File exists. Set overwrite=true to overwrite it."
    with open(resolved, "w", encoding="utf-8", errors="replace") as f:
        f.write(content)
    return f"OK: Wrote {len(content)} characters to {resolved}."


def tool_file_replace(file_path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
    resolved = ensure_allowed_file(file_path)
    if not os.path.isfile(resolved):
        return f"ERROR: File '{resolved}' does not exist."
    with open(resolved, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    count = content.count(old_text)
    if count == 0:
        return "ERROR: old_text was not found."
    if count > 1 and not replace_all:
        return f"ERROR: old_text appears {count} times. Set replace_all=true or provide more context."
    updated = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
    with open(resolved, "w", encoding="utf-8", errors="replace") as f:
        f.write(updated)
    replaced = count if replace_all else 1
    return f"OK: Replaced {replaced} occurrence(s) in {resolved}."


def apply_hunks_to_content(content: str, lines: list) -> tuple:
    """Apply every hunk of an Update File section to content.

    Returns (updated_content, error). Hunks are matched sequentially against
    the evolving content; on the first failure nothing is considered applied.
    """
    hunks = split_patch_hunks(lines)
    if not hunks:
        return content, "contains no changes"

    any_changed = False
    for index, hunk in enumerate(hunks, start=1):
        old_text, new_text, changed = parse_patch_lines(hunk)
        if not changed:
            continue
        any_changed = True
        if content.endswith("\n") and old_text and not old_text.endswith("\n"):
            old_text += "\n"
            new_text += "\n"
        count = content.count(old_text)
        if count == 0:
            return content, f"hunk {index} of {len(hunks)}: patch context not found"
        if count > 1:
            return content, f"hunk {index} of {len(hunks)}: patch context appears {count} times; add more context"
        content = content.replace(old_text, new_text, 1)

    if not any_changed:
        return content, "contains no changes"
    return content, ""


def apply_update_section(file_path: str, lines: list) -> str:
    resolved = ensure_allowed_file(file_path)
    if not os.path.isfile(resolved):
        return f"ERROR: File '{resolved}' does not exist."
    with open(resolved, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    updated, error = apply_hunks_to_content(content, lines)
    if error:
        if error == "contains no changes":
            return f"ERROR: Update section for '{resolved}' contains no changes."
        return f"ERROR: Could not update '{resolved}': {error}."

    with open(resolved, "w", encoding="utf-8", errors="replace") as f:
        f.write(updated)
    return f"OK: Updated {resolved}."


def tool_apply_patch(patch_text: str) -> str:
    try:
        sections = parse_patch_sections(patch_text)
        results = []
        for section in sections:
            path = section["path"]
            resolved = ensure_allowed_file(path)
            if section["action"] == "add":
                if os.path.exists(resolved):
                    results.append(f"ERROR: File '{resolved}' already exists.")
                    continue
                parent = os.path.dirname(resolved)
                if not os.path.isdir(parent):
                    results.append(f"ERROR: Parent directory '{parent}' does not exist.")
                    continue
                content = build_add_file_content(section["lines"])
                with open(resolved, "w", encoding="utf-8", errors="replace") as f:
                    f.write(content)
                results.append(f"OK: Added {resolved}.")
            elif section["action"] == "update":
                results.append(apply_update_section(resolved, section["lines"]))
            elif section["action"] == "delete":
                if not os.path.isfile(resolved):
                    results.append(f"ERROR: File '{resolved}' does not exist.")
                    continue
                os.remove(resolved)
                results.append(f"OK: Deleted {resolved}.")
        return "\n".join(results)
    except Exception as e:
        return f"ERROR: Could not apply patch: {e}"


# ---------------------------------------------------------------------------
# Shell tool
# ---------------------------------------------------------------------------

def command_is_dangerous(command: str) -> str:
    lowered = command.lower()
    dangerous_patterns = [
        r"\brm\s+-[^\n;]*r[^\n;]*f",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-[^\n;]*f",
        r"\bdel\s+/s\b",
        # Match the destructive disk utility, but not PowerShell's benign
        # Format-Hex/Format-Table/Format-List cmdlets.
        r"(?<![-\w])format(?:\.com)?(?=\s|[\"']?(?:$|[;&|]))",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r"\bchmod\s+-[^\n;]*r",
        r"\bchown\s+-[^\n;]*r",
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, lowered):
            return f"Blocked dangerous command pattern: {pattern}"
    return ""


def tool_shell_run(
    command: str,
    working_directory: str,
    timeout: int = DEFAULT_SHELL_TIMEOUT,
    background: bool = False,
    session_id: str | None = None,
) -> str:
    resolved_cwd = _resolve_path(working_directory)
    if not is_path_allowed(resolved_cwd):
        return f"ERROR: Access denied. Working directory '{working_directory}' is not in allowed paths."
    if not os.path.isdir(resolved_cwd):
        return f"ERROR: Working directory '{resolved_cwd}' does not exist."

    danger = command_is_dangerous(command)
    if danger:
        return f"ERROR: {danger}"

    effective_cwd = working_directory
    shell_command = command
    if sys.platform == "win32" and resolved_cwd.startswith("\\\\"):
        effective_cwd = None
        shell_command = f'cd /d "{working_directory}" && {command}'

    if background:
        return _start_background_job(shell_command, effective_cwd, session_id=session_id)

    try:
        completed = subprocess.run(
            shell_command,
            cwd=effective_cwd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=max(1, min(int(timeout), 600)),
            encoding="utf-8",
            errors="replace",
        )
        return truncate_output(
            "Exit code: {code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}".format(
                code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            ),
            spill=True,
        )
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout or ""
        stderr = e.stderr or ""
        return truncate_output(f"ERROR: Command timed out after {timeout} seconds.\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}", spill=True)
    except Exception as e:
        return f"ERROR: Could not run command: {e}"


# ---------------------------------------------------------------------------
# Background shell jobs
#
# tool_shell_run's synchronous path (above) cannot host anything that doesn't
# exit on its own, like a dev server or a watch task: subprocess.run() blocks
# the tool call until the process exits or the (600s-capped) timeout fires,
# and there is nowhere to keep a handle to check on it afterwards. This
# registry lets shell_run(background=True) hand back a job id immediately,
# with shell_check/shell_kill to poll output and stop it later.
# ---------------------------------------------------------------------------

_BACKGROUND_JOBS: dict[str, dict] = {}
_BACKGROUND_JOBS_LOCK = threading.Lock()


def _pump_stream(stream, buf: deque, lock: threading.Lock) -> None:
    try:
        for line in iter(stream.readline, ""):
            with lock:
                buf.append(line.rstrip("\n"))
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _prune_background_jobs_locked() -> None:
    """Drop the oldest finished jobs once the registry grows past the cap. Caller must hold _BACKGROUND_JOBS_LOCK."""
    finished = sorted(
        (jid for jid, job in _BACKGROUND_JOBS.items() if job["proc"].poll() is not None),
        key=lambda jid: _BACKGROUND_JOBS[jid]["started_at"],
    )
    while len(_BACKGROUND_JOBS) > MAX_BACKGROUND_JOBS and finished:
        del _BACKGROUND_JOBS[finished.pop(0)]


def _start_background_job(shell_command: str, cwd: str | None, session_id: str | None = None) -> str:
    with _BACKGROUND_JOBS_LOCK:
        _prune_background_jobs_locked()
        running = sum(1 for job in _BACKGROUND_JOBS.values() if job["proc"].poll() is None)
        if running >= MAX_BACKGROUND_JOBS:
            return f"ERROR: Too many background jobs already running (limit {MAX_BACKGROUND_JOBS}). Stop one with shell_kill first."

    try:
        proc = subprocess.Popen(
            shell_command,
            cwd=cwd,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except Exception as e:
        return f"ERROR: Could not start background command: {e}"

    job_id = uuid.uuid4().hex[:8]
    job = {
        "proc": proc,
        "command": shell_command,
        "cwd": cwd,
        "session_id": session_id,
        "started_at": time.time(),
        "stdout": deque(maxlen=BACKGROUND_OUTPUT_MAX_LINES),
        "stderr": deque(maxlen=BACKGROUND_OUTPUT_MAX_LINES),
        "buf_lock": threading.Lock(),
    }
    with _BACKGROUND_JOBS_LOCK:
        _BACKGROUND_JOBS[job_id] = job

    threading.Thread(target=_pump_stream, args=(proc.stdout, job["stdout"], job["buf_lock"]), daemon=True).start()
    threading.Thread(target=_pump_stream, args=(proc.stderr, job["stderr"], job["buf_lock"]), daemon=True).start()

    return (
        f"Started background job {job_id} (pid {proc.pid}): {shell_command}\n"
        f"Use shell_check(job_id=\"{job_id}\") to poll output and shell_kill(job_id=\"{job_id}\") to stop it."
    )


def tool_shell_check(job_id: str, tail_lines: int = 200) -> str:
    with _BACKGROUND_JOBS_LOCK:
        job = _BACKGROUND_JOBS.get(job_id)
    if job is None:
        return f"ERROR: Unknown background job '{job_id}'. It may not exist, or it finished and was pruned."

    proc = job["proc"]
    returncode = proc.poll()
    n = max(1, min(int(tail_lines), BACKGROUND_OUTPUT_MAX_LINES))
    with job["buf_lock"]:
        stdout_tail = list(job["stdout"])[-n:]
        stderr_tail = list(job["stderr"])[-n:]

    status = "running" if returncode is None else f"exited (code {returncode})"
    header = f"Job {job_id} ({status}) — pid {proc.pid} — {job['command']}"
    body = (
        "--- stdout (tail) ---\n" + "\n".join(stdout_tail)
        + "\n--- stderr (tail) ---\n" + "\n".join(stderr_tail)
    )
    return truncate_output(f"{header}\n{body}", spill=True)


def tool_shell_kill(job_id: str) -> str:
    with _BACKGROUND_JOBS_LOCK:
        job = _BACKGROUND_JOBS.get(job_id)
    if job is None:
        return f"ERROR: Unknown background job '{job_id}'."

    proc = job["proc"]
    if proc.poll() is not None:
        return f"Job {job_id} already exited (code {proc.returncode})."

    try:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=8)
    except Exception as e:
        return f"ERROR: Failed to stop job {job_id}: {e}"
    return f"Stopped job {job_id} (exit code {proc.returncode})."


_KILL_ALL_SESSIONS = object()


def kill_all_background_jobs(session_id=_KILL_ALL_SESSIONS, overall_timeout: float = 12.0) -> None:
    """Best-effort cleanup so an agent's background jobs (dev servers, watch
    tasks) don't outlive the run/session/process that started them.

    Called two ways:
    - No session_id (the default): kill every job, regardless of session.
      Registered with atexit below, and called from the web app's process
      shutdown paths (lifespan shutdown, /api/shutdown) so nothing outlives
      the backend or Electron app being closed.
    - With session_id: kill only that session's jobs. Called when a session
      is actually deleted (there is no route back to shell_check/shell_kill
      for it after that), so its background jobs don't run forever. A
      cancelled-but-not-deleted run deliberately does NOT hit this path —
      backgrounding a command is precisely so it survives past the turn that
      started it, so cancelling that turn shouldn't kill it.

    Waits against one shared deadline across all jobs (rather than a fixed
    timeout per job) so a handful of hung processes can't multiply the total
    wait past what callers budget for it (e.g. Electron's before-quit)."""
    with _BACKGROUND_JOBS_LOCK:
        if session_id is _KILL_ALL_SESSIONS:
            procs = [job["proc"] for job in _BACKGROUND_JOBS.values() if job["proc"].poll() is None]
        else:
            procs = [
                job["proc"] for job in _BACKGROUND_JOBS.values()
                if job["proc"].poll() is None and job.get("session_id") == session_id
            ]
    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass

    deadline = time.monotonic() + overall_timeout
    for proc in procs:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    for proc in procs:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    if session_id is not _KILL_ALL_SESSIONS:
        # The session is gone for good, so there is no shell_check/shell_kill
        # call coming for these jobs later - drop them instead of waiting for
        # _prune_background_jobs_locked to evict them once the cap is hit.
        with _BACKGROUND_JOBS_LOCK:
            for jid in [jid for jid, job in _BACKGROUND_JOBS.items() if job.get("session_id") == session_id]:
                del _BACKGROUND_JOBS[jid]


atexit.register(kill_all_background_jobs)


# ---------------------------------------------------------------------------
# Diff / preview helpers
# ---------------------------------------------------------------------------

def read_text_file_for_diff(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def make_unified_diff(old_text: str, new_text: str, fromfile: str, tofile: str) -> str:
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=fromfile,
        tofile=tofile,
        lineterm="",
    ))


def build_file_write_diff(arguments: dict) -> str:
    resolved = ensure_allowed_file(arguments["file_path"])
    new_text = arguments["content"]
    if os.path.exists(resolved):
        old_text = read_text_file_for_diff(resolved)
        return make_unified_diff(old_text, new_text, f"{resolved} (current)", f"{resolved} (proposed)")
    return make_unified_diff("", new_text, "/dev/null", f"{resolved} (new file)")


def build_file_replace_diff(arguments: dict) -> str:
    resolved = ensure_allowed_file(arguments["file_path"])
    old_text = arguments["old_text"]
    new_text = arguments["new_text"]
    replace_all = arguments.get("replace_all", False)
    content = read_text_file_for_diff(resolved)
    count = content.count(old_text)
    if count == 0:
        return "Diff unavailable: old_text was not found."
    if count > 1 and not replace_all:
        return f"Diff unavailable: old_text appears {count} times and replace_all is false."
    updated = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
    return make_unified_diff(content, updated, f"{resolved} (current)", f"{resolved} (proposed)")


def render_update_section_preview(file_path: str, lines: list) -> str:
    resolved = ensure_allowed_file(file_path)
    content = read_text_file_for_diff(resolved)
    updated, error = apply_hunks_to_content(content, lines)
    if error:
        return f"Diff unavailable for {resolved}: {error}."
    return make_unified_diff(content, updated, f"{resolved} (current)", f"{resolved} (proposed)")


def build_apply_patch_diff(arguments: dict) -> str:
    sections = parse_patch_sections(arguments["patch_text"])
    diffs = []
    for section in sections:
        resolved = ensure_allowed_file(section["path"])
        if section["action"] == "add":
            content = build_add_file_content(section["lines"])
            diffs.append(make_unified_diff("", content, "/dev/null", f"{resolved} (new file)"))
        elif section["action"] == "update":
            diffs.append(render_update_section_preview(resolved, section["lines"]))
        elif section["action"] == "delete":
            old_text = read_text_file_for_diff(resolved)
            diffs.append(make_unified_diff(old_text, "", f"{resolved} (current)", "/dev/null"))
    return "\n".join(diff for diff in diffs if diff)


def build_tool_diff_preview(tool_name: str, arguments: dict) -> str:
    try:
        if tool_name == "file_write":
            return build_file_write_diff(arguments)
        if tool_name == "file_replace":
            return build_file_replace_diff(arguments)
        if tool_name == "apply_patch":
            return build_apply_patch_diff(arguments)
    except Exception as e:
        return f"Diff unavailable: {e}"
    return ""


# ---------------------------------------------------------------------------
# Output truncation and compression
# ---------------------------------------------------------------------------

TOOL_OUTPUT_SPILL_DIR = register_allowed_path(os.path.join(LOG_DIR, "tool_output_spill"))
_SPILL_KEEP_FILES = 40


def _prune_spill_files() -> None:
    try:
        entries = sorted(
            (entry for entry in os.scandir(TOOL_OUTPUT_SPILL_DIR) if entry.is_file()),
            key=lambda entry: entry.stat().st_mtime,
        )
        for entry in entries[:-_SPILL_KEEP_FILES]:
            os.unlink(entry.path)
    except OSError:
        pass


def _spill_full_output(text: str) -> str | None:
    """Persist a full pre-truncation tool output so a provider can recover it."""
    try:
        os.makedirs(TOOL_OUTPUT_SPILL_DIR, exist_ok=True)
        name = f"tool-output-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.txt"
        path = os.path.join(TOOL_OUTPUT_SPILL_DIR, name)
        with open(path, "w", encoding="utf-8", errors="replace") as handle:
            handle.write(text)
        _prune_spill_files()
        return path
    except OSError:
        return None


def truncate_output(text: str, max_chars: int = MAX_TOOL_OUTPUT, spill: bool = False) -> str:
    if len(text) <= max_chars:
        return text
    note = ""
    if spill:
        spill_path = _spill_full_output(text)
        if spill_path:
            note = f"; full output saved to {spill_path}"
    return text[:max_chars] + f"\n... (truncated, total {len(text)} characters{note})"


_TOOL_RESULT_HEADER = re.compile(r"^--- .+? ---\n", re.MULTILINE)


def slim_tool_result(text: str) -> str:
    return _TOOL_RESULT_HEADER.sub("", text, count=1)


def _summarize_code(content: str) -> str:
    parts = []
    imports = []
    signatures = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            imports.append(stripped)
        elif stripped.startswith("def "):
            signatures.append(stripped.rstrip(":") + ":")
        elif stripped.startswith("class "):
            signatures.append(stripped.rstrip(":") + ":")
    if imports:
        parts.append("Imports: " + ", ".join(imports[:10]))
        if len(imports) > 10:
            parts.append(f"  ... ({len(imports)} imports total)")
    if signatures:
        parts.append("Signatures:\n  " + "\n  ".join(signatures))
    return "\n".join(parts) if parts else None


def _summarize_content(content: str) -> str:
    lines = content.splitlines()
    line_count = len(lines)
    char_count = len(content)

    code_summary = _summarize_code(content)
    if code_summary:
        return f"[Python, {char_count} chars, {line_count} lines]\n{code_summary}"

    preview_lines = lines[:5]
    preview = "\n".join(preview_lines)
    if line_count > 5:
        preview += f"\n... ({line_count} lines total)"
    return f"[{char_count} chars, {line_count} lines] {preview}"


def summarize_tool_result(content: str, source_tool: str = "") -> str:
    summary = _summarize_content(content)
    if summary:
        label = source_tool or "tool"
        return f"[Summarized {label} result]\n{summary}"
    return truncate_output(content)


def compress_file_write_args(assistant_msg: dict, tool_call_id: str):
    """Replace the content arg of a successful file_write tool call with a short summary."""
    tool_calls = assistant_msg.get("tool_calls", [])
    for i, tc in enumerate(tool_calls):
        if tc.get("id") != tool_call_id:
            continue
        try:
            args = json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
        except (json.JSONDecodeError, TypeError):
            return
        content = args.get("content", "")
        if len(content) <= TOOL_COMPRESS_THRESHOLD:
            return
        args["_content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        args["content"] = _summarize_content(content)
        tool_calls[i] = {
            **tc,
            "function": {**tc["function"], "arguments": json.dumps(args, ensure_ascii=False)},
        }
        return


def _aggressive_compress_tool_results(messages: list, turn_start_index: int):
    limit = 800
    for i in range(turn_start_index, len(messages)):
        msg = messages[i]
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if len(content) > limit:
                messages[i] = {**msg, "content": content[:limit] + "\n... (truncated for retry)"}


def compress_turn_tool_results(messages: list, turn_start_index: int, end_index: int = None):
    end_index = end_index if end_index is not None else len(messages)
    tool_call_names: dict[str, str] = {}
    for i in range(turn_start_index, end_index):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_call_names[tc["id"]] = tc["function"]["name"]

    for i in range(turn_start_index, end_index):
        msg = messages[i]
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if content.startswith("ERROR"):
                continue
            tc_id = msg.get("tool_call_id", "")
            source_tool = tool_call_names.get(tc_id, "")
            if len(content) > TOOL_COMPRESS_THRESHOLD:
                messages[i] = {**msg, "content": summarize_tool_result(content, source_tool)}
        elif msg.get("role") == "assistant" and msg.get("tool_calls"):
            compressed_calls = []
            for tc in msg["tool_calls"]:
                func_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                except (json.JSONDecodeError, TypeError):
                    compressed_calls.append(tc)
                    continue
                changed = False
                for arg_key in ("content", "new_text", "patch_text"):
                    val = args.get(arg_key, "")
                    if isinstance(val, str) and len(val) > TOOL_COMPRESS_THRESHOLD:
                        args[f"_{arg_key}_sha256"] = hashlib.sha256(val.encode("utf-8")).hexdigest()
                        args[arg_key] = _summarize_content(val)
                        changed = True
                if changed:
                    compressed_calls.append({
                        **tc,
                        "function": {
                            **tc["function"],
                            "arguments": json.dumps(args, ensure_ascii=False),
                        },
                    })
                else:
                    compressed_calls.append(tc)
            messages[i] = {**msg, "tool_calls": compressed_calls}


# ---------------------------------------------------------------------------
# Tool dispatch (read-only)
# ---------------------------------------------------------------------------

_REQUIRED_ARGS = {
    "file_search": ["directory", "pattern"],
    "file_list": ["directory"],
    "content_search": ["directory", "query"],
    "file_read": ["file_path"],
    "image_read": ["file_path"],
    "web_request": ["url"],
    "gather_context": ["jobs"],
    "skill_list": [],
    "skill_read": ["name"],
    "file_write": ["file_path", "content"],
    "file_replace": ["file_path", "old_text", "new_text"],
    "apply_patch": ["patch_text"],
    "shell_run": ["command", "working_directory"],
    "shell_check": ["job_id"],
    "shell_kill": ["job_id"],
    "plan_update": ["items"],
}


def _check_required_args(name: str, arguments: dict) -> str | None:
    required = _REQUIRED_ARGS.get(name, [])
    missing = [k for k in required if k not in arguments]
    if missing:
        return f"ERROR: Missing required argument(s): {', '.join(missing)}"
    return None


def _execute_read_only_tool_uncached(name: str, arguments: dict) -> str:
    err = _check_required_args(name, arguments)
    if err:
        return err
    if name == "file_search":
        return tool_file_search(arguments["directory"], arguments["pattern"], arguments.get("recursive", True))
    if name == "file_list":
        return tool_file_list(arguments["directory"], arguments.get("glob"), arguments.get("max_results", MAX_SEARCH_RESULTS))
    if name == "content_search":
        return tool_content_search(
            arguments["directory"],
            arguments["query"],
            arguments.get("glob"),
            arguments.get("case_sensitive", False),
            arguments.get("max_results", MAX_SEARCH_RESULTS),
        )
    if name == "file_read":
        return tool_file_read(
            arguments["file_path"],
            arguments.get("read_mode", "full"),
            arguments.get("lines", 200),
            arguments.get("offset", 0),
            arguments.get("query", ""),
        )
    if name == "image_read":
        return tool_image_read(arguments["file_path"])
    if name == "web_request":
        return tool_web_request(arguments["url"], arguments.get("method", "GET"))
    if name == "gather_context":
        return tool_gather_context(arguments["jobs"], arguments.get("budget"))
    if name == "skill_list":
        return skill_registry.catalog_text()
    if name == "skill_read":
        try:
            return skill_registry.read_skill(arguments["name"])
        except (OSError, UnicodeError, ValueError) as exc:
            return f"ERROR: {exc}"
    return f"ERROR: Unknown tool '{name}'"


def execute_read_only_tool(name: str, arguments: dict) -> str:
    cached = None if name == "image_read" else _cache_get(name, arguments)
    if cached is not None:
        return cached
    result = _execute_read_only_tool_uncached(name, arguments)
    if name != "image_read" and not result.startswith("ERROR:"):
        _cache_put(name, arguments, result)
    return result
