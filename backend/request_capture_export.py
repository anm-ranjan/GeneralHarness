"""Self-contained, hash-verified native capture exports (not training labels)."""
import hashlib
import io
import json
import zipfile

try:
    from agent.request_capture import CaptureStore, now
except ImportError:  # backend/agent on sys.path (web app)
    from request_capture import CaptureStore, now


def export_request_captures(store, events=b""):
    records = [(p.name, p.read_bytes()) for p in sorted((store.root / "records").glob("*.json"))]
    if not records:
        raise FileNotFoundError("No request captures for this session")
    blobs = {}

    def blob(ref):
        if ref not in blobs:
            blobs[ref] = store.read_bytes(ref)
        return json.loads(blobs[ref])

    def packed(node):
        if node[0] == "dict":
            for _, child in node[1]:
                packed(child)
        elif node[0] == "list":
            for child in node[1]:
                packed(child)
        elif node[0] == "image":
            blob(node[1])
            blobs[node[2]] = store.read_bytes(node[2])

    for _, data in records:
        record = json.loads(data)
        if record["kind"] == "request":
            refs = record["payload"]
            if refs is None:
                raise ValueError("A request capture is incomplete")
            store.reconstruct(refs)
            for ref in [refs["body"], *(refs["messages"] or [])]:
                packed(blob(ref))
        elif record.get("response"):
            blob(record["response"])

    files = {"records/" + name: data for name, data in records}
    files.update({"blobs/" + ref: data for ref, data in blobs.items()})
    files["events.jsonl"] = events
    manifest = {"format": "myharness-request-capture-export", "version": 1, "exported_at": now(),
                "note": "Snapshot; missing run outcomes mean unknown/in-progress, not completed. Quality is ungraded.",
                "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                          for name, data in files.items()}}
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        for name, data in files.items():
            archive.writestr(name, data)
    return result.getvalue()


def main():
    """Export on the backend host directly to a chosen local/synced folder."""
    import argparse
    import os
    from pathlib import Path
    import re
    import uuid

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.session_id):
        parser.error("Invalid session ID")
    source = CaptureStore(args.data_dir / "request_captures" / args.session_id)
    try:
        from session_store import SessionStore
        events = SessionStore(args.data_dir).events_jsonl(args.session_id)
        data = export_request_captures(source, events)
        temporary = args.output.with_name("." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, args.output)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, "Export failed: " + str(exc) + "\n")
    print(str(args.output.resolve()))


if __name__ == "__main__":
    main()
