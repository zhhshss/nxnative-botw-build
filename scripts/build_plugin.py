#!/usr/bin/env python3
"""Cross-build the existing plugin with the runner's verified Windows tools."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    tools = json.loads(args.tools_manifest.read_text())
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    clang, lld, sdk = (str(tools[key]) for key in ('clang', 'lld', 'sdk'))
    obj = out / 'NXKeyboard.o'
    binary = out / 'NXKeyboard.dylib'
    subprocess.run([clang, '-target', 'arm64-apple-ios14.0', '-isysroot', sdk,
                    '-F', str(Path(sdk) / 'System/Library/SubFrameworks'),
                    '-DUSE_UIKIT_PUBLIC_HEADERS=1', '-fobjc-arc', '-fblocks',
                    '-fno-stack-protector', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-Wno-deprecated-declarations', '-fvisibility=hidden',
                    '-c', str(root / 'plugin/src/NXKeyboard.m'), '-o', str(obj)], check=True)
    command = [lld, '-flavor', 'darwin', '-dylib', '-arch', 'arm64',
               '-platform_version', 'ios', '14.0', '27.0', '-syslibroot', sdk,
               '-F', str(Path(sdk) / 'System/Library/SubFrameworks'),
               '-install_name', '@rpath/NXKeyboard.dylib', '-adhoc_codesign',
               '-dead_strip', '-o', str(binary), str(obj), '-lSystem', '-lobjc', '-lz']
    for framework in ('UIKit', 'Foundation', 'GameController', 'CoreGraphics', 'QuartzCore'):
        command += ['-framework', framework]
    subprocess.run(command, check=True)
    spec = importlib.util.spec_from_file_location('verify', root / 'plugin/tools/verify.py')
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)
    data = binary.read_bytes()
    kind, commands = verify.macho(data)
    if kind != 6:
        raise ValueError('Plugin is not MH_DYLIB')
    signature = next(raw for cmd, raw in commands if cmd == 0x1d)
    pages = verify.audit_signature(data, signature)
    report = {'version': '0.3.2', 'sha256': hashlib.sha256(data).hexdigest(),
              'signature_pages_verified': pages, 'game_files_used': False,
              'target': 'Existing supported old DmIOS only; new host adaptation pending',
              'runtime_tested': False}
    (out / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    (out / 'README.txt').write_text(
        'NXKeyboard 0.3.2: resource inventory/export for the existing installed game.\n'
        'This build targets the previously supported DmIOS, not the new host objects.\n'
        'Replace the old plugin, sign through LiveContainer and restart the game.\n'
        'Long-press the overlay, open Build resources, inspect size, then export if desired.\n'
        'The exporter never uploads files; save/share the resulting ZIP yourself.\n'
        'No device test was performed by this build.\n')
    names = ['NXKeyboard.dylib', 'validation.json', 'README.txt']
    (out / 'SHA256SUMS.txt').write_text(''.join(
        hashlib.sha256((out / name).read_bytes()).hexdigest() + '  ' + name + '\n'
        for name in names))
    with zipfile.ZipFile(out / 'NXKeyboard-0.3.2-LiveContainer.zip', 'x',
                         compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names + ['SHA256SUMS.txt']:
            archive.write(out / name, name)
    obj.unlink()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
