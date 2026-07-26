"""Print one scalar value from an agent_config.yaml, or nothing on failure.

Usage: read_backend_url.py <config-path> [dotted.key]

The default key is ``desktop.backend_url``, which is how ``run.sh``/``run.cmd``
point the Rust TUI at the configured backend instead of always assuming
loopback. The launchers also use it for ``server.host`` and ``server.port``.

Client machines frequently lack PyYAML, so this tries a proper YAML parse first
and falls back to a stdlib-only line scan of the relevant block. Only two levels
of nesting are supported, which covers every key the launchers need. Prints
nothing and exits 0 on any error so callers can fall back to their defaults.
"""
import sys

DEFAULT_KEY = "desktop.backend_url"


def _via_yaml(path, keys):
    import yaml
    with open(path, encoding="utf-8") as handle:
        current = yaml.safe_load(handle) or {}
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return ""
        current = current[key]
    if current is None or isinstance(current, (dict, list)):
        return ""
    return str(current).strip()


def _clean(value):
    # Strip an inline comment only when the value is not quoted.
    if value[:1] not in ("'", '"') and "#" in value:
        value = value.split("#", 1)[0].strip()
    return value.strip().strip('"').strip("'").strip()


def _via_scan(path, keys):
    # Minimal fallback for machines without PyYAML: walk the file, track the
    # current top-level key, and return the value when we reach the leaf.
    if len(keys) == 1:
        top_key, leaf = None, keys[0]
    else:
        top_key, leaf = keys[0], keys[-1]
    seen_top = top_key is None
    current_top = None
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent == 0:
                current_top = stripped.split(":", 1)[0].strip()
                if top_key is None and current_top == leaf and ":" in stripped:
                    return _clean(stripped.split(":", 1)[1].strip())
                continue
            if top_key is not None and current_top != top_key:
                continue
            if not seen_top and current_top == top_key:
                seen_top = True
            key = stripped.split(":", 1)[0].strip()
            if key != leaf or ":" not in stripped:
                continue
            return _clean(stripped.split(":", 1)[1].strip())
    return ""


def main():
    if len(sys.argv) < 2:
        return
    path = sys.argv[1]
    keys = [part for part in (sys.argv[2] if len(sys.argv) > 2 else DEFAULT_KEY).split(".") if part]
    if not keys:
        return
    for resolver in (_via_yaml, _via_scan):
        try:
            value = resolver(path, keys)
        except Exception:
            continue
        if value:
            print(value)
            return


if __name__ == "__main__":
    main()
