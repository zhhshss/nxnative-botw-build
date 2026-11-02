import hashlib
import io
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
import zipfile

from cryptography.exceptions import InvalidTag
from scripts import artifact_zip, fetch_ipa, shader_cache, serve_owner_files


class ForwardOnly(io.BytesIO):
    def seek(self, *args):
        raise io.UnsupportedOperation("forward-only ZIP writer")


class OwnerFileServerTests(unittest.TestCase):
    def test_ranges_head_and_wrong_token_over_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "app.ipa").write_bytes(b"0123456789")
            (root / "app.ipa.partial").write_bytes(b"not yet verified")
            server = serve_owner_files.create_server(root, "127.0.0.1", 0, "test-token")
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            url = f"http://127.0.0.1:{server.server_port}/test-token/app.ipa"
            try:
                for header, expected in (("bytes=0-3", b"0123"), ("bytes=-3", b"789"),
                                         ("bytes=8-99", b"89"), ("bytes=3-", b"3456789")):
                    with urlopen(Request(url, headers={"Range": header}), timeout=5) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.read(), expected)
                with urlopen(Request(url, method="HEAD"), timeout=5) as response:
                    self.assertEqual(response.headers["Content-Length"], "10")
                    self.assertEqual(response.read(), b"")
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(url, headers={"Range": "bytes=10-"}), timeout=5)
                self.assertEqual(error.exception.code, 416)
                with self.assertRaises(HTTPError) as error:
                    urlopen(url.replace("test-token", "wrong-token"), timeout=5)
                self.assertEqual(error.exception.code, 404)
                with self.assertRaises(HTTPError) as error:
                    urlopen(url + ".partial", timeout=5)
                self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown(); server.server_close(); thread.join()


class ShaderCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"
        lib = self.work / "shader/ios/b-test"
        lib.mkdir(parents=True)
        (lib / "inputs.tsv").write_text("game\t01007EF00011E000\ninputs\ttest\n")
        (lib / "one_b.metallib").write_bytes(b"compiled shader" * 500)
        lib.with_name(lib.name + ".key").write_text("native-build-fingerprint\n")
        pso = self.work / "ipa/build/ios/pso"
        pso.mkdir(parents=True)
        (pso / "key.applegpu_g18p").write_text("pipeline-key\n")
        (self.work / "prod.keys").write_text("never cache this")
        (self.work / "shader/prod.keys").write_text("never cache misplaced keys either")
        (self.work / "base.nsp").write_bytes(b"private game container")
        self.cache = self.root / "encrypted.bin"
        self.key, self.password = "a" * 64, b"test-only-cache-password"

    def test_roundtrip_keeps_generated_outputs_and_excludes_keys_and_containers(self):
        self.assertTrue(shader_cache.save(self.work, self.cache, self.key, self.password))
        restored = self.root / "restored"
        shader_cache.restore(self.cache, restored, self.key, self.password)
        self.assertEqual((restored / "shader/ios/b-test/one_b.metallib").read_bytes(), b"compiled shader" * 500)
        self.assertEqual((restored / "ipa/build/ios/pso/key.applegpu_g18p").read_text(), "pipeline-key\n")
        self.assertFalse((restored / "prod.keys").exists())
        self.assertFalse((restored / "shader/prod.keys").exists())
        self.assertFalse((restored / "base.nsp").exists())

    def test_wrong_password_and_tampering_never_restore_outputs(self):
        shader_cache.save(self.work, self.cache, self.key, self.password)
        for wrong_password in (True, False):
            if not wrong_password:
                data = bytearray(self.cache.read_bytes()); data[-18] ^= 1
                self.cache.write_bytes(data)
            target = self.root / "rejected"
            with self.assertRaises(InvalidTag):
                shader_cache.restore(self.cache, target, self.key,
                                     b"wrong" if wrong_password else self.password)
            self.assertFalse(target.exists())

    def test_authenticated_archive_cannot_write_outside_cache_roots(self):
        archive = self.root / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            member = tarfile.TarInfo("../escape"); member.size = 3
            tar.addfile(member, io.BytesIO(b"bad"))
        shader_cache.encrypt_archive(archive, self.cache, self.key, self.password)
        with self.assertRaises(ValueError):
            shader_cache.restore(self.cache, self.root / "rejected", self.key, self.password)
        self.assertFalse((self.root / "escape").exists())

    def test_cache_key_tracks_content_compilers_gpu_and_password_but_ignores_checkout_time(self):
        source, containers = self.root / "source", self.root / "containers"
        tool = source / "dm/gfx/tools/compiler.py"
        tool.parent.mkdir(parents=True); tool.write_text("compiler v1")
        containers.mkdir(); (containers / "base.nsp").write_bytes(b"game v1")
        key = lambda gpu="g18p", versions=["metal v1"], password=b"test": shader_cache.cache_key(
            source, containers, "", gpu, versions, password)
        first = key()
        tool.touch()
        (source / "workflow.yml").write_text("an unrelated CI fix")
        self.assertEqual(first, key())
        self.assertNotEqual(first, key(gpu="g17p"))
        self.assertNotEqual(first, key(versions=["metal v2"]))
        self.assertNotEqual(first, key(password=b"rotated-password"))
        (containers / "base.nsp").write_bytes(b"game v2")
        self.assertNotEqual(first, key())

    def test_compiler_mount_changes_reuse_only_an_identical_legacy_cache(self):
        source, containers = self.root / "source", self.root / "containers"
        tool = source / "dm/gfx/tools/compiler.py"
        tool.parent.mkdir(parents=True); tool.write_text("compiler v1")
        containers.mkdir(); game = containers / "base.nsp"; game.write_bytes(b"game v1")
        old_root = "/private/var/run/old/Metal.xctoolchain"
        new_root = "/private/var/run/new/Metal.xctoolchain"
        old = "metal v1\nTarget: air64\nInstalledDir: " + old_root + "/usr/metal/current/bin"
        current = old.replace(old_root, new_root)
        keys = lambda version=current: shader_cache.cache_keys(source, containers, "", "g18p", [version], self.password, old_root)
        stable, compatible = keys()
        self.assertEqual(stable, keys(old)[0])
        self.assertNotEqual(stable, compatible)
        shader_cache.save(self.work, self.cache, compatible, self.password)
        self.assertTrue(shader_cache.restore(self.cache, self.root / "restored", stable, self.password, compatible))
        changed = keys(current.replace("metal v1", "metal v2"))
        self.assertNotEqual(stable, changed[0])
        self.assertNotEqual(compatible, changed[1])
        with self.assertRaises(ValueError):
            shader_cache.restore(self.cache, self.root / "wrong-compiler", changed[0], self.password, changed[1])
        game.write_bytes(b"game v2")
        changed = keys()
        with self.assertRaises(ValueError):
            shader_cache.restore(self.cache, self.root / "wrong-game", changed[0], self.password, changed[1])


@unittest.skipUnless(shutil.which("zip") and shutil.which("bsdtar"), "requires zip and libarchive-tools")
class IPAPickupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.name, self.password = fetch_ipa.NAME, "test-only-zip-password"
        self.ipa = self.root / self.name
        with zipfile.ZipFile(self.ipa, "w") as zip:
            zip.writestr("Payload/DmIOS.app/Info.plist", b"app info")
            zip.writestr("Payload/DmIOS.app/DmIOS", b"executable")
            zip.writestr("Payload/DmIOS.app/romfs.img", b"game data" * 1000)
        self.expected = hashlib.sha256(self.ipa.read_bytes()).hexdigest()
        inner = self.root / (self.name + ".zip")
        subprocess.run(["zip", "-0", "-q", "-j", "-e", "-P", self.password,
                        str(inner), str(self.ipa)], check=True)
        self.outer = self.root / "artifact.zip"
        stream = ForwardOnly()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as zip:
            zip.writestr("SHA256.txt", b"manifest deliberately comes first\n")
            with zip.open(inner.name, "w", force_zip64=True) as entry:
                entry.write(inner.read_bytes())
        self.outer.write_bytes(stream.getvalue())
        self.server = serve_owner_files.create_server(self.root, "127.0.0.1", 0, "test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/test-token/"
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def extract(self, output, password, expected):
        with artifact_zip.RemoteZip(self.url + self.outer.name) as archive:
            with archive.open_entry(archive.entry(self.name + ".zip")) as source:
                fetch_ipa.extract_stream(source, output, password, self.name, expected)

    def test_nested_zip64_stream_produces_only_the_verified_ipa(self):
        output = self.root / "download.ipa"
        self.extract(output, self.password, self.expected)
        self.assertEqual(output.read_bytes(), self.ipa.read_bytes())
        self.assertFalse(output.with_name(output.name + ".partial").exists())

    def test_bad_password_and_sha_never_publish_partial_or_stale_content(self):
        output = self.root / "download.ipa"
        for password, digest in (("wrong", self.expected), (self.password, "0" * 64)):
            with self.assertRaises(ValueError):
                self.extract(output, password, digest)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_name(output.name + ".partial").exists())

    def encrypted_artifact(self):
        encrypted = self.root / (self.name + ".enc")
        shader_cache.encrypt_archive(self.ipa, encrypted, self.expected,
                                     self.password.encode(), kind="ipa")
        stream = ForwardOnly()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as zip:
            zip.writestr("SHA256.txt", b"manifest comes first\n")
            with zip.open(encrypted.name, "w", force_zip64=True) as entry:
                entry.write(encrypted.read_bytes())
        artifact = self.root / "aes-artifact.zip"; artifact.write_bytes(stream.getvalue())
        return artifact

    def test_authenticated_ipa_stream_from_stored_zip64(self):
        artifact = self.encrypted_artifact()
        output = self.root / "aes-download.ipa"
        with artifact_zip.RemoteZip(self.url + artifact.name) as archive:
            with archive.open_entry(archive.entry(self.name + ".enc")) as source:
                fetch_ipa.extract_aes_stream(source, output, self.password, self.name, self.expected)
            self.assertEqual(output.read_bytes(), self.ipa.read_bytes())

    def test_incorrect_outer_crc_never_publishes_an_otherwise_valid_ipa(self):
        encrypted = self.root / (self.name + ".enc")
        shader_cache.encrypt_archive(self.ipa, encrypted, self.expected, self.password.encode(), kind="ipa")
        damaged = self.root / "bad-crc.zip"
        with zipfile.ZipFile(damaged, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.write(encrypted, encrypted.name)
            archive.filelist[-1].CRC ^= 1
        output = self.root / "rejected-outer-crc.ipa"
        with artifact_zip.RemoteZip(self.url + damaged.name) as archive:
            with archive.open_entry(archive.entry(encrypted.name)) as source:
                with self.assertRaises(ValueError):
                    fetch_ipa.extract_aes_stream(source, output, self.password, self.name, self.expected)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_name(output.name + ".partial").exists())

    def test_zip64_directory_size_above_4gib_without_reading_the_payload(self):
        archive_path = self.root / "large-descriptor.zip"
        name, size = b"payload.enc", (1 << 32) + 137
        with archive_path.open("wb") as archive:
            # Actions' streamed local header gives no ZIP64 sizes. Its 64-bit
            # descriptor is ambiguous to a reader that has not read the directory.
            archive.write(struct.pack("<4s5H3I2H", b"PK\x03\x04", 20, 8, 0, 0, 0, 0, 0, 0, len(name), 0) + name)
            start = archive.tell()
            archive.seek(start + size)
            archive.write(struct.pack("<4sIQQ", b"PK\x07\x08", 0, size, size))
            directory_start = archive.tell()
            extra = struct.pack("<HHQQ", 1, 16, size, size)
            archive.write(struct.pack("<4s6H3I5H2I", b"PK\x01\x02", 45, 45, 8, 0, 0, 0, 0,
                                      0xffffffff, 0xffffffff, len(name), len(extra), 0, 0, 0, 0, 0) + name + extra)
            directory_size = archive.tell() - directory_start
            end64 = archive.tell()
            archive.write(struct.pack("<4sQ2H2I4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, 1, 1, directory_size, directory_start))
            archive.write(struct.pack("<4sIQI", b"PK\x06\x07", 0, end64, 1))
            archive.write(struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 1, 1, directory_size, 0xffffffff, 0))
        requests = []
        def record(request, **kwargs):
            requests.append(request)
            return urlopen(request, **kwargs)
        with patch.object(artifact_zip, "urlopen", side_effect=record):
            with artifact_zip.RemoteZip(self.url + archive_path.name) as archive:
                entry = archive.entry(name.decode())
        self.assertEqual((entry.start, entry.size), (start, size))
        ranges = [r.headers["Range"] for r in requests if "Range" in r.headers]
        received = sum(int(r.split("-")[1]) - int(r.split("=")[1].split("-")[0]) + 1 for r in ranges)
        self.assertLess(received, 128 * 1024, "the central directory must be read without buffering a multi-gigabyte artifact")

    def test_authenticated_ipa_rejects_wrong_password_and_keeps_existing_output(self):
        encrypted = self.root / "encrypted.bin"
        shader_cache.encrypt_archive(self.ipa, encrypted, self.expected,
                                     self.password.encode(), kind="ipa")
        output = self.root / "rejected.ipa"
        with encrypted.open("rb") as source, self.assertRaises(InvalidTag):
            shader_cache.decrypt_ipa_stream(source, output, self.expected, b"wrong")
        self.assertFalse(output.exists())
        output.write_bytes(b"an existing file")
        with encrypted.open("rb") as source, self.assertRaises(FileExistsError):
            shader_cache.decrypt_ipa_stream(source, output, self.expected, self.password.encode())
        self.assertEqual(output.read_bytes(), b"an existing file")

    def test_hash_is_bound_to_the_actual_ipa_in_the_build_log(self):
        log = f"job\tPublish\t2026-10-31T12:00:00Z {self.expected}  /runner/out/{self.name}\n"
        self.assertEqual(fetch_ipa.expected_digest(log, self.name), self.expected)
        with self.assertRaises(ValueError):
            fetch_ipa.expected_digest(log + f"{'0' * 64}  /runner/out/{self.name}\n", self.name)


if __name__ == "__main__":
    unittest.main()
