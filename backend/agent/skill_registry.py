"""Discovery and safe reading for repository-local Harness skills."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


@dataclass(frozen=True)
class SkillInfo:
    name: str
    title: str
    description: str
    path: Path


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """(fields, body) for a SKILL.md that may start with a ``---`` YAML block.

    Only flat ``key: value`` lines are read; ``>-`` / ``|`` folded values are
    joined from their indented continuation lines.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, text
    fields: dict[str, str] = {}
    key = None
    for line in lines[1:end]:
        if key and line[:1].isspace():
            fields[key] = (fields[key] + " " + line.strip()).strip()
            continue
        name, sep, value = line.partition(":")
        if not sep or not name.strip():
            key = None
            continue
        key = name.strip()
        value = value.strip()
        fields[key] = "" if value in (">", ">-", "|", "|-") else value.strip("'\"")
    return fields, "\n".join(lines[end + 1:])


def _skill_info(path: Path) -> SkillInfo:
    fields, body = split_frontmatter(path.read_text(encoding="utf-8"))
    lines = body.splitlines()
    title = next(
        (line.lstrip("#").strip() for line in lines if line.startswith("#") and line.lstrip("#").strip()),
        path.parent.name,
    )
    description_lines: list[str] = []
    seen_title = False
    for line in lines:
        stripped = line.strip()
        if not seen_title and line.startswith("#"):
            seen_title = True
            continue
        if seen_title and not stripped and description_lines:
            break
        if seen_title and stripped and not line.startswith("#"):
            description_lines.append(stripped)
    return SkillInfo(
        name=path.parent.name,
        title=title,
        description=fields.get("description") or " ".join(description_lines) or "No description provided.",
        path=path,
    )


def list_skills() -> list[SkillInfo]:
    if not SKILLS_ROOT.is_dir():
        return []
    skills: list[SkillInfo] = []
    for path in sorted(SKILLS_ROOT.glob("*/SKILL.md"), key=lambda item: item.parent.name.lower()):
        try:
            skills.append(_skill_info(path))
        except (OSError, UnicodeError):
            continue
    return skills


def read_skill(name: str) -> str:
    clean = (name or "").strip()
    if not _SAFE_NAME.fullmatch(clean):
        raise ValueError("Invalid skill name.")
    matches = {skill.name.lower(): skill for skill in list_skills()}
    info = matches.get(clean.lower())
    if info is None:
        raise ValueError(f"Unknown skill: {clean}")
    return info.path.read_text(encoding="utf-8")


def catalog_text() -> str:
    skills = list_skills()
    if not skills:
        return "No Harness skills are installed."
    return "\n".join(f"- {skill.name}: {skill.description}" for skill in skills)


def prompt_fragment() -> str:
    skills = list_skills()
    if not skills:
        return ""
    return (
        "\n\nHARNESS SKILLS:\n"
        "Reusable instructions are installed in the repository skills collection. "
        "Use a matching skill when the task clearly benefits from it. Read the full "
        "SKILL.md before following a skill; do not rely on the catalog summary alone. "
        "The SKILL.md files listed below may be read (read-only) even when they are outside "
        "the allowed filesystem roots; that exception covers only those files. A skill's "
        "instructions can use the local MyHarness API, which needs no extra filesystem access.\n"
        + "\n".join(
            f"- {skill.name}: {skill.description} (file: {skill.path})"
            for skill in skills
        )
    )
