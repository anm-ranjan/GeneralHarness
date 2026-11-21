"""Global Antigravity customizations, installed only from Settings.

MyHarness sends Antigravity nothing but the folder limits (see
``acp_provider.build_system_append``). The working habits it used to add, and
this checkout's skills, can instead live in Antigravity's own global config,
where they apply to every Antigravity session, inside MyHarness or not:

- ``<config>/rules/working-habits.md``: an always-on rule (``RULE_TEXT``),
- ``<config>/skills.json``: one entry pointing at this checkout's ``skills/``
  folder, so the skills are read in place and stay current.

``<config>`` is ``~/.gemini/config``. Other entries in ``skills.json`` and
other rule files are left alone; removing takes out only what was added here.
Skills find their thread from Antigravity's own ``ANTIGRAVITY_CONVERSATION_ID``
(see ``/api/acp/session``), so none needs to be left out today;
``SESSION_BOUND_SKILLS`` remains for one that cannot.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import skill_registry

RULE_FILE = "working-habits.md"
SESSION_BOUND_SKILLS: tuple[str, ...] = ()

RULE_TEXT = """---
trigger: always_on
---

When a request is genuinely ambiguous and the readings lead to materially different work, and an
`ask_user` tool is available, call it with one concrete question and wait for the answer rather than
guessing. Do not use it for anything you can settle by reading the code.

Be concise. When you changed files or ran commands, end with a short summary: files changed,
commands run, test result. Leave it out when you did neither.
"""


class SetupError(Exception):
    """A file is in a state that installing or removing must not overwrite."""


def config_dir() -> Path:
    override = os.environ.get("MYHARNESS_ANTIGRAVITY_CONFIG_DIR", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".gemini" / "config"


def _rule_path() -> Path:
    return config_dir() / "rules" / RULE_FILE


def _skills_json_path() -> Path:
    return config_dir() / "skills.json"


def _skills_root() -> str:
    return str(skill_registry.SKILLS_ROOT.resolve())


def _offered_skills() -> tuple[list[str], list[str]]:
    """(skills Antigravity gets, skills left out because they need a MyHarness session)."""
    names = [skill.name for skill in skill_registry.list_skills()]
    return ([n for n in names if n not in SESSION_BOUND_SKILLS],
            [n for n in names if n in SESSION_BOUND_SKILLS])


def _read_skills_json() -> dict[str, Any]:
    path = _skills_json_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, ValueError) as exc:
        raise SetupError(f"{path} is not valid JSON; fix or remove it first ({exc}).") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
        raise SetupError(f"{path} does not look like a skills.json (expected an object with an entries list).")
    return data


def _is_ours(entry: Any) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        return False
    try:
        return str(Path(entry["path"]).expanduser().resolve()) == _skills_root()
    except OSError:
        return False


def _our_entry() -> dict[str, Any]:
    _offered, excluded = _offered_skills()
    entry: dict[str, Any] = {"path": _skills_root()}
    if excluded:
        entry["exclude"] = excluded
    return entry


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def status() -> dict[str, Any]:
    rule = _rule_path()
    rule_text = rule.read_text(encoding="utf-8") if rule.is_file() else None
    skills_error = ""
    entry = None
    try:
        entry = next((e for e in _read_skills_json().get("entries", []) if _is_ours(e)), None)
    except SetupError as exc:
        skills_error = str(exc)
    offered, excluded = _offered_skills()
    rule_current = rule_text == RULE_TEXT
    skills_current = entry == _our_entry()
    return {
        "config_dir": str(config_dir()),
        "rule_path": str(rule),
        "rule_text": RULE_TEXT,
        "rule_installed": rule_text is not None,
        "skills_json_path": str(_skills_json_path()),
        "skills_root": _skills_root(),
        "skills_installed": entry is not None,
        "skills": offered,
        "excluded_skills": excluded,
        "error": skills_error,
        "installed": rule_current and skills_current,
        "partial": (rule_text is not None or entry is not None) and not (rule_current and skills_current),
    }


def install() -> dict[str, Any]:
    data = _read_skills_json()
    entries = [e for e in data.get("entries", []) if not _is_ours(e)]
    data["entries"] = entries + [_our_entry()]
    _write_atomic(_rule_path(), RULE_TEXT)
    _write_atomic(_skills_json_path(), json.dumps(data, indent=2) + "\n")
    return status()


def remove() -> dict[str, Any]:
    path = _skills_json_path()
    if path.exists():
        data = _read_skills_json()
        entries = data.get("entries", [])
        kept = [e for e in entries if not _is_ours(e)]
        if len(kept) != len(entries):
            data["entries"] = kept
            if not kept and set(data) <= {"entries"}:
                path.unlink()
            else:
                _write_atomic(path, json.dumps(data, indent=2) + "\n")
    _rule_path().unlink(missing_ok=True)
    return status()
