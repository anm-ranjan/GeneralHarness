"""A thin, read-mostly git facade for the Workspace Source Control panel.

Shells ``git`` inside a workspace root. Parsing uses ``--porcelain=v2`` so the output is stable
across git versions and locales. Path-allowlist enforcement is the caller's responsibility
(``web_app`` validates before calling these helpers).
"""

from __future__ import annotations

import subprocess

_TIMEOUT_SECONDS = 15
_MAX_DIFF_CHARS = 200_000

_STATUS_LABELS = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "typechange",
    "U": "unmerged",
    "?": "untracked",
}


def _run_git(root: str, args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git command timed out"
    return proc.returncode, proc.stdout, proc.stderr


def is_git_repo(root: str) -> bool:
    code, out, _ = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    return code == 0 and out.strip() == "true"


def _label(char: str) -> str:
    return _STATUS_LABELS.get(char, char)


def status(root: str) -> dict:
    """Return branch + staged/unstaged/untracked change lists.

    Always returns a dict; ``is_repo`` is False when ``root`` is not a working tree.
    """
    if not is_git_repo(root):
        return {"is_repo": False}

    code, out, err = _run_git(root, ["status", "--porcelain=v2", "--branch"])
    if code != 0:
        return {"is_repo": True, "error": err.strip() or "git status failed"}

    branch = ""
    ahead = 0
    behind = 0
    staged: list[dict] = []
    unstaged: list[dict] = []
    untracked: list[dict] = []

    for line in out.splitlines():
        if not line:
            continue
        if line.startswith("# branch.head"):
            branch = line.split(" ", 2)[2].strip()
        elif line.startswith("# branch.ab"):
            parts = line.split()
            for token in parts:
                if token.startswith("+"):
                    ahead = int(token[1:] or 0)
                elif token.startswith("-"):
                    behind = int(token[1:] or 0)
        elif line[0] in ("1", "2"):
            maxsplit = 8 if line[0] == "1" else 9
            fields = line.split(" ", maxsplit)
            xy = fields[1]
            path = fields[maxsplit]
            if line[0] == "2":
                # Rename/copy: "<path>\t<origPath>"; keep the new path.
                path = path.split("\t", 1)[0]
            index_char, worktree_char = xy[0], xy[1]
            if index_char != ".":
                staged.append({"path": path, "status": _label(index_char)})
            if worktree_char != ".":
                unstaged.append({"path": path, "status": _label(worktree_char)})
        elif line[0] == "u":
            fields = line.split(" ", 10)
            unstaged.append({"path": fields[10], "status": "unmerged"})
        elif line[0] == "?":
            untracked.append({"path": line.split(" ", 1)[1], "status": "untracked"})

    return {
        "is_repo": True,
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
    }


def file_diff(root: str, path: str, staged: bool = False) -> dict:
    """Return the unified diff for a single path (or the whole tree when ``path`` is empty)."""
    args = ["diff", "--no-color"]
    if staged:
        args.append("--cached")
    args.append("--")
    if path:
        args.append(path)
    code, out, err = _run_git(root, args)
    if code != 0:
        return {"diff_text": "", "error": err.strip() or "git diff failed"}
    truncated = len(out) > _MAX_DIFF_CHARS
    return {"diff_text": out[:_MAX_DIFF_CHARS], "truncated": truncated}


def stage(root: str, paths: list[str], unstage: bool = False) -> dict:
    """Stage or unstage the given paths."""
    if not paths:
        return {"ok": False, "error": "No paths provided"}
    if unstage:
        args = ["reset", "-q", "HEAD", "--", *paths]
    else:
        args = ["add", "--", *paths]
    code, _out, err = _run_git(root, args)
    if code != 0:
        return {"ok": False, "error": err.strip() or "git stage failed"}
    return {"ok": True}


def commit(root: str, message: str) -> dict:
    """Commit the currently staged changes."""
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "Commit message is required"}
    code, out, err = _run_git(root, ["commit", "-m", message])
    if code != 0:
        detail = (err.strip() or out.strip() or "git commit failed")
        return {"ok": False, "error": detail}
    head_code, head_out, _ = _run_git(root, ["rev-parse", "--short", "HEAD"])
    commit_hash = head_out.strip() if head_code == 0 else ""
    return {"ok": True, "hash": commit_hash, "summary": out.strip()}
