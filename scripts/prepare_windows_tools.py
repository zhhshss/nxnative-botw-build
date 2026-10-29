#!/usr/bin/env python3
"""Install pinned Windows host-compilation tools; no game or shader inputs.

Uses the staged fork's verified LLVM installer and SDK archive adapter.
Only Python's standard library is needed. SDK/compiler files stay local to the
runner; the host artifact contains their identities, never the SDK itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid

LLVM_VERSION = '20261020'
LLVM_SHA256 = 'fd5462b7f5d93b2ff981fcd3f9c7e427e47f3203c64ea91c27bcc444eebd74cd'
LLVM_URL = ('https://github.com/mstorsjo/llvm-mingw/releases/download/20261020/'
            'llvm-mingw-20261020-ucrt-x86_64.zip')
SDK_VERSION = '27.0'
SDK_SHA256 = '97d107a51562b2236c064a0dea98777faddcf65a1259d4f7abccd46695ef3c15'
SDK_URL = 'https://github.com/xybp888/iOS-SDKs/releases/download/iOS27.0-SDKs/iPhoneOS27.0.sdk.zip'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def checked_stage(path: Path) -> Path:
    stage = path.resolve(strict=True)
    provenance = json.loads((stage / 'provenance.json').read_text(encoding='utf-8'))
    if provenance.get('status') != 'prepared_source_only' or provenance.get('limitations', {}).get('game_data_included') is not False:
        raise ValueError('Use a source-only tree from prepare_source_tree.py')
    return stage


def prepare(stage: Path, out: Path) -> dict:
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise ValueError('This installer is pinned for a Windows x64 runner')
    stage = checked_stage(stage)
    out = out.resolve()
    if out == Path(out.anchor) or out == Path.home().resolve() or out.is_relative_to(stage):
        raise ValueError('Use a dedicated tools directory outside the source stage')
    out.mkdir(parents=True, exist_ok=True)
    # All imported helpers and their transitive imports use the standard library.
    sys.path.insert(0, str(stage))
    from webui import apple_tools, windows_tools
    if windows_tools.LLVM_VERSION != LLVM_VERSION or windows_tools.LLVM_ASSETS.get('x86_64') != LLVM_SHA256:
        raise ValueError('Fork LLVM pin differs from this build workflow')
    release = apple_tools.sdk_release(SDK_VERSION)
    if release.get('url') != SDK_URL or release.get('sha256') != SDK_SHA256:
        raise ValueError('Fork SDK pin differs from this build workflow')
    task = out / ('install-' + uuid.uuid4().hex[:12])
    task.mkdir()

    def run(folder, command, env=None):
        print('Running compiler validation: ' + Path(command[0]).name, flush=True)
        subprocess.run([str(value) for value in command], cwd=folder, env=env, check=True)

    llvm = windows_tools.install_llvm(task, out, run)
    sdk = out / 'ios-cross' / f'iPhoneOS{SDK_VERSION}.sdk'
    if sdk.exists():
        marker = sdk / 'nxnative-sdk-source.json'
        info = apple_tools.sdk_info(sdk)
        if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')) != release or not info or info['version'] != SDK_VERSION:
            raise ValueError('Existing SDK is unverified; use a fresh tools directory')
        print('Reusing SDK installed from the pinned archive', flush=True)
    else:
        apple_tools.install_sdk(task, {'tools': str(out), 'sdk_source': 'xybp888', 'sdk_version': SDK_VERSION})
    clang, lld = llvm / 'bin/clang.exe', llvm / 'bin/lld.exe'
    versions = {}
    for name, binary in (('clang', clang), ('lld', lld)):
        command = [str(binary), '--version'] if name == 'clang' else [str(binary), '-flavor', 'darwin', '--version']
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        versions[name] = (result.stdout or result.stderr).strip()
    report = {
        'schema_version': 1, 'purpose': 'public_host_only_compile',
        'platform': 'Windows-x86_64', 'clang': str(clang), 'lld': str(lld), 'sdk': str(sdk),
        'llvm': {'version': LLVM_VERSION, 'url': LLVM_URL, 'archive_sha256': LLVM_SHA256,
                 'clang_sha256': sha256(clang), 'lld_sha256': sha256(lld)},
        'sdk_release': {'version': SDK_VERSION, 'url': SDK_URL, 'archive_sha256': SDK_SHA256},
        'compiler_versions': versions,
        'stage_provenance_sha256': sha256(stage / 'provenance.json'),
        'game_data_used': False, 'metal_shader_compiler_installed': False,
    }
    target = out / 'tools.json'
    temporary = out / 'tools.json.partial'
    temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, target)
    print('Tool manifest: ' + str(target), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--github-output', type=Path, help='Optional GITHUB_OUTPUT file for subsequent steps')
    args = parser.parse_args()
    try:
        report = prepare(args.stage, args.out)
        if args.github_output:
            values = {'tools_manifest': str(args.out.resolve() / 'tools.json'),
                      'clang': report['clang'], 'lld': report['lld'], 'sdk': report['sdk']}
            if any('\n' in value or '\r' in value for value in values.values()):
                raise ValueError('Tool path cannot contain line breaks')
            with args.github_output.open('a', encoding='utf-8') as stream:
                for key, value in values.items():
                    stream.write(f'{key}={value}\n')
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{parser.prog}: {error}\n')


if __name__ == '__main__':
    main()
