#!/usr/bin/env python3
"""Run source-only BOTW host and real UIKit-handler regressions on a CI Mac.

Creates a disposable iOS simulator. No game files or physical devices are used.
The target touch source tree must already include the reviewed local patches.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import uuid

GAME = '01007EF00011E000'
HOST_TESTS = (
    't_aspect', 't_look', 't_botw_touchhud', 't_botw_touch_actions',
    't_botw_touch_menu', 't_botw_touch_trackpad', 't_touchpad_geometry',
    't_gate_obs_result', 't_gate_reentry', 't_hid', 't_haptics', 't_device_motion',
)


def run(command, timeout=600, **kwargs):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    # Bound each stage independently so an unresponsive test or first simulator
    # boot identifies itself in the log instead of consuming the whole CI job.
    process = subprocess.Popen(list(map(str, command)), start_new_session=True, **kwargs)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)


def simctl_json(*arguments):
    return json.loads(subprocess.check_output(['xcrun', 'simctl', *arguments, '--json'], text=True))


def check(stage: Path, output: Path):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('These UIKit and guest-gate checks require an Apple-silicon Mac')
    if output.exists() or output.is_symlink():
        raise ValueError('Use a new output directory')
    if output.is_relative_to(stage):
        raise ValueError('Keep test output outside the source stage')
    output.mkdir(parents=True)
    unit = output / 'host'
    targets = [unit / name for name in HOST_TESTS]
    run(['make', '-C', stage / 'dm/host/tests', f'GAME={GAME}', f'B={unit}', '-j3', *targets])
    test_environment = dict(os.environ, TMPDIR=str(unit))
    for target in targets:
        log_path = output / (target.name + '.log')
        try:
            with log_path.open('w') as log:
                run([target], stdout=log, stderr=subprocess.STDOUT,
                    env=test_environment, timeout=120)
        finally:
            print(log_path.read_text(errors='replace'), flush=True)
    runtimes = [runtime for runtime in simctl_json('list', 'runtimes')['runtimes']
                if runtime.get('isAvailable') and 'iOS' in runtime['name']
                and int(runtime['version'].split('.')[0]) >= 26]
    if not runtimes:
        raise ValueError('Install an iOS 26 or newer simulator runtime for the UIKit regression')
    runtime = max(runtimes, key=lambda entry: tuple(map(int, entry['version'].split('.'))))
    # An existing compatible iPhone identifies a device type without guessing a
    # model that the selected runtime cannot boot. The existing device is unused.
    available = simctl_json('list', 'devices', 'available')['devices'].get(runtime['identifier'], [])
    device = next((item for item in available if item.get('deviceTypeIdentifier')
                   and 'iPhone' in item['name']), None)
    if not device:
        raise ValueError('No compatible iPhone simulator type is present in the CI image')
    identifier = subprocess.check_output([
        'xcrun', 'simctl', 'create', 'NXKTouchCI-' + uuid.uuid4().hex[:12],
        device['deviceTypeIdentifier'], runtime['identifier'],
    ], text=True).strip()
    environment = dict(os.environ, SIMULATOR_UDID=identifier)
    environment.pop('SIMULATOR_DEVICE_SET', None)
    test_failed = False
    try:
        run(['bash', stage / 'dm/ios/tests/run_look_harness.sh', output / 'uikit'], env=environment)
    except BaseException:
        test_failed = True
        raise
    finally:
        subprocess.run(['xcrun', 'simctl', 'shutdown', identifier], capture_output=True)
        deleted = subprocess.run(['xcrun', 'simctl', 'delete', identifier], capture_output=True, text=True)
        if deleted.returncode:
            print('Simulator cleanup failed: ' + deleted.stderr, flush=True)
            if not test_failed:
                deleted.check_returncode()
    report = {'host_tests': list(HOST_TESTS), 'uikit_handlers': 'passed',
              'simulator_runtime': runtime['identifier'], 'game_data_used': False,
              'physical_device_tested': False}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        check(args.stage.resolve(), args.output.resolve())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
