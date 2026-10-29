#!/usr/bin/env python3
"""Package a rebuilt BOTW app without re-sending installed game/user data.

SOURCE is a rebuilt .app directory or IPA. The output is a new distribution ZIP,
not an IPA or an installer. Excluded files are inventoried but never opened.
"""
import argparse
from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import struct
import unicodedata
import zipfile

BUNDLE_ID = 'org.nxnative.01007ef00011e000'
CHUNK = 1024 * 1024
METADATA_LIMIT = 4 * CHUNK


def safe_name(name):
    if (not name or name.startswith('/') or '\\' in name or
            any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError(f'Unsafe archive path: {name!r}')
    parts = name.rstrip('/').split('/')
    if any(p in ('', '.', '..') or ':' in p for p in parts):
        raise ValueError(f'Unsafe archive path: {name!r}')
    return '/'.join(parts)


def folded(name):
    return unicodedata.normalize('NFC', name).casefold()


def exclusion(name):
    parts = name.casefold().split('/')
    if parts[0] in ('romfs', 'romfs.img', 'aoc'):
        return 'installed_game_data'
    if parts[0] in ('documents', 'library', 'save', 'saves', 'savedata', 'tmp', 'temp'):
        return 'user_data'
    if any(p in ('lcappinfo.plist', 'lccontainerinfo.plist') for p in parts):
        return 'livecontainer_state'
    if any(p in ('_codesignature', 'embedded.mobileprovision', 'sc_info', '_masreceipt') for p in parts):
        return 'device_signing'
    if any(p.endswith('_livecontainerpatchbackup') for p in parts):
        return 'livecontainer_backup'
    return None


class Source:
    def __init__(self, path):
        self.path = Path(path)
        self.files = {}
        self.zip = None
        self.kind = 'app' if self.path.is_dir() else 'ipa'
        if self.path.is_symlink():
            raise ValueError('Source must not be a symlink')
        self.nodes = {}
        try:
            if self.kind == 'app':
                if self.path.suffix.casefold() != '.app':
                    raise ValueError('Directory source must end in .app')
                self._scan_directory(self.path, '')
            else:
                self.zip = zipfile.ZipFile(self.path, 'r')
                members = self.zip.infolist()
                for member in members:
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise ValueError(f'Non-regular ZIP entry: {member.filename!r}')
                    self._node(member.filename, member.is_dir())
                    if member.flag_bits & 1:
                        raise ValueError('Encrypted ZIP entries are unsupported')
                roots = {safe_name(m.filename).split('/')[1] for m in members
                         if safe_name(m.filename).startswith('Payload/')
                         and len(safe_name(m.filename).split('/')) >= 3
                         and safe_name(m.filename).split('/')[1].endswith('.app')}
                if len(roots) != 1:
                    raise ValueError('IPA must contain exactly one Payload/*.app bundle')
                prefix = 'Payload/' + roots.pop() + '/'
                for member in members:
                    if member.filename.startswith(prefix) and not member.is_dir():
                        relative = safe_name(member.filename[len(prefix):])
                        self.files[relative] = (member, member.file_size, member.external_attr >> 16)
            self._validate_ancestors()
        except BaseException:
            self.close()
            raise

    def _node(self, name, directory):
        key = folded(safe_name(name))
        if key in self.nodes:
            raise ValueError(f'Duplicate or case-colliding path: {name!r}')
        self.nodes[key] = directory

    def _validate_ancestors(self):
        for name in self.nodes:
            parts = name.split('/')
            for i in range(1, len(parts)):
                if self.nodes.get('/'.join(parts[:i])) is False:
                    raise ValueError(f'File used as a directory: {name!r}')

    def _scan_directory(self, path, prefix):
        for entry in sorted(os.scandir(path), key=lambda e: e.name):
            relative = safe_name(prefix + entry.name)
            metadata = entry.stat(follow_symlinks=False)
            if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                raise ValueError(f'Symlink or special source entry: {relative!r}')
            self._node(relative, stat.S_ISDIR(metadata.st_mode))
            if stat.S_ISDIR(metadata.st_mode):
                self._scan_directory(Path(entry.path), relative + '/')
            else:
                self.files[relative] = (Path(entry.path), metadata.st_size, metadata.st_mode)

    @contextmanager
    def open(self, name):
        locator, size, _ = self.files[name]
        if self.zip:
            with self.zip.open(locator) as stream:
                yield stream
        else:
            fd = os.open(locator, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size != size:
                    raise ValueError(f'Source changed while reading: {name}')
                yield stream
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError(f'Source changed while reading: {name}')

    def read_small(self, name, limit=METADATA_LIMIT):
        if self.files[name][1] > limit:
            raise ValueError(f'Metadata is too large: {name}')
        with self.open(name) as stream:
            value = stream.read(limit + 1)
        if len(value) > limit or len(value) != self.files[name][1]:
            raise ValueError(f'Metadata size mismatch: {name}')
        return value

    def close(self):
        if self.zip:
            self.zip.close()


def macho_type(source):
    size = source.files['DmIOS'][1]
    with source.open('DmIOS') as stream:
        header = stream.read(32)
        if len(header) != 32:
            raise ValueError('DmIOS has no complete Mach-O header')
        magic, cpu, subtype, kind, count, command_bytes, _, reserved = struct.unpack('<8I', header)
        if (magic != 0xfeedfacf or cpu != 0x100000c or subtype != 0 or
                kind not in (2, 6) or reserved or not 0 < count <= 4096 or
                command_bytes > CHUNK or command_bytes > size - 32):
            raise ValueError('DmIOS must be a thin arm64 MH_EXECUTE or MH_DYLIB')
        commands = stream.read(command_bytes)
    if len(commands) != command_bytes:
        raise ValueError('Truncated Mach-O load commands')
    position, texts, ids = 0, 0, 0
    for _ in range(count):
        if position + 8 > len(commands):
            raise ValueError('Truncated Mach-O load command')
        command, length = struct.unpack_from('<II', commands, position)
        if length < 8 or length % 8 or position + length > len(commands):
            raise ValueError('Invalid Mach-O load command length')
        raw = commands[position:position + length]
        if command == 0x19:
            if length < 72:
                raise ValueError('Invalid Mach-O segment')
            _, _, offset, file_size, _, protection, sections, _ = struct.unpack_from('<4Q4I', raw, 24)
            if length != 72 + sections * 80 or offset + file_size > size:
                raise ValueError('Invalid Mach-O segment bounds')
            if raw[8:24].rstrip(b'\0') == b'__TEXT':
                texts += 1
                if offset or file_size < 32 + command_bytes or not protection & 4:
                    raise ValueError('Invalid executable __TEXT segment')
        if command == 0xd:
            if length < 24:
                raise ValueError('Invalid Mach-O dylib identity')
            name_offset = struct.unpack_from('<I', raw, 8)[0]
            if not 24 <= name_offset < length or not raw[name_offset] or b'\0' not in raw[name_offset:]:
                raise ValueError('Invalid Mach-O dylib identity name')
            ids += 1
        position += length
    if position != command_bytes or texts != 1 or (kind == 6 and ids != 1):
        raise ValueError('Inconsistent Mach-O header or missing dylib identity')
    return kind


def shader_inputs(source, prefix, chain):
    stamp_path = prefix + 'inputs.tsv'
    if stamp_path not in source.files:
        raise ValueError('Missing active shader inputs.tsv')
    rows = {}
    for line in source.read_small(stamp_path).decode('utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        key, value = line.split('\t', 1)
        if key in rows:
            raise ValueError('Duplicate shader input stamp field')
        rows[key] = value
    if rows.get('game') != '01007EF00011E000' or not re.fullmatch(r'[0-9a-f]{12}', rows.get('inputs', '')):
        raise ValueError('Shader input stamp does not identify this BOTW build')
    expected = f'# chain b-{chain} inputs {rows["inputs"]}'
    for path in ('dm/c1_full.tsv', 'dm/cb_extent.tsv'):
        if path in source.files:
            with source.open(path) as stream:
                line = stream.readline(4096).decode('utf-8').rstrip('\r\n')
            if line != expected and not line.startswith(expected + ' '):
                raise ValueError('Shader table input stamp mismatch: ' + path)
    return rows['inputs']


def validate_source(source):
    if 'Info.plist' not in source.files or 'DmIOS' not in source.files:
        raise ValueError('Source must contain Info.plist and DmIOS')
    info_bytes = source.read_small('Info.plist')
    info = plistlib.loads(info_bytes)
    if not isinstance(info, dict) or info.get('CFBundleIdentifier') != BUNDLE_ID or info.get('CFBundleExecutable') != 'DmIOS':
        raise ValueError('Only the BOTW org.nxnative.01007ef00011e000 / DmIOS bundle is supported')
    commit, chain = info.get('DMCommit'), info.get('DMChain')
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{7,40}(?:\+dirty)?', commit):
        raise ValueError('Info.plist must record the rebuilt source revision in DMCommit')
    if not isinstance(chain, str) or not re.fullmatch(r'[0-9a-f]{12}', chain):
        raise ValueError('Info.plist must record a 12-digit shader DMChain')
    prefix = f'msl/b-{chain}/'
    shaders = [name for name in source.files if name.startswith(prefix)]
    if not any(name.endswith('.metallib') and source.files[name][1] for name in shaders):
        raise ValueError(f'Missing shader libraries for DMChain: {prefix}')
    reflections = [name for name in shaders if name.endswith('_b.json')]
    if not reflections:
        raise ValueError(f'Missing shader reflection for DMChain: {prefix}')
    for name in shaders:
        if name.endswith('.json') and not name.endswith('/_b_build.json'):
            reflection = json.loads(source.read_small(name))
            if not isinstance(reflection, dict) or reflection.get('chain') != chain:
                raise ValueError(f'Shader reflection chain mismatch: {name}')
    inputs_digest = shader_inputs(source, prefix, chain)
    kind = macho_type(source)
    return info_bytes, commit, chain, kind, inputs_digest


def zip_info(name, mode=0o644):
    item = zipfile.ZipInfo(name)
    item.create_system = 3
    item.external_attr = (stat.S_IFREG | mode) << 16
    item.compress_type = zipfile.ZIP_DEFLATED
    return item


README = '''BOTW incremental distribution package

This ZIP is not an IPA, an automatic installer, or a ready-to-install app.
It was assembled from a rebuilt app; packaging does not verify iOS execution,
shader correctness, or compatibility of an existing NXKeyboard plugin.

Close the game completely and back up the existing app before applying files.
MERGE AppFiles into the existing org.nxnative.01007ef00011e000.app directory.
Replace only matching files. DO NOT replace/delete the whole .app or any data
container. Keep the installed romfs directory / romfs.img, aoc, and save data.
This package assumes those existing game resources match the rebuilt version.
It contains the new shader chain and other runtime/framework resources too;
copying only DmIOS is insufficient.

Metadata/romfs.id, when present, is a REFERENCE: compare its size/index with
an installed Documents/romfs.img before applying it. Review an old installed
romfs.id even if the new source has no identity file. Repacking a same-version
image can change its index; never force a new marker over unverified old data.

Metadata/new-Info.plist is a REFERENCE, not an automatically applied file.
Review and merge required new app/runtime keys into the installed Info.plist
while retaining LiveContainer-specific identity and settings. Do not blindly
overwrite it, LCAppInfo.plist, or any device/container state. The original
embedded.mobileprovision and signature resource directories are excluded;
Mach-O files may still contain their build-time signatures and need re-signing.

LiveContainer requires a converted main executable and new signatures. If
manifest.json says conversion_required=true, DmIOS is still MH_EXECUTE and must
be converted by LiveContainer before re-signing. Force-signing alone may skip
conversion and produce "cannot dlopen a main executable". Complete and verify
the conversion, then force re-sign the app and its runtime files in LiveContainer.
Even MH_DYLIB is only a structural check, not proof of complete LC conversion.
Do not launch until the required metadata merge, conversion and signing finish.

manifest.json records the rebuilt commit, shader chain, copied-file SHA-256
hashes and excluded-file inventory. SHA256SUMS.txt verifies packaged files;
manifest.sha256 verifies manifest.json. Compare the rebuilt version with the
installed game's resource version before merging. Restore your backup on failure.
'''


def build_update(source_path, output_path):
    source_path, output_path = Path(source_path), Path(output_path)
    if output_path.exists() or output_path.is_symlink():
        raise ValueError('Output must be a new file')
    if source_path.resolve() == output_path.resolve() or (
            source_path.is_dir() and output_path.resolve().is_relative_to(source_path.resolve())):
        raise ValueError('Output must be outside the source app and separate from its IPA')
    source = Source(source_path)
    created = None
    try:
        info, commit, chain, kind, inputs_digest = validate_source(source)
        reference_metadata = []
        excluded, selected, counts = [], [], defaultdict(lambda: {'files': 0, 'bytes': 0})
        for name, (_, size, mode) in sorted(source.files.items()):
            reason = exclusion(name)
            match = re.match(r'^msl/b-([0-9a-f]{12})/', name)
            if match and match[1] != chain:
                reason = 'inactive_shader_chain'
            if name.casefold() == 'romfs.id':
                reference_metadata.append(name)
                reason = 'resource_identity_requires_review'
            if reason:
                excluded.append({'path': name, 'size': size, 'reason': reason})
                counts[reason]['files'] += 1
                counts[reason]['bytes'] += size
            elif name != 'Info.plist':
                selected.append((name, size, mode))
        files, checksums = [], []
        with output_path.open('xb') as output:
            created = os.fstat(output.fileno())
            with zipfile.ZipFile(output, 'w', allowZip64=True) as archive:
                for name, expected_size, mode in selected:
                    destination = 'AppFiles/' + name
                    digest, written = hashlib.sha256(), 0
                    permissions = 0o755 if name == 'DmIOS' or mode & 0o111 else 0o644
                    with source.open(name) as stream, archive.open(zip_info(destination, permissions), 'w', force_zip64=True) as target:
                        while chunk := stream.read(CHUNK):
                            target.write(chunk); digest.update(chunk); written += len(chunk)
                    if written != expected_size:
                        raise ValueError(f'Source size changed while packing: {name}')
                    value = digest.hexdigest()
                    files.append({'path': destination, 'size': written, 'sha256': value})
                    checksums.append((destination, value))

                def add(name, data):
                    archive.writestr(zip_info(name), data)
                    value = hashlib.sha256(data).hexdigest()
                    checksums.append((name, value))
                    return value

                identity_records = []
                for name in reference_metadata:
                    data = source.read_small(name)
                    identity_records.append({'path': 'Metadata/' + name,
                                             'sha256': add('Metadata/' + name, data)})
                info_hash = add('Metadata/new-Info.plist', info)
                report = {
                    'format': 'nxkeyboard-botw-incremental-v1',
                    'bundle_identifier': BUNDLE_ID, 'executable': 'DmIOS',
                    'source_kind': source.kind, 'source_commit': commit, 'shader_chain': chain,
                    'shader_inputs': inputs_digest,
                    'resource_identity_metadata': identity_records,
                    'resource_identity_review_required': True,
                    'source_version': str(plistlib.loads(info).get('CFBundleShortVersionString', 'unknown')),
                    'source_macho_type': 'MH_EXECUTE' if kind == 2 else 'MH_DYLIB',
                    'conversion_required': kind == 2, 'signing_required': True,
                    'ready_to_install': False, 'installation_mode': 'reviewed_file_merge',
                    'metadata': {'path': 'Metadata/new-Info.plist', 'sha256': info_hash, 'apply_automatically': False},
                    'files': files, 'excluded': excluded, 'excluded_summary': dict(counts),
                    'preserves_installed_game_data': True,
                    'validation_scope': 'Bundle identity, Mach-O structure, shader chain metadata and streamed file hashes; no runtime validation.',
                }
                manifest = (json.dumps(report, indent=2, ensure_ascii=True) + '\n').encode()
                manifest_hash = add('manifest.json', manifest)
                add('manifest.sha256', f'{manifest_hash}  manifest.json\n'.encode())
                add('README.txt', README.encode())
                archive.writestr(zip_info('SHA256SUMS.txt'), ''.join(
                    f'{value}  {name}\n' for name, value in sorted(checksums)).encode())
        return {'output': str(output_path), 'source_commit': commit, 'shader_chain': chain,
                'files': len(files), 'packed_source_bytes': sum(f['size'] for f in files),
                'excluded_summary': dict(counts), 'conversion_required': kind == 2,
                'ready_to_install': False}
    except BaseException:
        if created:
            try:
                current = output_path.lstat()
                if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                    output_path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Rebuilt BOTW .app directory or IPA')
    parser.add_argument('output', type=Path, help='New incremental distribution ZIP')
    args = parser.parse_args()
    try:
        result = build_update(args.source, args.output)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, plistlib.InvalidFileException) as exc:
        parser.exit(2, f'{parser.prog}: {exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
