#!/usr/bin/env python3
"""Exercise current launcher document pickers on a disposable iPad simulator."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import uuid
import zipfile


def run(command, **kwargs):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def check(stage, output):
    output.mkdir(parents=True)
    app = output / 'BOTWLauncherTest.app'
    tests = Path(__file__).resolve().parents[1] / 'tests/document_picker_ui'
    builder = (stage / 'dm/ios/tests/build_launcher_harness.sh').read_text()
    builder = builder.replace('ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"', 'ROOT=' + shlex.quote(str(stage)))
    builder = builder.replace('"$ROOT/dm/ios/Sources/Launcher.swift"', '"$ROOT/dm/ios/Sources/GameData.swift" "$ROOT/dm/ios/Sources/Launcher.swift"')
    local_builder = output / 'build-current-fixture.sh'
    local_builder.write_text(builder)
    run(['bash', local_builder, output])
    # The source has gained the legacy reader since this upstream fixture was written.
    # Recompile its production sources with that reader and the real cloud bridge.
    sdk = subprocess.check_output(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path'], text=True).strip()
    run(['xcrun', '--sdk', 'iphonesimulator', 'swiftc', '-swift-version', '5', '-parse-as-library',
         '-target', 'arm64-apple-ios26.0-simulator', '-sdk', sdk, '-I', stage / 'dm/host',
         '-module-cache-path', output / 'module-cache', '-o', app / 'BOTWLauncherTest',
         '-import-objc-header', stage / 'dm/ios/Sources/DmIOS-Bridging-Header.h',
         output / 'CloudSaves.o', output / 'GraphicsPack.o', output / 'GameImageImport.o', output / 'resources.o',
         '-larchive', *[stage / ('dm/ios/Sources/' + name) for name in
                       ('LauncherRow.swift', 'LauncherSettings.swift', 'BotwTouchSettings.swift', 'GameData.swift', 'Launcher.swift')],
         tests / 'PickerHarnessApp.swift'])
    run(['plutil', '-replace', 'UIDeviceFamily', '-json', '[2]', app / 'Info.plist'])
    run(['plutil', '-insert', 'UIFileSharingEnabled', '-bool', 'YES', app / 'Info.plist'])
    run(['plutil', '-insert', 'LSSupportsOpeningDocumentsInPlace', '-bool', 'YES', app / 'Info.plist'])
    shutil.rmtree(app / 'msl')
    fixtures = app / 'picker-fixtures'
    fixtures.mkdir()
    romfs = output / 'romfs-fixture'
    romfs.mkdir()
    (romfs / 'marker').write_bytes(b'picker fixture')
    run(['python3', stage / 'dm/ios/tools/game_data.py', fixtures / 'Picker-ROM.nxrom',
         '--romfs', romfs, '--title-id', '01007EF00011E000', '--version', '1.9.0', '--marker', 'marker',
         '--metadata', app / 'dm/game-data.json', '--stamp', output / 'game-data.stamp'])
    run(['python3', stage / 'dm/tools/romfs_image.py', romfs, fixtures / 'Picker-Image.img'])
    required = {'schema': 1, 'series': '1-picker-fixture', 'minimum_version': 1, 'external': True,
                'compatibility': {'game': '01007EF00011E000', 'build': 'v1114112', 'platform': 'ios',
                                  'api': 'nvn', 'chain': 'ca8b9c5b4e34', 'library': 'picker', 'contract': 'fixture'}}
    (app / 'dm/graphics-required.json').write_text(json.dumps(required))
    payloads = {'msl/picker/one.metallib': b'MTLB picker fixture', 'tables/shader_map.tsv': b'fixture'}
    files = [{'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in sorted(payloads.items())]
    compilers = {'records': [], 'metal_versions': [], 'targets': [], 'minimum_os': '26.0'}
    compiler = hashlib.sha256(json.dumps(compilers, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    identity = 'nxnative-graphics-v1\n1-picker-fixture\n1\n' + compiler + '\n'
    identity += ''.join(f"{r['path']}\t{r['size']}\t{r['sha256']}\n" for r in files)
    manifest = {'schema': 1, 'kind': 'graphics', 'series': required['series'], 'version': 1,
                'compatibility': required['compatibility'], 'compilers': compilers,
                'compiler_id': compiler, 'id': hashlib.sha256(identity.encode()).hexdigest(), 'files': files}
    with zipfile.ZipFile(fixtures / 'Picker-Graphics.zip', 'w', compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, sort_keys=True, separators=(',', ':')))
        for name, data in payloads.items(): archive.writestr(name, data)
    (fixtures / 'Picker-Wrong.txt').write_text('not a resource package')
    run(['codesign', '--force', '--sign', '-', app])
    devices = json.loads(subprocess.check_output(['xcrun', 'simctl', 'list', 'devicetypes', '--json'], text=True))['devicetypes']
    runtimes = json.loads(subprocess.check_output(['xcrun', 'simctl', 'list', 'runtimes', '--json'], text=True))['runtimes']
    runtime = max((r for r in runtimes if r.get('isAvailable') and 'iOS' in r['name']), key=lambda r: tuple(map(int, r['version'].split('.'))))
    available = json.loads(subprocess.check_output(['xcrun', 'simctl', 'list', 'devices', 'available', '--json'], text=True))['devices'].get(runtime['identifier'], [])
    existing = next((d for d in available if 'iPad' in d['name'] and d.get('deviceTypeIdentifier')), None)
    if existing:
        device = next(d for d in devices if d['identifier'] == existing['deviceTypeIdentifier'])
    else:
        compatible = {d['identifier'] for d in runtime.get('supportedDeviceTypes', [])}
        candidates = [d for d in devices if 'iPad' in d['name'] and d['identifier'] in compatible]
        if not candidates:
            raise ValueError('No iPad simulator type supports the installed iOS runtime')
        device = next((d for d in candidates if 'Pro' in d['name']), candidates[0])
    identifier = subprocess.check_output(['xcrun', 'simctl', 'create', 'BOTWPicker-' + uuid.uuid4().hex[:10],
                                          device['identifier'], runtime['identifier']], text=True).strip()
    try:
        run(['xcrun', 'simctl', 'boot', identifier])
        run(['xcrun', 'simctl', 'bootstatus', identifier, '-b'])
        run(['xcrun', 'simctl', 'install', identifier, app])
        project = output / 'ui-tests'
        shutil.copytree(tests, project)
        run(['xcodegen', 'generate', '--spec', project / 'project.yml', '--project', project])
        result = output / 'picker-tests.xcresult'
        with (output / 'ui-tests.log').open('w') as log:
            tested = subprocess.run(['xcodebuild', '-project', str(project / 'PickerTests.xcodeproj'), '-scheme', 'PickerTests',
                                     '-destination', 'platform=iOS Simulator,id=' + identifier,
                                     '-derivedDataPath', str(output / 'derived'), '-resultBundlePath', str(result),
                                     'test'], stdout=log, stderr=subprocess.STDOUT, timeout=900)
        print((output / 'ui-tests.log').read_text()[-10000:], flush=True)
        run(['xcrun', 'simctl', 'io', identifier, 'screenshot', output / 'picker-screen.png'])
        data = subprocess.check_output(['xcrun', 'simctl', 'get_app_container', identifier,
                                        'org.nxnative.botwlaunchertest', 'data'], text=True).strip()
        diagnostics = Path(data) / 'Documents/picker-diagnostics.json'
        if diagnostics.is_file(): shutil.copyfile(diagnostics, output / diagnostics.name)
        run(['xcrun', 'xcresulttool', 'export', 'attachments', '--path', result, '--output-path', output / 'attachments'])
        tested.check_returncode()
    finally:
        subprocess.run(['xcrun', 'simctl', 'shutdown', identifier], capture_output=True)
        subprocess.run(['xcrun', 'simctl', 'delete', identifier], capture_output=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    check(args.stage.resolve(), args.output.resolve())
