import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

from cryptography.exceptions import InvalidTag
from scripts import fetch_ipa, shader_cache, serve_owner_files


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

    def extract(self, output, password, expected):
        producer = subprocess.Popen(["cat", str(self.outer)], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
        try:
            fetch_ipa.extract_stream(producer.stdout, output, password, self.name, expected)
        finally:
            producer.stdout.close()
            try:
                producer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                producer.kill(); producer.wait()

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
        producer = subprocess.Popen(["cat", str(artifact)], stdout=subprocess.PIPE)
        try:
            fetch_ipa.extract_aes_stream(producer.stdout, output, self.password, self.name, self.expected)
            self.assertEqual(output.read_bytes(), self.ipa.read_bytes())
        finally:
            producer.stdout.close(); producer.wait(timeout=5)

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
