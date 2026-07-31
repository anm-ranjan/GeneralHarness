"""Host-local encrypted credentials shared by every MyHarness client.

The encrypted payload and its random AES key intentionally use the same file
format on every OS. Filesystem permissions and full-disk encryption remain the
host boundary; this store primarily prevents credentials from appearing in
YAML, logs, source control, and ordinary file inspection.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


NATIVE_API_KEY = "MYHARNESS_API_KEY"
STT_API_KEY = "MYHARNESS_STT_API_KEY"
ALLOWED_CREDENTIALS = frozenset({NATIVE_API_KEY, STT_API_KEY})

_AAD = b"myharness.credentials.v1"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_lock = threading.RLock()


class CredentialStoreError(RuntimeError):
    pass


def credential_directory(directory: str | os.PathLike[str] | None = None) -> Path:
    raw = str(directory or os.environ.get("MYHARNESS_CREDENTIALS_DIR", "")).strip()
    return Path(raw).expanduser() if raw else Path.home() / ".myharness"


def credential_paths(directory: str | os.PathLike[str] | None = None) -> tuple[Path, Path]:
    root = credential_directory(directory)
    return root / "credentials.enc", root / "credentials.key"


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise CredentialStoreError("Credential directory must be a real directory")
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise CredentialStoreError(f"Could not secure credential directory: {exc}") from exc


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise CredentialStoreError(f"Credential path must not be a symbolic link: {path.name}")


def _atomic_write(path: Path, data: bytes) -> None:
    _ensure_private_directory(path.parent)
    _reject_symlink(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _load_key(key_path: Path, *, create: bool) -> bytes | None:
    _reject_symlink(key_path)
    if key_path.exists():
        try:
            key = key_path.read_bytes()
        except OSError as exc:
            raise CredentialStoreError(f"Could not read the credential key: {exc}") from exc
        if len(key) != _KEY_BYTES:
            raise CredentialStoreError("Credential key has an invalid length")
        return key
    if not create:
        return None
    key = os.urandom(_KEY_BYTES)
    _atomic_write(key_path, key)
    return key


def load_credentials(directory: str | os.PathLike[str] | None = None) -> dict[str, str]:
    encrypted_path, key_path = credential_paths(directory)
    with _lock:
        _reject_symlink(encrypted_path)
        if not encrypted_path.exists():
            return {}
        key = _load_key(key_path, create=False)
        if key is None:
            raise CredentialStoreError("Encrypted credentials exist but the host key is missing")
        try:
            envelope = json.loads(encrypted_path.read_text(encoding="utf-8"))
            if envelope.get("version") != 1 or envelope.get("cipher") != "AES-256-GCM":
                raise CredentialStoreError("Unsupported credential file format")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, _AAD)
            decoded = json.loads(plaintext.decode("utf-8"))
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError("Could not decrypt the credential file") from exc
        if not isinstance(decoded, dict):
            raise CredentialStoreError("Credential payload must be an object")
        return {
            name: str(value)
            for name, value in decoded.items()
            if name in ALLOWED_CREDENTIALS and isinstance(value, str) and value
        }


def save_credentials(credentials: dict[str, str], directory: str | os.PathLike[str] | None = None) -> None:
    unknown = set(credentials) - ALLOWED_CREDENTIALS
    if unknown:
        raise CredentialStoreError("Unsupported credential name")
    cleaned = {name: str(value).strip() for name, value in credentials.items() if str(value).strip()}
    encrypted_path, key_path = credential_paths(directory)
    with _lock:
        _ensure_private_directory(encrypted_path.parent)
        key = _load_key(key_path, create=True)
        nonce = os.urandom(_NONCE_BYTES)
        plaintext = json.dumps(cleaned, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, _AAD)
        envelope = {
            "version": 1,
            "cipher": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        _atomic_write(encrypted_path, (json.dumps(envelope, sort_keys=True) + "\n").encode("utf-8"))


def update_credentials(
    replacements: dict[str, str] | None = None,
    removals: list[str] | tuple[str, ...] | None = None,
    directory: str | os.PathLike[str] | None = None,
) -> dict[str, bool]:
    replacements = replacements or {}
    removals = removals or []
    if (set(replacements) | set(removals)) - ALLOWED_CREDENTIALS:
        raise CredentialStoreError("Unsupported credential name")
    with _lock:
        current = load_credentials(directory)
        for name in removals:
            current.pop(name, None)
        for name, value in replacements.items():
            cleaned = str(value).strip()
            if cleaned:
                current[name] = cleaned
        save_credentials(current, directory)
        return credential_status(directory)


def credential_status(directory: str | os.PathLike[str] | None = None) -> dict[str, bool]:
    credentials = load_credentials(directory)
    return {name: bool(credentials.get(name)) for name in sorted(ALLOWED_CREDENTIALS)}


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Manage this host's encrypted MyHarness credentials")
    parser.add_argument("command", choices=("status", "update"))
    parser.add_argument("--directory", default=None)
    args = parser.parse_args()
    try:
        if args.command == "status":
            result = credential_status(args.directory)
        else:
            payload = json.load(sys.stdin)
            result = update_credentials(
                payload.get("set") if isinstance(payload, dict) else None,
                payload.get("remove") if isinstance(payload, dict) else None,
                args.directory,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CredentialStoreError, ValueError, json.JSONDecodeError) as exc:
        print(f"Credential update failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
