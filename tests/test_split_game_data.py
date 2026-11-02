"""Separate IPA/data delivery: code preservation, stable data identity and import."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts import split_game_data as split

CHAIN = "715bb867230c"


def executable():
    segment = struct.pack("<II16s4Q4I", 0x19, 72, b"__TEXT", 0x100000000, 4096, 0, 4096, 5, 5, 0, 0)
    header = struct.pack("<8I", 0xfeedfacf, 0x100000c, 0, 2, 1, len(segment), 0, 0)
    return (header + segment + b"dm_imported_game_ready\0dm_show_game_import\0").ljust(4096, b"\0")


def fixture(path, game=b"game image", settings=b"settings v1"):
    files = {
        "Info.plist": plistlib.dumps({"CFBundleIdentifier": split.BUNDLE_ID, "CFBundleExecutable": "DmIOS",
                                      "CFBundleShortVersionString": "1.9.0", "DMChain": CHAIN}),
        "DmIOS": executable(),
        "romfs.img": game,
        "romfs.id": b"legacy identity",
        "aoc/1.img": b"dlc",
        "dm/launcher.json": settings,
        f"msl/b-{CHAIN}/test_b.metallib": b"compiled shader",
        "pso/archive.tsv": b"pipeline metadata",
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr("Payload/DmIOS.app/" + name, value)
    return files


class AppSplitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ipa = self.root / "Full.ipa"
        self.files = fixture(self.ipa)
        self.descriptor = {"format": 1, "title_id": "01007EF00011E000", "version": "1.9.0",
                           "marker": "Pack/Bootup_Graphics.pack", "size": 10000, "sha256": "1" * 64}

    def test_app_updates_do_not_read_game_payloads_and_preserve_code(self):
        output = self.root / "App.ipa"
        original = zipfile.ZipFile.open
        def guarded(archive, name, *args, **kwargs):
            path = name.filename if isinstance(name, zipfile.ZipInfo) else name
            if str(archive.filename) == str(self.ipa):
                self.assertFalse(split.game_entry(path.removeprefix("Payload/DmIOS.app/")), "an app-only update must not read game data")
            return original(archive, name, *args, **kwargs)
        with mock.patch.object(zipfile.ZipFile, "open", guarded):
            report = split.build_app(self.ipa, output, self.descriptor)
        with zipfile.ZipFile(output) as archive:
            for name, value in self.files.items():
                if not split.game_entry(name):
                    self.assertEqual(archive.read("Payload/DmIOS.app/" + name), value)
            self.assertEqual(json.loads(archive.read("Payload/DmIOS.app/" + split.DESCRIPTOR)), self.descriptor)
            self.assertFalse(any(split.game_entry(n.removeprefix("Payload/DmIOS.app/")) for n in archive.namelist()))
        self.assertEqual(report["preserved_app_sha256"]["DmIOS"], hashlib.sha256(self.files["DmIOS"]).hexdigest())

    def test_cached_descriptor_survives_an_app_resource_change(self):
        first = split.build_app(self.ipa, self.root / "First.ipa", self.descriptor)
        fixture(self.ipa, settings=b"settings v2")
        second = split.build_app(self.ipa, self.root / "Second.ipa", self.descriptor)
        self.assertEqual(first["descriptor"], second["descriptor"])
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_rejects_wrong_game_descriptor_and_keeps_existing_output(self):
        for descriptor in (dict(self.descriptor, title_id="another game"), dict(self.descriptor, marker="../escape"), dict(self.descriptor, sha256="not a hash")):
            with self.assertRaises(ValueError):
                split.build_app(self.ipa, self.root / "Bad.ipa", descriptor)
        output = self.root / "Keep.ipa"; output.write_bytes(b"existing")
        with self.assertRaises(ValueError):
            split.build_app(self.ipa, output, self.descriptor)
        self.assertEqual(output.read_bytes(), b"existing")

    def test_extraction_checkpoint_reuses_only_complete_verified_images(self):
        work, aoc = self.root / "work", self.root / "aoc"
        romfs, _, _ = split.extract_game(self.ipa, work, aoc)
        self.assertEqual(romfs.read_bytes(), self.files["romfs.img"])
        first = romfs.stat().st_mtime_ns
        split.extract_game(self.ipa, work, aoc)
        self.assertEqual(romfs.stat().st_mtime_ns, first)
        romfs.write_bytes(b"changed data")
        with self.assertRaisesRegex(ValueError, "unverified extraction"):
            split.extract_game(self.ipa, work, aoc)


@unittest.skipUnless(os.environ.get("BOTW_CI_STAGE"), "requires prepared source for the original .nxrom packer")
class OriginalFormatTests(unittest.TestCase):
    def test_split_data_matches_original_format_and_identity_across_app_updates(self):
        packer = split.load_packer(Path(os.environ["BOTW_CI_STAGE"]))
        import romfs_image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); romfs = root / "romfs"; aoc = root / "aoc"
            (romfs / "Pack").mkdir(parents=True); aoc.mkdir()
            (romfs / "Pack/Bootup_Graphics.pack").write_bytes(b"game" * 10000)
            (aoc / "1.img").write_bytes(b"dlc data")
            image = root / "RomFS.img"; romfs_image.pack(romfs, image)
            ipa = root / "Full.ipa"; fixture(ipa, game=image.read_bytes())
            extracted, dlc, _ = split.extract_game(ipa, root / "work", root / "extracted-aoc")
            data, metadata = root / "Game.nxrom", root / "Game.json"
            descriptor = packer.pack(data, extracted, dlc, "01007EF00011E000", "1.9.0", "Pack/Bootup_Graphics.pack", metadata)
            direct, direct_metadata = root / "Original.nxrom", root / "Original.json"
            original = packer.pack(direct, image, dlc, "01007EF00011E000", "1.9.0", "Pack/Bootup_Graphics.pack", direct_metadata)
            self.assertEqual(data.read_bytes(), direct.read_bytes())
            self.assertEqual(descriptor, original)
            split.build_app(ipa, root / "App.ipa", descriptor)
            self.assertEqual(data.read_bytes()[:8], b"NXROM001")
