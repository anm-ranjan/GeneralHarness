"""Bring fleet peers online and offline over SSH.

A peer is a fleet host other than this one that has an `ssh` target and a
`repo` path in fleet.hosts. This machine keeps an SSH forward open to every
such peer, so the browser reaches it on its configured loopback url, and can
start or stop that peer's backend. A peer is simply "online" while its backend
answers; nothing records who started it, so anyone may bring it online or take
it offline.

Everything run on another machine is built from config values only, never from
request data.
"""
from __future__ import annotations

import json
import shlex
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import utils

ONLINE = "online"
OFF = "off"
UNREACHABLE = "unreachable"

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_REMOTE_PORT = 8420
HTTP_TIMEOUT = 2.0
SSH_CHECK_TIMEOUT = 3
START_WAIT_SECONDS = 40
STOP_WAIT_SECONDS = 15
TUNNEL_RETRY_SECONDS = 10


class BridgeError(RuntimeError):
    """A bridge action could not be carried out; the message is for the user."""


class BridgeBusy(BridgeError):
    """The peer has work in flight and the caller did not force the stop."""

    def __init__(self, running: int, waiting_approval: int):
        super().__init__("The host has running or waiting sessions.")
        self.running = running
        self.waiting_approval = waiting_approval


def local_port(host: dict) -> int | None:
    """Port this machine reaches the peer on, or None unless it is a loopback
    forward (any other url is a route we do not manage)."""
    parsed = urlparse(host.get("url", ""))
    if (parsed.hostname or "") not in LOOPBACK_HOSTS:
        return None
    return parsed.port


def bridgeable_hosts() -> list[dict]:
    """The peers this machine can bring online and offline."""
    if not utils.FLEET_ENABLED or not utils.FLEET_SELF_ID:
        return []
    return [
        host for host in utils.FLEET_HOSTS
        if host["id"] != utils.FLEET_SELF_ID
        and host.get("ssh") and host.get("repo") and local_port(host)
    ]


def find_host(host_id: str) -> dict:
    for host in bridgeable_hosts():
        if host["id"] == host_id:
            return host
    raise BridgeError(f"'{host_id}' is not a host this machine can bridge to.")


def _base_url(host: dict) -> str:
    return f"http://127.0.0.1:{local_port(host)}"


def _ssh_base() -> list[str]:
    return ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_CHECK_TIMEOUT}"]


def _fetch_json(url: str, method: str = "GET") -> dict | None:
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _machine_answers(host: dict) -> bool:
    try:
        result = subprocess.run(
            _ssh_base() + [host["ssh"], "true"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=SSH_CHECK_TIMEOUT + 3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def probe(host: dict) -> dict:
    """Online when the backend answers, off when only the machine does."""
    status = _fetch_json(f"{_base_url(host)}/api/fleet/status")
    if status is not None:
        return {
            "id": host["id"], "label": host["label"], "state": ONLINE,
            "running": int(status.get("running") or 0),
            "waiting_approval": int(status.get("waiting_approval") or 0),
        }
    state = OFF if _machine_answers(host) else UNREACHABLE
    return {"id": host["id"], "label": host["label"], "state": state, "running": 0, "waiting_approval": 0}


def probe_all() -> list[dict]:
    hosts = bridgeable_hosts()
    if not hosts:
        return []
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return list(pool.map(probe, hosts))


def remote_start_script(host: dict) -> str:
    """Shell script that launches the peer's backend detached from the SSH
    session. Built only from the configured repo path and port."""
    repo = shlex.quote(host["repo"])
    port = int(host.get("remote_port") or DEFAULT_REMOTE_PORT)
    return (
        f"cd {repo} || exit 1; mkdir -p logs; "
        'py=.venv/bin/python; [ -x "$py" ] || py=python3; '
        f'MYHARNESS_WEB_HOST=127.0.0.1 MYHARNESS_WEB_PORT={port} '
        'nohup "$py" -u backend/web_app.py >> logs/bridge-backend.log 2>&1 < /dev/null &'
    )


def _wait_until(predicate, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    return predicate()


def start(host: dict) -> dict:
    current = probe(host)
    if current["state"] == ONLINE:
        return current
    if current["state"] == UNREACHABLE:
        raise BridgeError(f"{host['label']} does not answer over SSH ({host['ssh']}).")
    # A login shell, so the peer's agent CLIs are on PATH as in a terminal.
    command = f'exec "${{SHELL:-/bin/sh}}" -lc {shlex.quote(remote_start_script(host))}'
    try:
        result = subprocess.run(
            _ssh_base() + [host["ssh"], command],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BridgeError(f"Could not start the backend on {host['label']}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        raise BridgeError(f"Could not start the backend on {host['label']}: {detail[0]}")
    if not _wait_until(lambda: _fetch_json(f"{_base_url(host)}/api/fleet/status") is not None, START_WAIT_SECONDS):
        raise BridgeError(
            f"Started the backend on {host['label']} but it did not answer within "
            f"{START_WAIT_SECONDS}s. See logs/bridge-backend.log in {host['repo']}."
        )
    return probe(host)


def stop(host: dict, force: bool = False) -> dict:
    current = probe(host)
    if current["state"] != ONLINE:
        return current
    if not force and (current["running"] or current["waiting_approval"]):
        raise BridgeBusy(current["running"], current["waiting_approval"])
    _fetch_json(f"{_base_url(host)}/api/shutdown", method="POST")
    if not _wait_until(lambda: _fetch_json(f"{_base_url(host)}/api/fleet/status") is None, STOP_WAIT_SECONDS):
        raise BridgeError(f"{host['label']} did not stop within {STOP_WAIT_SECONDS}s.")
    return probe(host)


# ---------------------------------------------------------------------------
# Tunnels
# ---------------------------------------------------------------------------

def tunnel_command(host: dict) -> list[str]:
    remote_port = int(host.get("remote_port") or DEFAULT_REMOTE_PORT)
    return [
        "ssh", "-N",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", f"ConnectTimeout={SSH_CHECK_TIMEOUT}",
        "-L", f"{local_port(host)}:127.0.0.1:{remote_port}",
        host["ssh"],
    ]


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


class Tunnels:
    """One supervised `ssh -L` per peer, for as long as this backend runs."""

    def __init__(self):
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._procs: dict[str, subprocess.Popen] = {}

    def start(self) -> None:
        if not shutil.which("ssh"):
            return
        for host in bridgeable_hosts():
            thread = threading.Thread(target=self._supervise, args=(host,), name=f"tunnel-{host['id']}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for proc in list(self._procs.values()):
            if proc.poll() is None:
                proc.terminate()
        self._threads.clear()
        self._procs.clear()

    def _supervise(self, host: dict) -> None:
        port = local_port(host)
        while not self._stop.is_set():
            # Something already serves this port (a hand-run tunnel script):
            # leave it alone rather than fail to bind.
            if _port_open(port):
                self._stop.wait(TUNNEL_RETRY_SECONDS)
                continue
            try:
                proc = subprocess.Popen(
                    tunnel_command(host),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError:
                self._stop.wait(TUNNEL_RETRY_SECONDS)
                continue
            self._procs[host["id"]] = proc
            while proc.poll() is None and not self._stop.is_set():
                self._stop.wait(1)
            if proc.poll() is None:
                proc.terminate()
            self._stop.wait(TUNNEL_RETRY_SECONDS)


tunnels = Tunnels()
