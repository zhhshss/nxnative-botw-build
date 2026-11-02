#!/usr/bin/env python3
"""Compile and check the render backport without game files on a CI Mac."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess

GAME = "01007EF00011E000"
GPU_TARGETS = ("t_feedback", "t_rt_range", "t_unwritten_store", "t_framegen", "t_framegen_motion")


def run(command, log: Path, env=None, timeout=600):
    print("+ " + " ".join(map(str, command)), flush=True)
    with log.open("w") as output:
        result = subprocess.run(list(map(str, command)), stdout=output,
                                stderr=subprocess.STDOUT, env=env, timeout=timeout)
    if result.returncode:
        print(log.read_text(errors="replace")[-6000:], flush=True)
        result.check_returncode()


def check(stage: Path, output: Path):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("These render checks require an Apple-silicon Mac")
    if output.exists() or output.is_symlink() or output.is_relative_to(stage):
        raise ValueError("Use a new output directory outside the source stage")
    output.mkdir(parents=True)
    tests = stage / "dm/gfx/tests"
    run(["make", "-C", tests, f"GAME={GAME}", "-j2", *GPU_TARGETS, "t_framegen_step", "t_feedback_stats_tsan"], output / "compile.log")
    run([tests / "t_framegen_step"], output / "framegen-step.log", timeout=60)
    environment = dict(os.environ, DM_TEST_FEEDBACK_STATS="1")
    run([tests / "t_feedback"], output / "feedback-stats.log", environment, timeout=60)
    environment["TSAN_OPTIONS"] = "halt_on_error=1"
    run([tests / "t_feedback_stats_tsan"], output / "feedback-stats-tsan.log", environment, timeout=120)
    probe = output / "metal-probe.m"
    probe.write_text('#import <Foundation/Foundation.h>\n#import <Metal/Metal.h>\n'
                     'int main(void) { @autoreleasepool { id<MTLDevice> d = MTLCreateSystemDefaultDevice();\n'
                     'NSDictionary *r = @{ @"device": d.name ?: @"none", @"metal4": @(d && [d supportsFamily:MTLGPUFamilyMetal4]) };\n'
                     'NSData *v = [NSJSONSerialization dataWithJSONObject:r options:0 error:nil];\n'
                     'fwrite(v.bytes, 1, v.length, stdout); return 0; } }\n')
    binary = output / "metal-probe"
    run(["clang", "-fobjc-arc", probe, "-framework", "Metal", "-framework", "Foundation", "-o", binary], output / "probe-compile.log")
    capability = json.loads(subprocess.check_output([binary], text=True))
    if capability["metal4"]:
        for target in GPU_TARGETS:
            run(["make", "-C", tests, f"GAME={GAME}", "check_" + target], output / (target + ".log"))
        gpu_result = "passed"
    else:
        gpu_result = "unavailable: runner has no Metal 4 GPU"
    report = {"result": "compiled and CPU concurrency checks passed", "gpu_tests": gpu_result,
              "capability": capability, "targets": list(GPU_TARGETS),
              "thread_sanitizer": "passed", "game_data_used": False, "physical_device_tested": False}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        check(args.stage.resolve(), args.output.resolve())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
