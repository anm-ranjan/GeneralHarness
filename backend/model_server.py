"""Settings -> Native: start and stop a LAN model server (llama-server) from the app.

The launch script is the path of a shell script on the Linux machine that
serves the model, for example ``/archive/models/run-inkling.sh`` holding
``exec llama-server -m ...``. Only a path to a ``.sh`` file is accepted, never
a command line: what runs on the other machine is that file, run as
``bash -l <path>`` from its folder (it need not be executable).

* ``login`` is ``user@host`` (``host`` or ``user@host:port`` also work). Empty
  means this computer's user name at the host of the local base URL, so
  ``http://gpu-box.lan:8081/v1`` logs in to ``gpu-box.lan``.
* SSH is the system ``ssh`` client in batch mode, as the fleet bridge uses:
  ``~/.ssh/config``, ``known_hosts``, the SSH agent and the usual keys apply,
  and it never asks for a password.
* With an empty login and a loopback base URL (127.0.0.1, localhost) on a
  Linux or macOS backend, the script runs on this computer instead.

The script is started in its own session (``setsid``), so its process group id
identifies everything it started. Output goes to
``~/.cache/myharness/llama-server.log`` on the server. The launch is recorded
in native_settings.json (``launched``) so "Stop server" still works after the
app restarts: SIGTERM to the process group, then SIGKILL after 5 s. Only a
server started from here can be stopped; a record from before the server
rebooted (another Linux boot id) is dropped without killing anything.
"""
from __future__ import annotations

import os
import posixpath
import re
import shlex
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from getpass import getuser
from pathlib import Path
from urllib.parse import urlparse

import requests

import native_settings

# Where llama.cpp start scripts live on the LAN model machine.
DEFAULT_SCRIPT_DIR = "/archive/models"
DEFAULT_SCRIPT = f"{DEFAULT_SCRIPT_DIR}/run-inkling.sh"

_SSH_TIMEOUT = 10
_START_GRACE = 1.5  # seconds a launch must survive to count as started
_STOP_WAIT = 5.0
_LOG_LINES = 40
_REMOTE_LOG_DIR = "$HOME/.cache/myharness"
_REMOTE_LOG = "~/.cache/myharness/llama-server.log"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_USER_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]{0,63}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,252}$")
_busy = threading.Lock()  # one launch or stop at a time
_children: dict[int, subprocess.Popen] = {}  # local launches, kept so they are reaped quietly


class ServerError(ValueError):
    pass


# ── saved settings ────────────────────────────────────────────────


def _saved() -> tuple[dict, dict | None]:
    data = native_settings._read_file()
    server = data.get("server") if isinstance(data.get("server"), dict) else {}
    launched = data.get("launched") if isinstance(data.get("launched"), dict) else None
    return ({"script": str(server.get("script") or ""), "login": str(server.get("login") or "")},
            launched if launched and isinstance(launched.get("pgid"), int) else None)


def _update(**fields) -> None:
    with native_settings._lock:
        data = native_settings._read_file()
        for key, value in fields.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        native_settings._write_file(data)


def _saved_base_url() -> str:
    return native_settings.load().get("base_url", "")


def _api_host(base_url: str | None) -> str:
    try:
        return urlparse(str(base_url or "")).hostname or ""
    except ValueError:
        return ""


def default_login(base_url: str | None = None) -> str:
    host = _api_host(base_url if base_url is not None else _saved_base_url())
    return f"{getuser()}@{host}" if host else ""


def saved_status() -> dict:
    """What Settings shows without asking the server (part of GET /api/native/settings)."""
    server, launched = _saved()
    return {"script": server["script"] or DEFAULT_SCRIPT, "script_saved": bool(server["script"]),
            "login": server["login"], "default_script": DEFAULT_SCRIPT, "default_dir": DEFAULT_SCRIPT_DIR,
            "default_login": default_login(), "launched": launched,
            "log": launched.get("log") if launched else _REMOTE_LOG}


# ── where it runs ─────────────────────────────────────────────────


def _check_path(value, what: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ServerError(f"Enter the {what}")
    if any(ch in text for ch in "\r\n\0") or len(text) > 1000:
        raise ServerError(f"The {what} must be one path")
    if "\\" in text or re.match(r"^[A-Za-z]:", text):
        raise ServerError("Enter the Linux path on the model server (/archive/...), not a Windows path")
    if not (text.startswith("/") or text == "~" or text.startswith("~/")):
        raise ServerError(f"The {what} must be an absolute path such as {DEFAULT_SCRIPT}")
    return posixpath.normpath(text) if text.startswith("/") else text


def normalize_script(value) -> str:
    """An absolute path (or ~/...) of a .sh file; command lines are refused."""
    text = _check_path(value, "launch script")
    if not text.endswith(".sh"):
        raise ServerError(f"The launch script must be a .sh file, for example {DEFAULT_SCRIPT}")
    return text


def normalize_folder(value) -> str:
    return _check_path(value or DEFAULT_SCRIPT_DIR, "script folder")


def _target(login: str, base_url: str | None) -> dict:
    """{"mode": "ssh", "user", "host", "port", "login"} or {"mode": "local"}."""
    login = str(login or "").strip()
    if not login:
        host = _api_host(base_url)
        if not host:
            raise ServerError("Enter the SSH login (user@host) of the model server, or the base URL first")
        if host in _LOCAL_HOSTS and os.name != "nt":
            return {"mode": "local", "login": "this computer"}
        login = f"{getuser()}@{host}"
    user, _, hostport = login.rpartition("@")
    host, port = hostport, 22
    if hostport.count(":") == 1:
        host, _, port_text = hostport.partition(":")
        if not port_text.isdigit() or not 0 < int(port_text) < 65536:
            raise ServerError(f"Bad SSH port in '{login}'")
        port = int(port_text)
    user = user or getuser()
    if not _HOST_RE.match(host) or not _USER_RE.match(user):
        raise ServerError(f"Bad SSH login '{login}'; use user@host")
    return {"mode": "ssh", "user": user, "host": host, "port": port,
            "login": f"{user}@{host}" + (f":{port}" if port != 22 else "")}


def _ssh_run(target: dict, script: str, timeout: float = 30) -> tuple[int, str, str]:
    """Run a bash script on the model server: (exit status, stdout, stderr)."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={_SSH_TIMEOUT}",
            "-p", str(target["port"]), "-l", target["user"], "--", target["host"],
            f"bash -c {shlex.quote(script)}"]  # bash whatever the login shell is
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ServerError("No ssh client on this computer; install OpenSSH to reach the model server") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServerError(f"Could not reach {target['login']} over SSH: {exc}") from exc
    out = result.stdout.decode("utf-8", errors="replace")
    err = result.stderr.decode("utf-8", errors="replace")
    if result.returncode == 255:  # ssh itself failed: no route, refused, no key
        raise ServerError(f"Could not reach {target['login']} over SSH: {err.strip() or 'connection failed'}")
    return result.returncode, out, err


def _remote_path(path: str) -> str:
    """Shell-quote a path, keeping a leading ~/ as $HOME."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


# ── scripts in a folder ───────────────────────────────────────────


def list_scripts(values: dict) -> dict:
    """The .sh files directly in a folder of the model server (macOS ._ files skipped)."""
    folder = normalize_folder(values.get("folder"))
    base_url = values.get("base_url") or _saved_base_url()
    target = _target(values.get("login") or "", base_url)
    if target["mode"] == "local":
        path = Path(os.path.expanduser(folder))
        if not path.is_dir():
            raise ServerError(f"No folder {folder} on this computer")
        names = sorted(p.name for p in path.glob("*.sh") if p.is_file() and not p.name.startswith("._"))
    else:
        code, out, err = _ssh_run(target, (
            f"cd {_remote_path(folder)} 2>/dev/null || exit 91; "
            "for f in *.sh; do [ -f \"$f\" ] && case \"$f\" in ._*) ;; *) printf '%s\\n' \"$f\";; esac; done; exit 0"),
            timeout=20)
        if code == 91:
            raise ServerError(f"No folder {folder} on {target['login']}")
        if code != 0:
            raise ServerError(f"Could not list {folder} on {target['login']}: {(err or out).strip()}")
        names = sorted(line for line in out.splitlines() if line.strip())
    shown = folder.rstrip("/") or "/"
    return {"folder": folder, "login": target["login"],
            "scripts": [posixpath.join(shown, name) for name in names]}


# ── launch / status / stop ────────────────────────────────────────


def _probe_remote(target: dict, pgid: int) -> dict:
    _code, out, _err = _ssh_run(target, (
        f"cat /proc/sys/kernel/random/boot_id 2>/dev/null; echo ---; pgrep -a -g {pgid}; echo ---; "
        f"tail -n {_LOG_LINES} \"{_REMOTE_LOG_DIR}/llama-server.log\" 2>/dev/null; exit 0"), timeout=20)
    boot, _, rest = out.partition("---\n")
    procs, _, log = rest.partition("---\n")
    return {"boot_id": boot.strip(), "processes": [line for line in procs.splitlines() if line.strip()],
            "log_tail": log.rstrip()}


def _local_alive(pgid: int) -> bool:
    child = _children.get(pgid)
    if child is not None and child.poll() is not None:
        _children.pop(pgid, None)
    try:  # reap our own exited launcher, or the group looks alive (EPERM on macOS)
        os.waitpid(pgid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _local_log() -> Path:
    return native_settings._path().parent / "llama-server.log"


def _tail(path: Path) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-_LOG_LINES:])
    except OSError:
        return ""


def _check(launched: dict) -> dict:
    """{"running", "processes", "log_tail", "stale"} for a recorded launch."""
    if launched.get("mode") == "local":
        alive = _local_alive(launched["pgid"])
        return {"running": alive, "processes": [f"process group {launched['pgid']}"] if alive else [],
                "log_tail": _tail(_local_log()), "stale": False}
    probe = _probe_remote(launched, launched["pgid"])
    stale = bool(launched.get("boot_id") and probe["boot_id"] and probe["boot_id"] != launched["boot_id"])
    return {"running": bool(probe["processes"]) and not stale, "processes": [] if stale else probe["processes"],
            "log_tail": probe["log_tail"], "stale": stale}


def status() -> dict:
    """GET /api/native/server: the saved status plus whether the launch still runs."""
    result = saved_status()
    launched = result["launched"]
    result.update(running=False, processes=[], log_tail="", error="")
    if not launched:
        return result
    try:
        result.update(_check(launched))
    except ServerError as exc:
        result.update(running=None, error=str(exc))
    if result.pop("stale", False):
        _update(launched=None)
        result.update(launched=None, error="The model server restarted since the launch; nothing to stop.")
    return result


def _endpoint_answers(base_url: str) -> bool:
    """Whether something already serves at the base URL (503 = loading counts)."""
    try:
        requests.get(f"{base_url}/models", timeout=3)
    except requests.RequestException:
        return False
    return True


def launch(values: dict) -> dict:
    """Save the script and login, then start the script on the model server."""
    script = normalize_script(values.get("script"))
    login = str(values.get("login") or "").strip()
    raw_url = str(values.get("base_url") or "").strip() or _saved_base_url()
    try:
        base_url = native_settings.normalize_base_url(raw_url) if raw_url else ""
    except native_settings.EndpointError as exc:
        raise ServerError(str(exc)) from exc
    target = _target(login, base_url)
    if not _busy.acquire(blocking=False):
        raise ServerError("A launch or stop is already in progress")
    try:
        _update(server={"script": script, "login": login})
        _server, launched = _saved()
        if launched:
            try:
                state = _check(launched)
            except ServerError:
                state = {"running": False}
            if state.get("running"):
                raise ServerError(f"A server started from here is still running (process group "
                                  f"{launched['pgid']} on {launched['login']}); stop it first")
            _update(launched=None)
        if base_url and _endpoint_answers(base_url):
            raise ServerError(f"A server already answers at {base_url}. Stop it first "
                              "(one started outside the app has to be stopped where it was started).")
        record = _start_local(script) if target["mode"] == "local" else _start_remote(target, script)
        _update(launched=record)
        time.sleep(_START_GRACE)
        state = _check(record)
        if not state["running"]:
            _update(launched=None)
            tail = state.get("log_tail") or "(no output)"
            raise ServerError(f"The launch script exited at once. Last output in {record['log']}:\n{tail}")
    finally:
        _busy.release()
    return status()


def _start_remote(target: dict, script: str) -> dict:
    folder = _remote_path(posixpath.dirname(script) or "~")
    path = _remote_path(script)
    code, out, err = _ssh_run(target, (
        f"[ -f {path} ] || exit 93; "
        f"L=\"{_REMOTE_LOG_DIR}\"; mkdir -p \"$L\" || exit 90; cd {folder} || exit 91; "
        f"command -v setsid >/dev/null || exit 92; "
        f"setsid nohup bash -l {path} > \"$L/llama-server.log\" 2>&1 < /dev/null & "
        f"echo \"MYH_PGID=$!\"; echo \"MYH_BOOT=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)\""),
        timeout=30)
    reasons = {90: "could not create ~/.cache/myharness", 91: "the script's folder does not exist",
               92: "setsid is not installed", 93: f"there is no file {script}"}
    marks = dict(line.strip().split("=", 1) for line in out.splitlines() if line.startswith("MYH_") and "=" in line)
    pgid = marks.get("MYH_PGID", "")
    if code != 0 or not pgid.isdigit():
        raise ServerError(f"Could not start the script on {target['login']}: "
                          f"{reasons.get(code) or (err or out).strip() or f'exit {code}'}")
    return {**target, "pgid": int(pgid), "boot_id": marks.get("MYH_BOOT", ""),
            "script": script, "log": _REMOTE_LOG, "started_at": datetime.now(timezone.utc).isoformat()}


def _start_local(script: str) -> dict:
    path = os.path.expanduser(script)
    if not os.path.isfile(path):
        raise ServerError(f"No launch script at {path} on this computer")
    log_path = _local_log()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(["bash", "-l", path], cwd=os.path.dirname(path) or None,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
    _children[proc.pid] = proc
    return {"mode": "local", "login": "this computer", "pgid": proc.pid, "script": script,
            "log": str(log_path), "started_at": datetime.now(timezone.utc).isoformat()}


def stop() -> dict:
    """Stop the process group started from here: SIGTERM, then SIGKILL."""
    _server, launched = _saved()
    if not launched:
        raise ServerError("No model server was started from here")
    if not _busy.acquire(blocking=False):
        raise ServerError("A launch or stop is already in progress")
    try:
        if launched.get("mode") == "local":
            _stop_local(launched["pgid"])
        else:
            _stop_remote(launched)
        _update(launched=None)
    finally:
        _busy.release()
    result = status()
    result["stopped"] = launched
    return result


def _stop_local(pgid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not _local_alive(pgid):
            return
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + _STOP_WAIT
        while time.monotonic() < deadline:
            if not _local_alive(pgid):
                return
            time.sleep(0.25)
    raise ServerError(f"Process group {pgid} did not stop")


def _stop_remote(launched: dict) -> None:
    pgid = int(launched["pgid"])
    boot = shlex.quote(str(launched.get("boot_id") or ""))
    steps = int(_STOP_WAIT / 0.5)
    code, out, err = _ssh_run(launched, (
        f"b={boot}; now=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null); "
        f"if [ -n \"$b\" ] && [ -n \"$now\" ] && [ \"$b\" != \"$now\" ]; then echo rebooted; exit 0; fi; "
        f"pgrep -g {pgid} >/dev/null || {{ echo gone; exit 0; }}; "
        f"kill -TERM -- -{pgid} 2>/dev/null; "
        f"for i in $(seq {steps}); do sleep 0.5; pgrep -g {pgid} >/dev/null || {{ echo stopped; exit 0; }}; done; "
        f"kill -KILL -- -{pgid} 2>/dev/null; sleep 0.5; "
        f"pgrep -g {pgid} >/dev/null && {{ echo alive; exit 1; }}; echo killed"), timeout=30)
    if code != 0:
        raise ServerError(f"Process group {pgid} on {launched['login']} did not stop: {(err or out).strip()}")
