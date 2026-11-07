"""Check whether web addresses respond, with status codes and response times.

Saved groups live in sites.json next to this script. Manual use:
python run.py --set group=public    or    python run.py --options group
"""
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))
from myharness_agent import (  # noqa: E402
    dry_run, item, load_params, log, options, options_request, progress, result, stopping,
)

SITES = Path(__file__).resolve().parent / "sites.json"


def groups():
    try:
        data = json.loads(SITES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    value = data.get("groups") if isinstance(data, dict) else None
    return {str(k): [str(u) for u in v if isinstance(u, str)]
            for k, v in (value or {}).items() if isinstance(v, list)}


def check(url, method, timeout):
    started = time.monotonic()
    request = Request(url, method=method, headers={"User-Agent": "myharness-site-check"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, None, time.monotonic() - started
    except HTTPError as exc:
        return exc.code, None, time.monotonic() - started
    except (URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, str(reason), time.monotonic() - started


def main():
    params = load_params({"method": "HEAD", "timeout": 10})
    saved = groups()
    if options_request() == "group":
        options(sorted(saved))
        return 0
    urls = list(params.get("urls") or [])
    group = str(params.get("group") or "").strip()
    if group:
        if group not in saved:
            known = ", ".join(sorted(saved)) or "none"
            item("group", "failed", detail=f"Unknown group {group!r}. Groups: {known}")
            result(f"No saved group named {group!r}. Known groups: {known}")
            return 1
        urls = saved[group] + [u for u in urls if u not in saved[group]]
    if not urls:
        result("No addresses given. Choose a saved group or list addresses.")
        return 1
    if dry_run():
        for url in urls:
            item(url, "info", detail=f"Would send {params['method']}")
        result(f"Dry run: {len(urls)} address(es) would be checked.")
        return 0
    rows, up = [], 0
    for index, url in enumerate(urls):
        if stopping():
            item(url, "skipped", detail="Stopped")
            continue
        progress(index, len(urls), "Checking " + url)
        item(url, "running")
        status, error, elapsed = check(url, params["method"], params["timeout"])
        ms = f"{elapsed * 1000:.0f} ms"
        if status is not None and status < 400:
            up += 1
            item(url, "ok", detail=f"{status} in {ms}", data={"status": status, "ms": round(elapsed * 1000)})
        else:
            detail = f"{status} in {ms}" if status is not None else f"{error} after {ms}"
            item(url, "failed", detail=detail, data={"status": status, "error": error})
            log(f"{url}: {detail}", "warn")
        rows.append([url, status if status is not None else "", ms, error or ""])
    progress(len(urls), len(urls), "Done")
    result(f"{up} of {len(urls)} address(es) responded without an error",
           table={"columns": ["Address", "Status", "Time", "Error"], "rows": rows})
    # Down sites are findings, not a failure of the check itself.
    return 0


if __name__ == "__main__":
    sys.exit(main())
