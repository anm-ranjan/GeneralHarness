"""Immutable local evidence, separate from the model's compact working history."""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


class HistoryEvidence:
    def __init__(self, root):
        self.root = Path(root)

    def archive(self, name, arguments, result):
        record = {"name": name, "arguments": arguments, "result": result}
        blob = json.dumps(record, ensure_ascii=False, sort_keys=True)
        evidence_id = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{evidence_id}.json"
        if not path.exists() or path.read_text(encoding="utf-8") != blob:
            tmp = self.root / f".{uuid.uuid4().hex}.tmp"
            try:
                tmp.write_text(blob, encoding="utf-8")
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        return evidence_id

    def read(self, evidence_id, field="result", offset=0, limit=4000):
        if not isinstance(evidence_id, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence_id):
            return {"error": "Invalid evidence id."}
        if field not in {"result", "arguments"}:
            return {"error": "Evidence field must be result or arguments."}
        try:
            blob = (self.root / f"{evidence_id}.json").read_text(encoding="utf-8")
            if hashlib.sha256(blob.encode("utf-8")).hexdigest() != evidence_id:
                return {"error": "Archived evidence failed its hash check."}
            record = json.loads(blob)
            if not isinstance(record, dict) or not isinstance(record.get("result"), str) or not isinstance(record.get("arguments"), dict):
                return {"error": "Malformed archived evidence."}
            text = record[field] if field == "result" else json.dumps(record[field], ensure_ascii=False, sort_keys=True)
            offset = max(0, int(offset))
            limit = max(1, min(8000, int(limit)))
        except (OSError, ValueError, TypeError, KeyError):
            return {"error": "Original evidence is unavailable."}
        end = min(len(text), offset + limit)
        return {"evidence_id": evidence_id, "name": record["name"], "field": field,
                "offset": offset, "next_offset": end if end < len(text) else None,
                "total_chars": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text[offset:end], "historical": True,
                "notice": "Exact historical tool-returned text; source-tool truncation limits still apply. "
                          "May originate from interrupted work; it does not establish task completion, "
                          "current file contents or permission to repeat a write."}

    def search(self, query="", offset=0, limit=20):
        """Find originals even after their receipts leave bounded task state."""
        try:
            offset, limit = max(0, int(offset)), max(1, min(50, int(limit)))
        except (TypeError, ValueError):
            return {"error": "Invalid evidence pagination."}
        found = []
        query = str(query).casefold()
        for path in sorted(self.root.glob("*.json")):
            try:
                blob = path.read_text(encoding="utf-8")
                if query not in blob.casefold() or hashlib.sha256(blob.encode("utf-8")).hexdigest() != path.stem:
                    continue
                record = json.loads(blob)
                found.append({"evidence_id": path.stem, "name": record["name"],
                              "path": str(record["arguments"].get("file_path", ""))[:300],
                              "preview": str(record["result"])[:200]})
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
        end = min(len(found), offset + limit)
        return {"evidence": found[offset:end], "total": len(found),
                "next_offset": end if end < len(found) else None, "historical": True,
                "task_completion": "Not established by tool observations; audit may include interrupted work."}
