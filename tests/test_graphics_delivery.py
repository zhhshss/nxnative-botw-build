"""Original graphics ZIP compatibility, authentication and atomic replacement."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts import fetch_ipa, shader_cache


class GraphicsDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.password = "test-only-graphics-password"
        self.required = {"schema": 1, "series": "1-test", "minimum_version": 1,
                         "compatibility": {"game": "01007EF00011E000", "build": "v1114112", "platform": "ios",
                                           "api": "nvn", "chain": "ca8b9c5b4e34", "library": "b-ca8b9c5b4e34", "contract": "test"}}
        self.files = {"msl/b-ca8b9c5b4e34/one.metallib": b"MTLB synthetic library",
                      "msl/b-ca8b9c5b4e34/inputs.tsv": b"synthetic stamp",
                      "tables/shader_map.tsv": b"synthetic shader map"}
        self.package = self.root / "input.zip"

    def package_bytes(self, extra=None):
        compilers = {"metal_versions": ["test compiler"], "minimum_ios": "26.0"}
        compiler_id = hashlib.sha256(json.dumps(compilers, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        entries = [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(self.files.items())]
        manifest = {"schema": 1, "kind": "graphics", "series": self.required["series"], "version": 1,
                    "compatibility": self.required["compatibility"], "compilers": compilers, "compiler_id": compiler_id, "files": entries}
        identity = f"nxnative-graphics-v1\n{manifest['series']}\n1\n{compiler_id}\n"
        identity += "".join(f"{r['path']}\t{r['size']}\t{r['sha256']}\n" for r in entries)
        manifest["id"] = hashlib.sha256(identity.encode()).hexdigest()
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for name, payload in self.files.items(): archive.writestr(name, payload)
            if extra: archive.writestr(*extra)
        return data.getvalue()

    def encrypted(self, payload, kind="graphics"):
        self.package.write_bytes(payload)
        expected = hashlib.sha256(payload).hexdigest()
        output = self.root / "input.enc"
        shader_cache.encrypt_archive(self.package, output, expected, self.password.encode(), kind=kind)
        return output.read_bytes(), expected

    def test_original_graphics_zip_authenticated_roundtrip(self):
        payload = self.package_bytes()
        encrypted, expected = self.encrypted(payload)
        output = self.root / fetch_ipa.GRAPHICS_NAME
        fetch_ipa.extract_graphics_stream(io.BytesIO(encrypted), output, self.password, expected, self.required)
        self.assertEqual(output.read_bytes(), payload)
        self.assertEqual(fetch_ipa.validate_graphics_package(output, self.required)["version"], 1)

    def test_corruption_wrong_kind_and_unlisted_entries_preserve_old_graphics(self):
        output = self.root / fetch_ipa.GRAPHICS_NAME
        previous = self.package_bytes(); output.write_bytes(previous)
        encrypted, expected = self.encrypted(previous)
        corrupt = bytearray(encrypted); corrupt[-1] ^= 1
        wrong_kind, _ = self.encrypted(previous, "ipa")
        unsafe, unsafe_hash = self.encrypted(self.package_bytes(extra=("../outside", b"unsafe")))
        for data, digest in [(bytes(corrupt), expected), (wrong_kind, expected), (unsafe, unsafe_hash)]:
            with self.subTest(), self.assertRaises(ValueError):
                fetch_ipa.extract_graphics_stream(io.BytesIO(data), output, self.password, digest, self.required)
            self.assertEqual(output.read_bytes(), previous)
            self.assertFalse(output.with_name(output.name + ".partial").exists())

    def test_incompatible_series_and_minimum_version_are_rejected(self):
        self.package.write_bytes(self.package_bytes())
        for field, value in [("series", "different-series"), ("minimum_version", 2)]:
            required = dict(self.required); required[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                fetch_ipa.validate_graphics_package(self.package, required)

    def test_existing_compatible_graphics_and_app_reuse_app_only_publication(self):
        complete = self.root / fetch_ipa.NAME
        original_complete = b"preserved original complete IPA"
        complete.write_bytes(original_complete)
        app = self.root / fetch_ipa.APP_NAME
        with zipfile.ZipFile(app, "w") as archive:
            archive.writestr("Payload/DmIOS.app/Info.plist", b"plist")
            archive.writestr("Payload/DmIOS.app/DmIOS", b"executable")
            archive.writestr("Payload/DmIOS.app/dm/graphics-required.json", json.dumps(self.required))
        graphics_payload = self.package_bytes()
        graphics = self.root / fetch_ipa.GRAPHICS_NAME; graphics.write_bytes(graphics_payload)
        expected = hashlib.sha256(app.read_bytes()).hexdigest()
        run = {"status": "completed", "conclusion": "success", "headSha": "source", "url": "https://example/run",
               "jobs": [{"name": "ipa / ipa", "conclusion": "success", "databaseId": 1}]}
        artifacts = {"artifacts": [{"name": "botw-ipa-encrypted", "expired": False, "id": 2, "size_in_bytes": 1000}]}
        arguments = ["fetch_ipa.py", "1", "--output-dir", str(self.root), "--password-file", str(self.root / "unused"), "--with-graphics"]
        for marker in ("graphics package:", "graphics ZIP:"):
            # The current NSP wrapper emits graphics ZIP, as recorded by the real cold build.
            log = (f"[19:42:32] {marker} /out/BOTW-iPad-Touch.graphics.v1.zip (Import shaders in the launcher)\n"
                   f"{expected}  /out/{fetch_ipa.NAME}\n")
            with self.subTest(marker=marker), mock.patch.object(sys, "argv", arguments), \
                    mock.patch.object(fetch_ipa, "gh_json", side_effect=[run, artifacts]), \
                    mock.patch.object(fetch_ipa.subprocess, "check_output", return_value=log), \
                    mock.patch.object(fetch_ipa, "artifact_download_url", side_effect=AssertionError("no repeated download")):
                fetch_ipa.main()
                self.assertEqual(graphics.read_bytes(), graphics_payload)
                self.assertEqual(complete.read_bytes(), original_complete)


if __name__ == "__main__":
    unittest.main()
