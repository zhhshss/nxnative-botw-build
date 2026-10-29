#!/usr/bin/env python3
"""Synthetic tests: no game files, SDK, Mac, or large resource copies needed."""
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import stat
import struct
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

TOOL_PATH = Path(__file__).resolve().parents[1] / 'scripts/build_incremental_update.py'
SPEC = importlib.util.spec_from_file_location('incremental_update', TOOL_PATH)
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)
CHAIN = '2fcec1622a5f'


def executable(kind=2):
    segment = struct.pack('<II16s4Q4I', 0x19, 72, b'__TEXT', 0x100000000, 4096, 0, 4096, 5, 5, 0, 0)
    identity = struct.pack('<6I8s', 0xd, 32, 24, 2, 0x10000, 0x10000, b'DmIOS\0') if kind == 6 else b''
    header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, kind, 1 + bool(identity), len(segment + identity), 0, 0)
    return (header + identity + segment).ljust(4096, b'\0')


def make_app(root, kind=2):
    app = root / 'Rebuilt.app'
    app.mkdir()
    files = {
        'Info.plist': plistlib.dumps({'CFBundleIdentifier': TOOL.BUNDLE_ID, 'CFBundleExecutable': 'DmIOS',
                                     'DMCommit': '32b12b4de075', 'DMChain': CHAIN, 'CFBundleShortVersionString': '1.9.0'}),
        'DmIOS': executable(kind),
        f'msl/b-{CHAIN}/shader_b.metallib': b'MTLB synthetic shader',
        f'msl/b-{CHAIN}/shader_b.json': json.dumps({'chain': CHAIN, 'stage': 'vs'}).encode(),
        'Frameworks/NewRuntime.framework/NewRuntime': b'new framework code',
        'Frameworks/NewRuntime.framework/Info.plist': b'framework metadata',
        'Frameworks/NewRuntime.framework/_CodeSignature/CodeResources': b'device signature',
        f'msl/b-{CHAIN}/inputs.tsv': b'game\t01007EF00011E000\ninputs\t123456789abc\n',
        'romfs.id': b'DMROMFSID1 synthetic identity',
        'msl/b-aaaaaaaaaaaa/old.metallib': b'old shader',
        'dm/launcher.json': b'{"settings":[]}',
        'dm/main.fixups.tsv': b'# BUILD_ID\tbuild\n',
        'Assets.car': b'new launcher assets',
        'aoc/001/DLC.bin': b'large installed DLC placeholder',
        'Documents/save/slot.dat': b'private saved game',
        'save/slot.dat': b'private saved game',
        'LCAppInfo.plist': b'private container settings',
        '_CodeSignature/CodeResources': b'device signature',
        'embedded.mobileprovision': b'device provision',
        'DmIOS_LiveContainerPatchBackUp': b'old executable',
    }
    for name, value in files.items():
        path = app / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(value)
    # A sparse file simulates a huge RomFS without allocating/copying it.
    with (app / 'RoMfS.ImG').open('wb') as stream:
        stream.truncate(8 * 1024**3)
    return app


def make_ipa(app, path, extra=()):
    with zipfile.ZipFile(path, 'w') as archive:
        for file in sorted(app.rglob('*')):
            if file.is_file():
                relative = file.relative_to(app).as_posix()
                # The IPA fixture has a small stand-in; reads are trapped below.
                value = b'not read' if relative.casefold() == 'romfs.img' else file.read_bytes()
                archive.writestr('Payload/Rebuilt.app/' + relative, value)
        for name, value in extra:
            archive.writestr(name, value)


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nxk-incremental-test-')
        self.root = Path(self.temporary.name)
        self.app = make_app(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def audit_package(self, path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            report = json.loads(archive.read('manifest.json'))
            self.assertNotIn('AppFiles/Info.plist', names)
            self.assertIn('Metadata/new-Info.plist', names)
            self.assertIn('Metadata/romfs.id', names)
            self.assertNotIn('AppFiles/romfs.id', names)
            self.assertNotIn('AppFiles/msl/b-aaaaaaaaaaaa/old.metallib', names)
            self.assertIn('AppFiles/Frameworks/NewRuntime.framework/NewRuntime', names)
            self.assertIn('AppFiles/Frameworks/NewRuntime.framework/Info.plist', names)
            self.assertIn('AppFiles/dm/launcher.json', names)
            self.assertIn(f'AppFiles/msl/b-{CHAIN}/shader_b.metallib', names)
            self.assertFalse(any(TOOL.exclusion(n[9:]) for n in names if n.startswith('AppFiles/')))
            for record in report['files']:
                value = archive.read(record['path'])
                self.assertEqual(record['size'], len(value))
                self.assertEqual(record['sha256'], hashlib.sha256(value).hexdigest())
            for line in archive.read('SHA256SUMS.txt').decode().splitlines():
                value, name = line.split('  ', 1)
                self.assertEqual(value, hashlib.sha256(archive.read(name)).hexdigest())
            self.assertIn('cannot dlopen a main executable', archive.read('README.txt').decode())
            self.assertEqual(report['source_commit'], '32b12b4de075')
            self.assertEqual(report['shader_chain'], CHAIN)
            self.assertFalse(report['ready_to_install'])
            return report

    def test_app_excludes_sparse_romfs_and_keeps_runtime(self):
        output = self.root / 'update.zip'
        original = TOOL.Source.open

        def guarded(source, name):
            self.assertIsNone(TOOL.exclusion(name), f'Excluded source was opened: {name}')
            return original(source, name)

        with mock.patch.object(TOOL.Source, 'open', guarded):
            result = TOOL.build_update(self.app, output)
        report = self.audit_package(output)
        self.assertTrue(report['conversion_required'])
        self.assertGreaterEqual(result['excluded_summary']['installed_game_data']['bytes'], 8 * 1024**3)
        self.assertLess(output.stat().st_size, 20000)

    def test_ipa_streams_selected_members_only(self):
        source, output = self.root / 'rebuilt.ipa', self.root / 'update.zip'
        make_ipa(self.app, source)
        original = zipfile.ZipFile.open
        def guarded(archive, name, *args, **kwargs):
            path = name.filename if isinstance(name, zipfile.ZipInfo) else name
            if str(archive.filename) == str(source):
                self.assertIsNone(TOOL.exclusion(path.removeprefix('Payload/Rebuilt.app/')))
            return original(archive, name, *args, **kwargs)
        with mock.patch.object(zipfile.ZipFile, 'open', guarded):
            TOOL.build_update(source, output)
        self.assertEqual(self.audit_package(output)['source_kind'], 'ipa')

    def test_dylib_still_requires_signing_and_review(self):
        (self.app / 'DmIOS').write_bytes(executable(6))
        output = self.root / 'update.zip'; TOOL.build_update(self.app, output)
        report = self.audit_package(output)
        self.assertFalse(report['conversion_required'])
        self.assertTrue(report['signing_required'])
        self.assertFalse(report['metadata']['apply_automatically'])

    def test_rejects_chain_identity_and_bad_macho(self):
        plist = self.app / 'Info.plist'; original = plist.read_bytes()
        for field, value in [('DMChain', 'aaaaaaaaaaaa'), ('DMCommit', 'unknown'),
                             ('CFBundleIdentifier', 'org.example.other'), ('CFBundleExecutable', '../DmIOS')]:
            with self.subTest(field=field):
                info = plistlib.loads(original); info[field] = value; plist.write_bytes(plistlib.dumps(info))
                output = self.root / 'bad.zip'
                with self.assertRaises(ValueError): TOOL.build_update(self.app, output)
                self.assertFalse(output.exists())
        plist.write_bytes(original)
        reflection = self.app / f'msl/b-{CHAIN}/shader_b.json'
        reflection.write_text('{"chain":"old"}')
        with self.assertRaisesRegex(ValueError, 'chain mismatch'):
            TOOL.build_update(self.app, self.root / 'bad.zip')
        reflection.write_text(json.dumps({'chain': CHAIN}))
        (self.app / 'DmIOS').write_bytes(b'not Mach-O')
        with self.assertRaises(ValueError): TOOL.build_update(self.app, self.root / 'bad.zip')

    def test_rejects_zip_paths_duplicates_and_symlinks(self):
        for extra in [('Payload/Rebuilt.app/../evil', b'x'), ('/absolute', b'x'),
                      ('Payload/Rebuilt.app\\evil', b'x'), ('Payload/Rebuilt.app/DmIOS', b'x'),
                      ('Payload/Rebuilt.app/dmios', b'x'), ('Payload/Rebuilt.app/DmIOS/child', b'x')]:
            with self.subTest(path=extra[0]):
                source = self.root / 'bad.ipa'
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning); make_ipa(self.app, source, [extra])
                with self.assertRaises(ValueError): TOOL.build_update(source, self.root / 'bad.zip')
                self.assertFalse((self.root / 'bad.zip').exists())
        link = zipfile.ZipInfo('Payload/Rebuilt.app/link')
        link.create_system = 3; link.external_attr = (stat.S_IFLNK | 0o777) << 16
        source = self.root / 'symlink.ipa'
        make_ipa(self.app, source, [(link, b'../../outside')])
        with self.assertRaisesRegex(ValueError, 'Non-regular ZIP'):
            TOOL.build_update(source, self.root / 'bad.zip')
        (self.app / 'linked').symlink_to(self.app / 'DmIOS')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            TOOL.build_update(self.app, self.root / 'bad.zip')

    def test_rejects_cross_game_or_mismatched_inputs(self):
        stamp = self.app / f'msl/b-{CHAIN}/inputs.tsv'
        stamp.write_text('game\t0100000000000000\ninputs\t123456789abc\n')
        with self.assertRaisesRegex(ValueError, 'input stamp'):
            TOOL.build_update(self.app, self.root / 'bad.zip')
        stamp.write_text('game\t01007EF00011E000\ninputs\t123456789abc\n')
        (self.app / 'dm/c1_full.tsv').write_text(f'# chain b-{CHAIN} inputs aaaaaaaaaaaa\n')
        with self.assertRaisesRegex(ValueError, 'table input stamp'):
            TOOL.build_update(self.app, self.root / 'bad.zip')
        self.assertFalse((self.root / 'bad.zip').exists())

    def test_output_not_overwritten_and_partial_removed(self):
        output = self.root / 'update.zip'; output.write_bytes(b'keep me')
        with self.assertRaisesRegex(ValueError, 'new file'): TOOL.build_update(self.app, output)
        self.assertEqual(output.read_bytes(), b'keep me')
        with self.assertRaisesRegex(ValueError, 'outside'):
            TOOL.build_update(self.app, self.app / 'new.zip')
        link = self.root / 'broken.zip'; link.symlink_to(self.root / 'absent')
        with self.assertRaisesRegex(ValueError, 'new file'): TOOL.build_update(self.app, link)
        output.unlink()
        original = TOOL.Source.open
        def failing(source, name):
            if name == 'Assets.car': raise OSError('synthetic source failure')
            return original(source, name)
        with mock.patch.object(TOOL.Source, 'open', failing):
            with self.assertRaisesRegex(OSError, 'synthetic'): TOOL.build_update(self.app, output)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
