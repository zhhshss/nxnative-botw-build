"""Independent authenticated data pickup and reuse without another download."""
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts import fetch_ipa, shader_cache


class GameDataDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        table = {"directories": ["romfs", "romfs/Pack", "aoc"], "files": [
            {"path": "romfs/Pack/Bootup_Graphics.pack", "size": 4}, {"path": "aoc/1.img", "size": 3}]}
        encoded = json.dumps(table, separators=(",", ":")).encode()
        self.contents = b"NXROM001" + struct.pack("<Q", len(encoded)) + encoded + b"gamedlc"
        self.descriptor = {"format": 1, "title_id": "01007EF00011E000", "version": "1.9.0", "marker": "Pack/Bootup_Graphics.pack",
                           "size": len(self.contents), "sha256": hashlib.sha256(self.contents).hexdigest()}
        self.package = self.root / "input.nxrom"; self.package.write_bytes(self.contents)
        self.password = "test-only-data-password"

    def encrypted(self, kind="game-data"):
        encrypted = self.root / "data.enc"
        shader_cache.encrypt_archive(self.package, encrypted, self.descriptor["sha256"], self.password.encode(), kind=kind)
        return encrypted.read_bytes()

    def test_independent_data_artifact_roundtrip_checks_the_original_index(self):
        output = self.root / "verified.nxrom"
        fetch_ipa.extract_game_data_stream(io.BytesIO(self.encrypted()), output, self.password, self.descriptor)
        self.assertEqual(output.read_bytes(), self.contents)

    def test_wrong_kind_corruption_and_invalid_index_never_publish(self):
        output = self.root / "rejected.nxrom"
        bad = bytearray(self.encrypted()); bad[-17] ^= 1
        for data in (bytes(bad), self.encrypted("ipa")):
            with self.assertRaises(ValueError):
                fetch_ipa.extract_game_data_stream(io.BytesIO(data), output, self.password, self.descriptor)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_name(output.name + ".partial").exists())
        table = {"directories": ["romfs"], "files": [{"path": "romfs/../outside", "size": 1}]}
        encoded = json.dumps(table).encode()
        data = b"NXROM001" + struct.pack("<Q", len(encoded)) + encoded + b"x"
        self.package.write_bytes(data)
        self.descriptor.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        with self.assertRaises(ValueError):
            fetch_ipa.extract_game_data_stream(io.BytesIO(self.encrypted()), output, self.password, self.descriptor)
        self.assertFalse(output.exists())

    def test_existing_matching_data_and_app_do_not_request_any_download(self):
        output = self.root / fetch_ipa.APP_NAME
        with zipfile.ZipFile(output, "w") as app:
            app.writestr("Payload/DmIOS.app/Info.plist", b"app")
            app.writestr("Payload/DmIOS.app/DmIOS", b"executable")
            app.writestr("Payload/DmIOS.app/dm/game-data.json", json.dumps(self.descriptor))
        data = self.root / "BOTW-1.9.0-DLC.nxrom"; data.write_bytes(self.contents)
        app_hash = hashlib.sha256(output.read_bytes()).hexdigest()
        log = (f"external game data: /out/{fetch_ipa.DATA_NAME}\n"
               f"{app_hash}  /out/{fetch_ipa.NAME}\n"
               f"{self.descriptor['sha256']}  /out/{fetch_ipa.DATA_NAME}\n")
        run = {"status": "completed", "conclusion": "success", "headSha": "source", "url": "https://example/run",
               "jobs": [{"name": "ipa / ipa", "conclusion": "success", "databaseId": 1}]}
        artifacts = {"artifacts": [{"name": "botw-ipa-encrypted", "expired": False, "id": 2, "size_in_bytes": 1000},
                                  {"name": "botw-game-data-encrypted", "expired": False, "id": 3, "size_in_bytes": 1000}]}
        arguments = ["fetch_ipa.py", "1", "--output-dir", str(self.root), "--password-file", str(self.root / "unused-password"), "--with-game-data"]
        with mock.patch.object(sys, "argv", arguments), mock.patch.object(fetch_ipa, "gh_json", side_effect=[run, artifacts]), \
                mock.patch.object(fetch_ipa.subprocess, "check_output", return_value=log), \
                mock.patch.object(fetch_ipa, "artifact_download_url", side_effect=AssertionError("no repeated download")):
            fetch_ipa.main()
        self.assertEqual(data.read_bytes(), self.contents)
