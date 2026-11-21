"""Who sent a local API request: the user, an agent's tool, or Antigravity.

Thread-starting routes must refuse Antigravity (its consumer sign-in may only
take prompts the user typed, so it may not start or message threads itself,
and no other agent may send it prompts either). Every agent tool runs as a
descendant of this backend: Native shell commands are its children, and the
Codex app-server, the Claude CLI and the ACP agents are children that run
their tools as their own descendants. The UI, the phone app (through its
tunnel) and other fleet hosts (through SSH) are not descendants.

So the peer of a loopback connection is looked up by its port and its parent
chain walked:

- ``ACP``: an ACP agent process this backend started is an ancestor,
- ``AGENT``: this backend is an ancestor,
- ``USER``: neither, or the request is not over loopback.

The lookup uses ``lsof`` and ``ps``, which macOS and Linux ship. Where they
are missing (Windows) or fail, the result is ``USER``, so the guard never
blocks the user; it only adds a refusal where it can tell.
"""
from __future__ import annotations

import os
import shutil
import subprocess

USER = "user"
AGENT = "agent"
ACP = "acp"

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _run(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def peer_pid(client_port: int) -> int | None:
    """The process whose end of a loopback TCP connection uses ``client_port``."""
    if not shutil.which("lsof"):
        return None
    out = _run(["lsof", "-nP", f"-iTCP:{client_port}", "-sTCP:ESTABLISHED", "-Fpn"])
    own = os.getpid()
    pid = None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n") and pid and pid != own:
            local = line[1:].split("->", 1)[0]
            if local.rsplit(":", 1)[-1] == str(client_port):
                return pid
    return None


def _parents() -> dict[int, int]:
    parents: dict[int, int] = {}
    for line in _run(["ps", "-A", "-o", "pid=,ppid="]).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            parents[int(parts[0])] = int(parts[1])
    return parents


def classify_pid(pid: int | None, acp_pids: set[int], parents: dict[int, int] | None = None) -> str:
    if not pid:
        return USER
    parents = _parents() if parents is None else parents
    own = os.getpid()
    seen: set[int] = set()
    current: int | None = pid
    is_agent = False
    while current and current not in seen:
        if current in acp_pids:
            return ACP
        if current == own:
            is_agent = True
        seen.add(current)
        current = parents.get(current)
    return AGENT if is_agent else USER


def classify(client_host: str | None, client_port: int | None, acp_pids: set[int]) -> str:
    if not client_port or (client_host or "") not in _LOOPBACK:
        return USER
    return classify_pid(peer_pid(client_port), acp_pids)
