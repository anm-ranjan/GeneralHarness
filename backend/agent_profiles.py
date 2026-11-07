"""Validated, repository-owned routine agent definitions (not native subagents).

A definition is ``<root>/<name>/AGENT.md`` (YAML frontmatter + instructions)
plus an optional Python entry script. Two roots are listed: the bundled
``agents/`` folder of this checkout (``MYHARNESS_AGENTS_DIR`` overrides it)
and the user's ``data/agents/`` folder, where /create_agent installs drafts
(``MYHARNESS_USER_AGENTS_DIR`` overrides it). Deployment syncs replace the
checkout but keep ``data/``, so user definitions survive them. A user
definition with the same name shadows the bundled one. See
``agents/README.md`` for the contract.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
import re
import threading

import yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TYPES = {"string", "text", "int", "float", "bool", "enum", "path", "paths", "strings"}
APPROVAL_MODES = {"inherit", "always_ask", "shell_only", "auto_approve"}
PLATFORMS = {"windows", "macos", "linux"}
DEFAULT_TOOLS = ("run_agent_script", "file_read", "file_list", "content_search")

_CACHE: dict = {}
_CACHE_LOCK = threading.RLock()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def agents_root() -> Path:
    """Bundled definitions, tracked in the repository."""
    override = os.environ.get("MYHARNESS_AGENTS_DIR")
    return Path(override or repo_root() / "agents").resolve()


def user_agents_root() -> Path:
    """User definitions (installed by /create_agent), kept under data/."""
    override = os.environ.get("MYHARNESS_USER_AGENTS_DIR")
    if override:
        return Path(override).resolve()
    import utils

    data_dir = os.environ.get("MYHARNESS_WEB_DATA_DIR", utils.DATA_DIR)
    return (Path(data_dir) / "agents").resolve()


def agents_roots() -> list[Path]:
    roots = [agents_root()]
    user = user_agents_root()
    if user not in roots:
        roots.append(user)
    return roots


def helper_lib_dir() -> Path:
    """Shared helper code for entry scripts (always the bundled copy)."""
    return repo_root() / "agents" / "_lib"


@dataclass(frozen=True)
class AgentProfile:
    name: str
    path: Path
    description: str = ""
    body: str = ""
    version: int = 1
    entry: str = ""
    python: str = ""
    params: dict = field(default_factory=dict)
    dry_run: bool = False
    approval: str = "shell_only"
    max_iterations: int = 12
    reasoning_effort: str = ""
    tools: tuple = DEFAULT_TOOLS
    working_directory: str = ""
    timeout_seconds: int = 3600
    on_stop: dict = field(default_factory=lambda: {"grace_seconds": 5})
    uses_skills: tuple = ()
    requires: tuple = ()
    platform: tuple = ()
    errors: tuple = ()

    def public(self, detail: bool = False) -> dict:
        value = asdict(self)
        value.pop("path")
        value["has_entry"] = bool(self.entry)
        value["source"] = "user" if self.path.is_relative_to(user_agents_root()) else "bundled"
        if detail:
            value["files"] = sorted(
                str(p.relative_to(self.path)) for p in self.path.rglob("*")
                if p.is_file() and p.resolve().is_relative_to(self.path) and "__pycache__" not in p.parts
            )
        else:
            value.pop("body")
        return value


def _known_tools() -> set[str]:
    from tool_defs import COLLABORATION_TOOLS, TOOLS

    return {t["function"]["name"] for t in TOOLS + COLLABORATION_TOOLS} | {"run_agent_script"}


def _validate_params(params: dict, data: dict, errors: list[str]) -> None:
    for name, spec in params.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            errors.append("Parameter names must be identifiers")
        if not isinstance(spec, dict) or spec.get("type") not in TYPES:
            errors.append(f"{name}: invalid parameter type")
            continue
        if spec["type"] == "enum" and (
            not isinstance(spec.get("options"), list) or not spec["options"]
            or any(not isinstance(x, str) for x in spec["options"])
        ):
            errors.append(f"{name}: enum needs string options")
        if spec.get("kind", "any") not in {"file", "dir", "any"}:
            errors.append(f"{name}: invalid path kind")
        for limit in ("min", "max"):
            if limit in spec and (type(spec[limit]) not in (int, float) or not math.isfinite(spec[limit])):
                errors.append(f"{name}: {limit} must be finite numeric")
        for limit in ("max_items", "max_length"):
            if limit in spec and (type(spec[limit]) is not int or spec[limit] < 1):
                errors.append(f"{name}: {limit} must be a positive integer")
        if type(spec.get("required", False)) is not bool or type(spec.get("must_exist", True)) is not bool:
            errors.append(f"{name}: required/must_exist must be boolean")
        if "pattern" in spec:
            try:
                re.compile(spec["pattern"])
            except (TypeError, re.error):
                errors.append(f"{name}: invalid pattern")
        if "options_from" in spec:
            # Choices listed by the entry script when the form opens; the
            # value stays free text, so a stale list never blocks a run.
            if spec["options_from"] != "script" or spec["type"] != "string":
                errors.append(f"{name}: options_from: script needs type string")
            elif not data.get("entry"):
                errors.append(f"{name}: options_from: script needs an entry script")
            depends = spec.get("options_depends", [])
            if not isinstance(depends, list) or any(
                d not in params or d == name
                or not isinstance(params[d], dict) or params[d].get("type") in ("path", "paths")
                for d in depends
            ):
                errors.append(f"{name}: options_depends must list other non-path parameters")
        elif "options_depends" in spec:
            errors.append(f"{name}: options_depends needs options_from")


def read_profile(folder) -> AgentProfile:
    folder = Path(folder).resolve()
    errors: list[str] = []
    data: dict = {}
    body = ""
    try:
        raw = (folder / "AGENT.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", raw, re.S)
        if not match:
            raise ValueError("AGENT.md needs YAML frontmatter")
        data = yaml.safe_load(match[1])
        if not isinstance(data, dict):
            raise ValueError("Manifest must be a map")
        body = match[2].strip()
        if data.get("name") != folder.name or not NAME_RE.fullmatch(str(data.get("name", ""))):
            errors.append("name must match its folder and use lowercase letters, numbers and hyphens")
        description = data.get("description")
        if not isinstance(description, str) or not description.strip() or "\n" in description:
            errors.append("description must be one line")
        tools = data.get("tools", list(DEFAULT_TOOLS))
        if not isinstance(tools, (list, tuple)):
            raise ValueError("tools must be a list")
        unknown = {str(t) for t in tools} - _known_tools()
        if unknown:
            errors.append("Unknown tools: " + ", ".join(sorted(unknown)))
        entry = data.get("entry", "")
        if entry:
            target = (folder / str(entry)).resolve()
            if (
                Path(str(entry)).is_absolute() or ".." in Path(str(entry)).parts
                or not target.is_relative_to(folder) or not target.is_file() or target.suffix != ".py"
            ):
                errors.append("entry must be an existing Python file inside the agent folder")
        else:
            data["tools"] = tools = [t for t in tools if t != "run_agent_script"]
        if data.get("uses_skills"):
            data["tools"] = list(dict.fromkeys(list(tools) + ["skill_list", "skill_read"]))
        if data.get("approval", "shell_only") not in APPROVAL_MODES:
            errors.append("Invalid approval mode")
        for key, default, low, high in (
            ("version", 1, 1, 1_000_000),
            ("max_iterations", 12, 1, 1000),
            ("timeout_seconds", 3600, 1, 86400),
        ):
            value = data.get(key, default)
            if type(value) is not int or not low <= value <= high:
                errors.append(f"{key} must be an integer in {low}..{high}")
        stop = data.get("on_stop", {"grace_seconds": 5})
        grace = stop.get("grace_seconds", 5) if isinstance(stop, dict) else None
        if type(grace) not in (int, float) or not 0 <= grace <= 30:
            errors.append("on_stop.grace_seconds must be in 0..30")
        params = data.get("params", {}) or {}
        if not isinstance(params, dict):
            raise ValueError("params must be a map")
        data["params"] = params
        _validate_params(params, data, errors)
        if type(data.get("dry_run", False)) is not bool:
            errors.append("dry_run must be boolean")
        for key in ("python", "reasoning_effort", "working_directory"):
            if key in data and not isinstance(data[key], str):
                errors.append(f"{key} must be a string")
    except Exception as exc:  # malformed YAML, unreadable file, wrong shapes
        errors.append(str(exc))
        if not isinstance(data, dict):
            data = {}
    keys = set(AgentProfile.__dataclass_fields__) - {"name", "path", "body", "errors"}
    values = {k: v for k, v in data.items() if k in keys}
    for key in ("tools", "uses_skills", "requires", "platform"):
        if key in values:
            if not isinstance(values[key], (list, tuple)):
                errors.append(f"{key} must be a list")
                values[key] = ()
            values[key] = tuple(values[key])
    for key in ("tools", "uses_skills", "platform"):
        if key in values and any(not isinstance(v, str) for v in values[key]):
            errors.append(f"{key} entries must be strings")
            values[key] = ()
    if not isinstance(values.get("on_stop", {}), dict):
        values.pop("on_stop")
    if not isinstance(values.get("params", {}), dict):
        values.pop("params")
    for key in ("python", "reasoning_effort", "working_directory", "entry", "description", "approval"):
        if key in values and not isinstance(values[key], str):
            values.pop(key)
    for key in ("version", "max_iterations", "timeout_seconds"):
        if key in values and type(values[key]) is not int:
            values.pop(key)
    if values.get("uses_skills"):
        import skill_registry

        available = {s.name for s in skill_registry.list_skills()}
        for name in values["uses_skills"]:
            if name not in available:
                errors.append(f"Unknown skill: {name}")
    if any(p not in PLATFORMS for p in values.get("platform", ())):
        errors.append("Invalid platform")
    return AgentProfile(name=folder.name, path=folder, body=body, errors=tuple(errors), **values)


def list_profiles() -> list[AgentProfile]:
    by_name: dict[str, AgentProfile] = {}
    for root in agents_roots():
        # Later roots (user definitions) shadow earlier ones of the same name.
        for profile in _list_root(root):
            by_name[profile.name] = profile
    return [by_name[name] for name in sorted(by_name)]


def _list_root(root: Path) -> list[AgentProfile]:
    if not root.is_dir():
        return []
    profiles = []
    with _CACHE_LOCK:
        for folder in sorted(root.iterdir()):
            if (
                not folder.is_dir() or folder.name.startswith(("_", "."))
                or not (folder / "AGENT.md").is_file() or not folder.resolve().is_relative_to(root)
            ):
                continue
            try:
                stat = (folder / "AGENT.md").stat()
                # Directory metadata invalidates missing/new entry files and symlink changes.
                stamp = (stat.st_mtime_ns, stat.st_size, folder.stat().st_mtime_ns)
                key = str(folder.resolve())
                cached = _CACHE.get(key)
                if cached and cached[0] == stamp:
                    profile = cached[1]
                    if profile.entry:
                        entry = (profile.path / profile.entry).resolve()
                        if not entry.is_file() or not entry.is_relative_to(profile.path):
                            profile = read_profile(folder)
                else:
                    profile = read_profile(folder)
                    _CACHE[key] = (stamp, profile)
                profiles.append(profile)
            except OSError as exc:
                profiles.append(AgentProfile(name=folder.name, path=folder.resolve(), errors=(str(exc),)))
        # Bound stale cache entries when definitions are removed or test roots change.
        if len(_CACHE) > 512:
            _CACHE.clear()
    return profiles


def get_profile(name: str) -> AgentProfile | None:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return None
    return next((p for p in list_profiles() if p.name == name), None)


def validate_params(profile: AgentProfile, values) -> tuple[dict, list[str]]:
    """Check and normalize parameters; the backend is authoritative."""
    import utils

    if not isinstance(values, dict):
        return {}, ["params must be an object"]
    result: dict = {}
    errors = [f"{k}: unknown parameter" for k in values if k not in profile.params]
    for name, spec in profile.params.items():
        value = values.get(name, spec.get("default"))
        if value is None or value == "" or value == []:
            if spec.get("required"):
                errors.append(f"{name}: required")
            continue
        typ = spec["type"]
        problem = None
        if typ in ("int", "float"):
            if (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or (typ == "int" and type(value) is not int)
            ):
                problem = f"must be {typ}"
            elif value < spec.get("min", -math.inf) or value > spec.get("max", math.inf):
                problem = "outside allowed range"
        elif typ == "bool":
            if type(value) is not bool:
                problem = "must be boolean"
        elif typ in ("paths", "strings"):
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                problem = "must be a list of strings"
            elif len(value) > spec.get("max_items", 200):
                problem = "too many items"
        elif not isinstance(value, str):
            problem = "must be a string"
        if problem:
            errors.append(f"{name}: {problem}")
            continue
        if typ == "enum" and value not in spec["options"]:
            errors.append(f"{name}: invalid option")
        entries = value if typ in ("paths", "strings") else [value]
        normalized = []
        for v in entries:
            if isinstance(v, str):
                if len(v) > spec.get("max_length", 200_000):
                    errors.append(f"{name}: too long")
                if "pattern" in spec and re.fullmatch(spec["pattern"], v) is None:
                    errors.append(f"{name}: does not match pattern")
            if typ in ("path", "paths"):
                v = str(Path(v).expanduser().resolve())
                if not utils.is_path_allowed(v):
                    errors.append(f"{name}: path is not allowed: {v}")
                elif spec.get("must_exist", True) and not Path(v).exists():
                    errors.append(f"{name}: path does not exist: {v}")
                elif Path(v).exists() and spec.get("kind") == "dir" and not Path(v).is_dir():
                    errors.append(f"{name}: must be a directory")
                elif Path(v).exists() and spec.get("kind") == "file" and not Path(v).is_file():
                    errors.append(f"{name}: must be a file")
            normalized.append(v)
        result[name] = normalized if typ in ("paths", "strings") else normalized[0]
    return result, errors


def prompt_fragment(profile: AgentProfile) -> str:
    return (
        "\n\nRoutine agent: " + profile.name + "\n" + profile.body
        + "\nParameters:\n" + json.dumps(profile.params, ensure_ascii=False)
    )


def script_tool(profile: AgentProfile) -> dict:
    """run_agent_script with its params schema specialized from the manifest."""
    from tool_defs import RUN_AGENT_SCRIPT_TOOL

    properties = {}
    for key, spec in profile.params.items():
        typ = spec["type"]
        schema = {"type": {"int": "integer", "float": "number", "bool": "boolean",
                           "paths": "array", "strings": "array"}.get(typ, "string")}
        if typ in ("paths", "strings"):
            schema["items"] = {"type": "string"}
        if typ == "enum":
            schema["enum"] = spec["options"]
        for source, target in (("min", "minimum"), ("max", "maximum"), ("help", "description"),
                               ("default", "default"), ("pattern", "pattern")):
            if source in spec:
                schema[target] = spec[source]
        properties[key] = schema
    tool = deepcopy(RUN_AGENT_SCRIPT_TOOL)
    tool["function"]["description"] = (
        "Run the validated " + profile.name + " script. The backend builds the command; supply parameters only."
    )
    tool["function"]["parameters"]["properties"]["params"] = {
        "type": "object",
        "properties": properties,
        "required": [k for k, v in profile.params.items() if v.get("required")],
        "additionalProperties": False,
    }
    return tool
