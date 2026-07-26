import base64
import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend import audio_transcription
from backend import web_app
from backend.session_store import SessionStore


class AudioTranscriptionTests(unittest.TestCase):
    def test_decode_audio_payload_accepts_data_url_and_enforces_mime(self):
        encoded = base64.b64encode(b"audio-bytes").decode("ascii")
        raw, mime = audio_transcription.decode_audio_payload(
            f"data:audio/webm;base64,{encoded}",
            "audio/webm",
            max_upload_mb=1,
        )
        self.assertEqual(raw, b"audio-bytes")
        self.assertEqual(mime, "audio/webm")

        with self.assertRaises(HTTPException) as unsupported:
            audio_transcription.decode_audio_payload(
                f"data:text/plain;base64,{encoded}",
                "text/plain",
                max_upload_mb=1,
            )
        self.assertEqual(unsupported.exception.status_code, 415)

    def test_local_audio_path_is_session_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = audio_transcription.local_audio_path(
                tmp,
                "ses_20260702_test",
                "voice_20260702_120000_ab12",
                "audio/webm",
            )
            self.assertEqual(path.name, "input.webm")
            self.assertIn("ses_20260702_test", path.parts)
            self.assertIn("voice_20260702_120000_ab12", path.parts)
            self.assertTrue(path.parent.exists())

    def test_remote_transcription_uses_app_dir_venv_python(self):
        script_source = audio_transcription._transcribe_remote.__code__.co_consts
        self.assertIn(".venv", script_source)
        self.assertIn("python", script_source)

    def test_endpoint_validates_session_and_dispatches_transcription(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(str(Path(tmp) / "data"))
            store.ensure_project("proj", "Project", tmp)
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "Session")

            config = audio_transcription.AudioConfig(
                enabled=True,
                processor="local",
                server="",
                app_dir="/opt/apps/whisperAudio",
                model="small",
                timeout_seconds=1800,
                max_upload_mb=500,
            )
            result = {
                "text": "draft prompt",
                "language": "en",
                "processor": "local",
                "model": "small",
                "session_id": meta.id,
                "voice_turn_id": "voice_test",
                "filename": "recording.webm",
            }

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app, "_DATA_DIR", str(Path(tmp) / "data")),
                patch.object(web_app.audio_transcription, "config_from_utils", return_value=config),
                patch.object(web_app.audio_transcription, "transcribe_audio", return_value=result) as transcribe,
            ):
                response = web_app.transcribe_audio(
                    web_app.AudioTranscriptionRequest(
                        session_id=meta.id,
                        data="data:audio/webm;base64,YQ==",
                        mime="audio/webm",
                        name="recording.webm",
                    )
                )

            self.assertEqual(response["text"], "draft prompt")
            transcribe.assert_called_once()

    def _api_config(self, **overrides):
        base = dict(
            enabled=True,
            processor="api",
            server="",
            app_dir="/opt/apps/whisperAudio",
            model="whisper-1",
            timeout_seconds=1800,
            max_upload_mb=500,
            api_base_url="https://api.example.com/v1",
            api_key="sk-test",
        )
        base.update(overrides)
        return audio_transcription.AudioConfig(**base)

    def test_api_processor_posts_multipart_and_parses_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._api_config()
            response = SimpleNamespace(
                status_code=200,
                text='{"text": "hello there"}',
                json=lambda: {"text": "hello there", "language": "en", "duration": 1.5},
            )

            with patch.object(audio_transcription.requests, "post", return_value=response) as post:
                result = audio_transcription.transcribe_audio(
                    data_dir=str(Path(tmp) / "data"),
                    session_id="ses_api",
                    audio_data="data:audio/webm;base64,YQ==",
                    mime="audio/webm",
                    name="recording.webm",
                    config=config,
                )

            self.assertEqual(result["text"], "hello there")
            self.assertEqual(result["processor"], "api")
            self.assertEqual(result["model"], "whisper-1")

            post.assert_called_once()
            url = post.call_args.args[0]
            kwargs = post.call_args.kwargs
            self.assertEqual(url, "https://api.example.com/v1/audio/transcriptions")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test")
            self.assertEqual(kwargs["data"]["model"], "whisper-1")
            self.assertEqual(kwargs["files"]["file"][0], "recording.webm")

    def test_api_processor_surfaces_http_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            response = SimpleNamespace(status_code=401, text="invalid api key", json=lambda: {})
            with patch.object(audio_transcription.requests, "post", return_value=response):
                with self.assertRaises(HTTPException) as failure:
                    audio_transcription.transcribe_audio(
                        data_dir=str(Path(tmp) / "data"),
                        session_id="ses_api",
                        audio_data="data:audio/webm;base64,YQ==",
                        mime="audio/webm",
                        config=self._api_config(),
                    )
        self.assertEqual(failure.exception.status_code, 502)
        self.assertIn("invalid api key", failure.exception.detail)

    def test_api_processor_requires_base_url_and_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            for overrides in ({"api_base_url": ""}, {"api_key": ""}):
                with patch.object(audio_transcription.requests, "post") as post:
                    with self.assertRaises(HTTPException) as missing:
                        audio_transcription.transcribe_audio(
                            data_dir=str(Path(tmp) / "data"),
                            session_id="ses_api",
                            audio_data="data:audio/webm;base64,YQ==",
                            mime="audio/webm",
                            config=self._api_config(**overrides),
                        )
                self.assertEqual(missing.exception.status_code, 500)
                post.assert_not_called()

    def test_config_from_utils_reads_api_keys(self):
        stub = SimpleNamespace(
            AUDIO_ENABLED=True,
            AUDIO_TRANSCRIPTION_PROCESSOR="api",
            AUDIO_TRANSCRIPTION_API_BASE_URL="https://api.example.com/v1",
            AUDIO_TRANSCRIPTION_API_KEY="sk-from-utils",
            AUDIO_TRANSCRIPTION_MODEL="whisper-1",
        )
        config = audio_transcription.config_from_utils(stub)
        self.assertEqual(config.processor, "api")
        self.assertEqual(config.api_base_url, "https://api.example.com/v1")
        self.assertEqual(config.api_key, "sk-from-utils")
        self.assertEqual(config.model, "whisper-1")

    def test_endpoint_rejects_missing_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(str(Path(tmp) / "data"))
            with patch.object(web_app, "_store", store):
                with self.assertRaises(HTTPException) as missing:
                    web_app.transcribe_audio(
                        web_app.AudioTranscriptionRequest(
                            session_id="missing",
                            data="data:audio/webm;base64,YQ==",
                        )
                    )
        self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
