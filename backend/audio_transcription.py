from __future__ import annotations

import base64
import binascii
import json
import os
import posixpath
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests
from fastapi import HTTPException

_AUDIO_MIME_EXT = {
    "audio/webm": ".webm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "application/octet-stream": ".bin",
}

_MODEL_CACHE = {}


@dataclass(frozen=True)
class AudioConfig:
    enabled: bool
    processor: str
    server: str
    app_dir: str
    model: str
    timeout_seconds: int
    max_upload_mb: int
    api_base_url: str = ""
    api_key: str = ""
    username: str = ""
    key_file: str = ""
    language: str = ""
    device: str = "cpu"


def config_from_utils(utils_module) -> AudioConfig:
    key_getter = getattr(utils_module, "get_stt_api_key", None)
    api_key = key_getter() if callable(key_getter) else getattr(utils_module, "AUDIO_TRANSCRIPTION_API_KEY", "")
    return AudioConfig(
        enabled=bool(getattr(utils_module, "AUDIO_ENABLED", False)),
        processor=str(getattr(utils_module, "AUDIO_TRANSCRIPTION_PROCESSOR", "local") or "local").lower(),
        server=str(getattr(utils_module, "AUDIO_TRANSCRIPTION_SERVER", "") or ""),
        app_dir=str(getattr(utils_module, "AUDIO_TRANSCRIPTION_APP_DIR", "/opt/apps/whisperAudio") or "/opt/apps/whisperAudio"),
        model=str(getattr(utils_module, "AUDIO_TRANSCRIPTION_MODEL", "small") or "small"),
        timeout_seconds=int(getattr(utils_module, "AUDIO_TRANSCRIPTION_TIMEOUT", 1800) or 1800),
        max_upload_mb=int(getattr(utils_module, "AUDIO_MAX_UPLOAD_MB", 500) or 500),
        api_base_url=str(getattr(utils_module, "AUDIO_TRANSCRIPTION_API_BASE_URL", "") or ""),
        api_key=str(api_key or ""),
        username=str(getattr(utils_module, "AUDIO_TRANSCRIPTION_USERNAME", "") or ""),
        key_file=str(getattr(utils_module, "AUDIO_TRANSCRIPTION_KEY_FILE", "") or ""),
        language=str(getattr(utils_module, "AUDIO_TRANSCRIPTION_LANGUAGE", "") or ""),
        device=str(getattr(utils_module, "AUDIO_TRANSCRIPTION_DEVICE", "cpu") or "cpu").lower(),
    )


def public_config(config: AudioConfig) -> dict:
    return {
        "enabled": config.enabled,
        "processor": config.processor,
        "server": config.server,
        "model": config.model,
        "language": config.language,
        "device": config.device,
        "max_upload_mb": config.max_upload_mb,
    }


def decode_audio_payload(data: str, mime: str, max_upload_mb: int) -> tuple[bytes, str]:
    resolved_mime = mime or "audio/webm"
    payload = data or ""
    if payload.startswith("data:"):
        header, sep, body = payload.partition(",")
        if not sep or ";base64" not in header:
            raise HTTPException(status_code=400, detail="Audio must be a base64 data URL")
        resolved_mime = header[5:].split(";", 1)[0] or resolved_mime
        payload = body

    if resolved_mime not in _AUDIO_MIME_EXT:
        raise HTTPException(status_code=415, detail=f"Unsupported audio type: {resolved_mime}")

    try:
        raw = base64.b64decode(payload, validate=True)
    except binascii.Error:
        raise HTTPException(status_code=400, detail="Invalid base64 audio data")

    max_bytes = max(1, int(max_upload_mb)) * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Audio is larger than {max_upload_mb} MB")
    if not raw:
        raise HTTPException(status_code=400, detail="Audio payload is empty")
    return raw, resolved_mime


def _safe_id(value: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._-")
    return cleaned[:96] or fallback


def make_voice_turn_id() -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"voice_{stamp}_{uuid.uuid4().hex[:6]}"


def local_audio_path(data_dir: str, session_id: str, voice_turn_id: str, mime: str) -> Path:
    ext = _AUDIO_MIME_EXT.get(mime, ".bin")
    path = Path(data_dir) / "audio" / _safe_id(session_id, "session") / _safe_id(voice_turn_id, "voice") / f"input{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def transcribe_audio(
    *,
    data_dir: str,
    session_id: str,
    audio_data: str,
    mime: str,
    name: str = "recording.webm",
    config: AudioConfig,
) -> dict:
    if not config.enabled:
        raise HTTPException(status_code=400, detail="Audio transcription is disabled in config")
    if config.processor not in {"local", "remote", "api"}:
        raise HTTPException(status_code=400, detail=f"Unsupported audio processor: {config.processor}")

    raw, resolved_mime = decode_audio_payload(audio_data, mime, config.max_upload_mb)
    voice_turn_id = make_voice_turn_id()
    input_path = local_audio_path(data_dir, session_id, voice_turn_id, resolved_mime)
    input_path.write_bytes(raw)

    if config.processor == "remote":
        result = _transcribe_remote(input_path, session_id, voice_turn_id, config)
    elif config.processor == "api":
        result = _transcribe_api(input_path, name, resolved_mime, config)
    else:
        result = _transcribe_local(input_path, config)

    return {
        "text": result["text"],
        "language": result.get("language", ""),
        "language_probability": result.get("language_probability"),
        "duration": result.get("duration"),
        "processor": config.processor,
        "model": config.model,
        "session_id": session_id,
        "voice_turn_id": voice_turn_id,
        "filename": name,
    }


def _transcribe_local(input_path: Path, config: AudioConfig) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"faster-whisper is not installed on the backend: {exc}")

    device = config.device if config.device in {"cpu", "cuda", "auto"} else "cpu"
    if device == "auto":
        device = "cuda"
    compute_type = "float16" if device == "cuda" else "int8"
    key = (config.model, device, compute_type)
    model = _MODEL_CACHE.get(key)
    if model is None:
        try:
            model = WhisperModel(config.model, device=device, compute_type=compute_type)
        except Exception:
            if config.device != "auto":
                raise
            device, compute_type = "cpu", "int8"
            key = (config.model, device, compute_type)
            model = _MODEL_CACHE.get(key) or WhisperModel(config.model, device=device, compute_type=compute_type)
        _MODEL_CACHE[key] = model

    try:
        kwargs = {"beam_size": 5}
        if config.language:
            kwargs["language"] = config.language
        segments, info = model.transcribe(str(input_path), **kwargs)
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Local transcription failed: {exc}")

    if not text:
        raise HTTPException(status_code=422, detail="No speech detected")
    return {
        "text": text,
        "language": getattr(info, "language", ""),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
    }


def _transcribe_api(input_path: Path, name: str, mime: str, config: AudioConfig) -> dict:
    """Transcribe via an OpenAI-compatible /audio/transcriptions endpoint."""
    base_url = (config.api_base_url or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=500,
            detail="audio.transcription.api_base_url is not set for the api processor",
        )
    if not config.api_key:
        raise HTTPException(
            status_code=500,
            detail="No STT API key configured (use Electron Settings or MYHARNESS_STT_API_KEY)",
        )

    url = f"{base_url}/audio/transcriptions"
    filename = name or input_path.name
    try:
        with input_path.open("rb") as audio_file:
            form = {"model": config.model}
            if config.language:
                form["language"] = config.language
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {config.api_key}"},
                files={"file": (filename, audio_file, mime or "application/octet-stream")},
                data=form,
                timeout=max(1, min(config.timeout_seconds, 24 * 60 * 60)),
            )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"API transcription request failed: {exc}")

    if response.status_code >= 400:
        detail = (getattr(response, "text", "") or "").strip()
        raise HTTPException(
            status_code=502,
            detail=f"API transcription failed ({response.status_code}): {detail[:500] or 'no response body'}",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"API transcription returned invalid JSON: {exc}")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="API transcription returned an unexpected payload")
    if payload.get("error"):
        raise HTTPException(status_code=502, detail=f"API transcription failed: {payload['error']}")

    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="No speech detected")
    return {
        "text": text,
        "language": str(payload.get("language") or ""),
        "language_probability": payload.get("language_probability"),
        "duration": payload.get("duration"),
    }


def _resolve_server(config: AudioConfig) -> dict:
    hostname = config.server.strip()
    if not hostname:
        raise HTTPException(status_code=500, detail="audio.transcription.server is required for remote transcription")
    key_file = config.key_file.strip()
    if key_file and os.path.exists(os.path.expanduser(key_file)):
        key_file = os.path.expanduser(key_file)
    else:
        default_key = os.path.expanduser("~/.ssh/id_rsa")
        key_file = default_key if os.path.exists(default_key) else ""

    return {
        "hostname": hostname,
        "name": hostname,
        "username": config.username or None,
        "key_file": key_file,
        "timeout": min(max(1, config.timeout_seconds), 60),
    }


def _remote_paths(app_dir: str, session_id: str, voice_turn_id: str, input_path: Path) -> dict:
    base = app_dir.rstrip("/") or "/opt/apps/whisperAudio"
    turn_dir = posixpath.join(
        base,
        "myharness",
        _safe_id(session_id, "session"),
        _safe_id(voice_turn_id, "voice"),
    )
    ext = input_path.suffix or ".bin"
    return {
        "turn_dir": turn_dir,
        "input": posixpath.join(turn_dir, f"input{ext}"),
        "script": posixpath.join(turn_dir, "transcribe.py"),
        "output": posixpath.join(turn_dir, "transcript.json"),
    }


def _transcribe_remote(input_path: Path, session_id: str, voice_turn_id: str, config: AudioConfig) -> dict:
    try:
        import paramiko
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"paramiko is required for remote audio transcription: {exc}")

    server = _resolve_server(config)
    paths = _remote_paths(config.app_dir, session_id, voice_turn_id, input_path)
    python_path = posixpath.join(config.app_dir.rstrip("/"), ".venv", "bin", "python")
    script = _remote_transcribe_script()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect_kwargs = {
            "hostname": server["hostname"],
            "timeout": server["timeout"],
        }
        if server["username"]:
            connect_kwargs["username"] = server["username"]
        if server["key_file"]:
            connect_kwargs["key_filename"] = server["key_file"]
        ssh.connect(**connect_kwargs)

        _remote_exec(ssh, f"mkdir -p {shlex.quote(paths['turn_dir'])}", timeout=server["timeout"])
        sftp = ssh.open_sftp()
        try:
            with sftp.open(paths["script"], "wb") as remote_script:
                remote_script.write(script.encode("utf-8"))
            sftp.put(str(input_path), paths["input"])
        finally:
            sftp.close()

        command = " ".join([
            shlex.quote(python_path),
            shlex.quote(paths["script"]),
            "--input",
            shlex.quote(paths["input"]),
            "--output",
            shlex.quote(paths["output"]),
            "--model",
            shlex.quote(config.model),
            "--device",
            shlex.quote(config.device),
        ])
        if config.language:
            command += f" --language {shlex.quote(config.language)}"
        _remote_exec(ssh, command, timeout=max(1, min(config.timeout_seconds, 24 * 60 * 60)))

        sftp = ssh.open_sftp()
        try:
            with sftp.open(paths["output"], "r") as output_file:
                raw_result = output_file.read().decode("utf-8")
        finally:
            sftp.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Remote transcription failed: {exc}")
    finally:
        ssh.close()

    try:
        result = json.loads(raw_result)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Remote transcription returned invalid JSON: {exc}")
    if result.get("error"):
        raise HTTPException(status_code=500, detail=f"Remote transcription failed: {result['error']}")
    text = str(result.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="No speech detected")
    return result


def _remote_exec(ssh, command: str, timeout: int) -> str:
    _stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    status = stdout.channel.recv_exit_status()
    if status != 0:
        raise HTTPException(status_code=500, detail=f"Remote command failed ({status}): {err or out}")
    return out


def _remote_transcribe_script() -> str:
    return """#!/usr/bin/env python3
import argparse
import json

from faster_whisper import WhisperModel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--language", default="")
    args = parser.parse_args()

    try:
        device = "cuda" if args.device == "auto" else args.device
        compute_type = "float16" if device == "cuda" else "int8"
        try:
            model = WhisperModel(args.model, device=device, compute_type=compute_type)
        except Exception:
            if args.device != "auto":
                raise
            model = WhisperModel(args.model, device="cpu", compute_type="int8")
        kwargs = {"beam_size": 5}
        if args.language:
            kwargs["language"] = args.language
        segments, info = model.transcribe(args.input, **kwargs)
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
        payload = {
            "text": text,
            "language": getattr(info, "language", ""),
            "language_probability": getattr(info, "language_probability", None),
            "duration": getattr(info, "duration", None),
        }
    except Exception as exc:
        payload = {"error": str(exc)}

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
"""
