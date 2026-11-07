"""Summarize folders: file count, total size, file types and largest files.

Read-only. Manual use: python run.py --set 'paths=["/some/folder"]'
"""
import heapq
import os
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))
from myharness_agent import dry_run, item, load_params, progress, result, stopping  # noqa: E402


def human(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def scan(root, include_hidden, max_files, top):
    files = total = 0
    types = Counter()
    largest = []
    truncated = False
    for current, dirs, names in os.walk(root, onerror=lambda exc: None):
        if stopping():
            break
        if not include_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            names = [n for n in names if not n.startswith(".")]
        dirs.sort()
        for name in names:
            path = os.path.join(current, name)
            try:
                if os.path.islink(path):
                    continue
                size = os.stat(path).st_size
            except OSError:
                continue
            files += 1
            total += size
            types[Path(name).suffix.lower() or "(none)"] += 1
            entry = (size, path)
            if len(largest) < top:
                heapq.heappush(largest, entry)
            else:
                heapq.heappushpop(largest, entry)
            if files >= max_files:
                truncated = True
                break
        if truncated:
            break
    return files, total, types, sorted(largest, reverse=True), truncated


def main():
    params = load_params({"top": 10, "include_hidden": False, "max_files": 200000})
    folders = params.get("paths") or []
    if not folders:
        result("No folders given.")
        return 1
    if dry_run():
        for folder in folders:
            item(folder, "info", label=Path(folder).name or folder, detail="Would be scanned")
        result(f"Dry run: {len(folders)} folder(s) would be scanned (read-only).")
        return 0
    rows, sections, failed = [], [], 0
    for index, folder in enumerate(folders):
        if stopping():
            item(folder, "skipped", label=Path(folder).name or folder, detail="Stopped")
            continue
        label = Path(folder).name or folder
        item(folder, "running", label=label)
        progress(index, len(folders), "Scanning " + label)
        if not os.path.isdir(folder):
            failed += 1
            item(folder, "failed", label=label, detail="Not a folder")
            continue
        files, total, types, largest, truncated = scan(
            folder, params["include_hidden"], params["max_files"], params["top"])
        detail = f"{files} files, {human(total)}" + (" (stopped at max_files)" if truncated else "")
        item(folder, "ok", label=label, detail=detail,
             data={"files": files, "bytes": total, "truncated": truncated})
        common = ", ".join(f"{ext} {count}" for ext, count in types.most_common(5))
        rows.append([folder, files, human(total), common])
        lines = [f"### {label}", "", f"{detail}. Most common types: {common or 'none'}.", ""]
        if largest:
            lines += ["| Size | File |", "| --- | --- |"]
            lines += [f"| {human(size)} | `{os.path.relpath(path, folder)}` |" for size, path in largest]
        sections.append("\n".join(lines))
    progress(len(folders), len(folders), "Done")
    summary = f"Scanned {len(rows)} of {len(folders)} folder(s)" + (f", {failed} failed" if failed else "")
    result(summary, table={"columns": ["Folder", "Files", "Size", "Common types"], "rows": rows},
           markdown="\n\n".join(sections))
    return 1 if failed and not rows else 0


if __name__ == "__main__":
    sys.exit(main())
