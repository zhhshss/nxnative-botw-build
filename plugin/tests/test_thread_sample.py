#!/usr/bin/env python3
"""Exercise the real capture header with host-side simulated Mach responses.

Only platform includes are removed from a temporary copy. The implementation,
including selection and cleanup control flow, is compiled without modification.
This verifies those contracts; it does not emulate kernel scheduling or establish
that register sampling works on a particular iOS device.
"""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


class ThreadCaptureTests(unittest.TestCase):
    def test_capture_control_flow(self):
        tests = Path(__file__).resolve().parent
        header = tests.parent / "src" / "NXKThreadSample.h"
        compiler = shlex.split(os.environ.get("CC", "cc"))
        self.assertTrue(compiler and shutil.which(compiler[0]), "a C compiler is required (CC)")
        common = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"]

        with tempfile.TemporaryDirectory(prefix="nxk-thread-test-") as directory:
            work = Path(directory)
            implementation = "\n".join(
                line for line in header.read_text().splitlines()
                if not line.lstrip().startswith("#include <mach/")
            )
            (work / "NXKThreadSample-under-test.h").write_text(implementation + "\n")
            source = work / "thread_sample_mock.c"
            shutil.copyfile(tests / "thread_sample_mock.c", source)

            # Test sanitizer compilation and runtime independently so unsupported
            # toolchains can still execute the assertions. A failure of the real
            # sanitized test is never retried without sanitizers.
            probe_source = work / "sanitizer_probe.c"
            probe_source.write_text("int main(void) { return 0; }\n")
            probe_binary = work / "sanitizer_probe"
            sanitizers = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
            probe = subprocess.run(
                compiler + common + sanitizers + [str(probe_source), "-o", str(probe_binary)],
                capture_output=True, text=True, timeout=30,
            )
            if probe.returncode == 0:
                probe = subprocess.run(
                    [str(probe_binary)], capture_output=True, text=True, timeout=10,
                )
            if probe.returncode != 0:
                sanitizers = []
                print("ASan/UBSan unavailable; running capture assertions without sanitizers.")

            binary = work / "thread_sample_test"
            subprocess.run(
                compiler + common + sanitizers + [str(source), "-o", str(binary)],
                check=True, timeout=30,
            )
            subprocess.run([str(binary)], check=True, timeout=10)
            if sanitizers:
                print("Capture control-flow checks ran with ASan and UBSan.")


if __name__ == "__main__":
    unittest.main()
