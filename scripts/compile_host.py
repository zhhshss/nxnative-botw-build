#!/usr/bin/env python3
"""Compile public nxnative host objects, without linking or reading game data.

The resulting ZIP contains relocatable ARM64 Mach-O objects and reproducibility
metadata. It is not a runnable app and includes no guest, RomFS, SDK or shaders.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import zipfile

GAME = '01007EF00011E000'
UPSTREAM_PIN = '32b12b4de075f13fa8658b980f164c992e6136a1'
FORK_PIN = 'd1ba3e97876da86ff616c6d8e8d684b968fb8c5d'
CHAIN_PIN = '2fcec1622a5f'
LLVM_SHA256 = 'fd5462b7f5d93b2ff981fcd3f9c7e427e47f3203c64ea91c27bcc444eebd74cd'
SDK_SHA256 = '97d107a51562b2236c064a0dea98777faddcf65a1259d4f7abccd46695ef3c15'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def source_relative(path):
    value = PurePosixPath(path)
    if value.is_absolute() or '..' in value.parts or str(value) != path or '\\' in path:
        raise ValueError(f'Unsafe source-relative path: {path!r}')
    return path


def compile_host(stage, manifest_path, out, artifact, jobs):
    if platform.system() != 'Windows':
        raise ValueError('Use this script on a Windows runner with the pinned tool manifest')
    stage, manifest_path, out, artifact = (Path(p).resolve() for p in (stage, manifest_path, out, artifact))
    if out.exists() or out.is_symlink() or artifact.exists() or artifact.is_symlink():
        raise ValueError('Build directory and artifact must both be new paths')
    if (out.is_relative_to(stage) or artifact.is_relative_to(stage) or
            artifact.is_relative_to(out) or out.is_relative_to(artifact) or jobs < 1 or jobs > 16):
        raise ValueError('Use separate build/artifact paths outside the source stage and 1-16 jobs')
    provenance = read_json(stage / 'provenance.json')
    if (provenance.get('upstream', {}).get('commit') != UPSTREAM_PIN or
            provenance.get('fork', {}).get('commit') != FORK_PIN or
            provenance.get('limitations', {}).get('game_data_included') is not False):
        raise ValueError('Unexpected source stage provenance')
    if not (stage / 'dm/ios/Sources/DmLauncher.m').is_file():
        raise ValueError('The source stage must include the fork Objective-C launcher')
    tools = read_json(manifest_path)
    if (tools.get('platform') != 'Windows-x86_64' or
            tools.get('llvm', {}).get('archive_sha256') != LLVM_SHA256 or
            tools.get('sdk_release', {}).get('archive_sha256') != SDK_SHA256 or
            tools.get('stage_provenance_sha256') != sha256(stage / 'provenance.json')):
        raise ValueError('Tool manifest does not match this staged build')
    clang, sdk = Path(tools['clang']), Path(tools['sdk'])
    if sha256(clang) != tools['llvm']['clang_sha256'] or not sdk.is_dir():
        raise ValueError('Compiler or SDK is missing or changed')
    chain_header = stage / 'dm/gfx/nvn/dmsh_chain.h'
    match = re.search(r'^#define DMSH_CHAIN "([0-9a-f]{12})"$', chain_header.read_text(), re.M)
    if not match or match[1] != CHAIN_PIN:
        raise ValueError('Expected the pinned upstream shader chain; do not relabel old libraries')
    compiler_script = stage / 'webui/build/windows/ios_cross_compile.py'
    spec = importlib.util.spec_from_file_location('fork_ios_cross_compile', compiler_script)
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    sources = compiler.source_list(stage)
    expected_sources = {}
    for source in sources:
        relative = source_relative(source.relative_to(stage).as_posix())
        if source.is_symlink() or source.suffix not in ('.c', '.m', '.S'):
            raise ValueError('Unexpected host source: ' + relative)
        expected_sources[relative] = sha256(source)
    out.mkdir(parents=True)
    profile = out / 'profile' / GAME
    profile.mkdir(parents=True)
    shutil.copyfile(stage / f'dm/games/{GAME}/game.tsv', profile / 'game.tsv')
    shutil.copyfile(chain_header, profile / 'dmsh_chain.h')
    host = out / 'host'
    command = [sys.executable, str(compiler_script), '--clang', str(clang), '--sdk', str(sdk),
               '--profile', str(profile), '--out', str(host), '--jobs', str(jobs), '--cpu', 'apple-a16']
    environment = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    print(f'Compiling {len(sources)} public host sources; no guest or shader input', flush=True)
    subprocess.run(command, cwd=stage, env=environment, check=True)
    inventory = read_json(host / 'inventory.json')
    if len(inventory) != len(expected_sources) or {e['source'] for e in inventory} != set(expected_sources):
        raise ValueError('Compile inventory differs from the selected public host sources')
    normalized, object_files = [], []
    for entry in inventory:
        relative = source_relative(entry['source'])
        if entry['exit_code'] or sha256(stage / relative) != expected_sources[relative]:
            raise ValueError('Compile failed or source changed: ' + relative)
        obj = Path(entry['object']).resolve()
        if obj.parent != (host / 'objects').resolve() or obj.is_symlink():
            raise ValueError('Object is outside the host output directory')
        with obj.open('rb') as stream:
            header = stream.read(16)
        if len(header) != 16 or struct.unpack('<4I', header) != (0xfeedfacf, 0x100000c, 0, 1):
            raise ValueError('Not a relocatable thin arm64 Mach-O object: ' + relative)
        if sha256(obj) != entry['sha256']:
            raise ValueError('Object hash differs from compile inventory: ' + relative)
        name = 'objects/' + obj.name
        if name in {item[1] for item in object_files}:
            raise ValueError('Duplicate object artifact path')
        # Record commands with relocatable, non-secret directory placeholders.
        substitutions = ((str(stage), '<SOURCE>'), (str(out), '<BUILD>'),
                         (str(sdk), '<SDK>'), (str(clang), '<CLANG>'), (sys.executable, '<PYTHON>'))
        recorded_command = []
        for argument in entry['command']:
            for original, replacement in substitutions:
                argument = argument.replace(original, replacement)
            recorded_command.append(argument.replace('\\', '/'))
        normalized.append({'source': relative, 'source_sha256': expected_sources[relative],
                           'object': name, 'sha256': entry['sha256'], 'exit_code': 0,
                           'command': recorded_command})
        object_files.append((obj, name))
    # Only explicit output files are archived; never recurse over build/tools.
    archive_files = object_files + [
        (host / 'include/game_profile.h', 'include/game_profile.h'),
        (profile / 'game.tsv', 'profile/game.tsv'),
        (profile / 'dmsh_chain.h', 'include/dmsh_chain.h'),
        (stage / 'provenance.json', 'source-provenance.json'),
    ]
    for path in stage.rglob('*'):
        if (path.is_file() and not path.is_symlink() and
                path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE', 'PATENTS', 'AUTHORS'))):
            relative = path.relative_to(stage).as_posix()
            if relative == 'LICENSE' or relative.startswith(('dm/third_party/', 'build-provenance/licenses/')):
                archive_files.append((path, 'licenses/' + relative))
    build_report = {
        'schema_version': 1, 'status': 'compiled_host_objects_only',
        'upstream_commit': UPSTREAM_PIN, 'fork_commit': FORK_PIN, 'profile': GAME,
        'compiled_shader_chain': CHAIN_PIN, 'optimization': 'O1', 'cpu': 'apple-a16', 'ios_minimum': '26.0',
        'object_count': len(normalized), 'source_provenance_sha256': sha256(stage / 'provenance.json'),
        'compiler': tools['compiler_versions'], 'llvm': tools['llvm'], 'sdk_release': tools['sdk_release'],
        'scripts': {'compile_host.py': sha256(Path(__file__)), 'fork_ios_cross_compile.py': sha256(compiler_script)},
        'game_data_included': False, 'sdk_included': False, 'shader_libraries_included': False,
        'guest_linked': False, 'device_tested': False, 'ready_to_install': False,
        'limitations': [
            'Relocatable host objects only, not a runnable DmIOS or IPA.',
            'Compiled for the pinned upstream chain; old Windows shader libraries are not claimed compatible.',
            'Recompile nvn_mtl.m if the actual final shader chain changes.',
            'Original guest rewrite updates, native aspect/HUD work and plugin integration remain separate.',
        ],
    }
    README = (
        'Public host compilation artifact\n\n'
        'This archive is not an app or IPA. It contains no game guest, RomFS, shader libraries or Apple SDK.\n'
        'inventory.json uses paths relative to the archive root. Verify all object SHA-256 values.\n'
        'To use the existing fork linker, resolve each object path against the extracted artifact root first.\n'
        'Generate embed.S with the staged gen_embed.py (regular,no_dead_strip sections), assemble private\n'
        'rewritten guest modules into embed.o, then link on a compatible toolchain and matching SDK.\n'
        'Shader libraries must match the compiled chain; a chain change requires nvn_mtl.m recompilation.\n'
        'No shader compatibility, game behavior, native aspect-ratio/HUD feature or device result is implied.\n'
        'Retain the included notices when redistributing the host code.\n'
    )
    generated = {
        'inventory.json': (json.dumps(normalized, indent=2) + '\n').encode(),
        'provenance.json': (json.dumps(build_report, indent=2) + '\n').encode(),
        'README.txt': README.encode(),
    }
    names = [name for _, name in archive_files] + list(generated)
    if len(names) != len(set(names)):
        raise ValueError('Duplicate artifact member name')
    artifact.parent.mkdir(parents=True, exist_ok=True)
    created = None
    try:
        with artifact.open('xb') as raw:
            created = os.fstat(raw.fileno())
            with zipfile.ZipFile(raw, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                sums = []
                for path, name in sorted(archive_files, key=lambda item: item[1]):
                    digest = hashlib.sha256()
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3; info.external_attr = (stat.S_IFREG | 0o644) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with path.open('rb') as source, archive.open(info, 'w', force_zip64=True) as target:
                        while block := source.read(1024 * 1024):
                            digest.update(block); target.write(block)
                    sums.append(f'{digest.hexdigest()}  {name}\n')
                for name, value in sorted(generated.items()):
                    archive.writestr(name, value)
                    sums.append(f'{hashlib.sha256(value).hexdigest()}  {name}\n')
                archive.writestr('SHA256SUMS.txt', ''.join(sorted(sums)))
    except BaseException:
        if created and artifact.exists():
            current = artifact.stat()
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                artifact.unlink()
        raise
    result = {'artifact': str(artifact), 'bytes': artifact.stat().st_size,
              'sha256': sha256(artifact), 'objects': len(normalized), 'ready_to_install': False}
    (out / 'artifact-report.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--tools-manifest', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--artifact', required=True, type=Path)
    parser.add_argument('--jobs', type=int, default=8)
    args = parser.parse_args()
    try:
        compile_host(args.stage, args.tools_manifest, args.out, args.artifact, args.jobs)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{parser.prog}: {error}\n')


if __name__ == '__main__':
    main()
