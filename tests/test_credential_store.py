import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.agent import credential_store


class CredentialStoreTests(unittest.TestCase):
    def test_round_trip_uses_encrypted_versioned_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            credential_store.save_credentials({credential_store.NATIVE_API_KEY: "sk-native"}, tmp)
            encrypted, key = credential_store.credential_paths(tmp)

            envelope = json.loads(encrypted.read_text(encoding="utf-8"))
            self.assertEqual(envelope["version"], 1)
            self.assertEqual(envelope["cipher"], "AES-256-GCM")
            self.assertNotIn("sk-native", encrypted.read_text(encoding="utf-8"))
            self.assertEqual(
                credential_store.load_credentials(tmp),
                {credential_store.NATIVE_API_KEY: "sk-native"},
            )
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(encrypted.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(Path(tmp).stat().st_mode), 0o700)

    def test_update_preserves_blank_values_and_removes_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            credential_store.update_credentials({
                credential_store.NATIVE_API_KEY: "native-one",
                credential_store.STT_API_KEY: "stt-one",
            }, directory=tmp)
            credential_store.update_credentials(
                {credential_store.NATIVE_API_KEY: ""},
                [credential_store.STT_API_KEY],
                tmp,
            )
            self.assertEqual(
                credential_store.load_credentials(tmp),
                {credential_store.NATIVE_API_KEY: "native-one"},
            )

    def test_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            credential_store.save_credentials({credential_store.STT_API_KEY: "stt"}, tmp)
            encrypted, _key = credential_store.credential_paths(tmp)
            envelope = json.loads(encrypted.read_text(encoding="utf-8"))
            envelope["ciphertext"] = envelope["ciphertext"][:-2] + "AA"
            encrypted.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaises(credential_store.CredentialStoreError):
                credential_store.load_credentials(tmp)

    def test_cli_reads_values_from_stdin_and_never_echoes_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = "sk-never-print-this"
            result = subprocess.run(
                [sys.executable, str(Path(credential_store.__file__)), "update", "--directory", tmp],
                input=json.dumps({"set": {credential_store.NATIVE_API_KEY: secret}}),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(secret, result.stdout)
            self.assertNotIn(secret, result.stderr)
            self.assertTrue(json.loads(result.stdout)[credential_store.NATIVE_API_KEY])


if __name__ == "__main__":
    unittest.main()
