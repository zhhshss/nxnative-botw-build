import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


@unittest.skipUnless(os.environ.get("BOTW_CI_STAGE"), "requires the prepared --ipa-ci source tree")
class PreparedIPATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ["BOTW_CI_STAGE"])
        sys.path.insert(0, str(cls.source / "dm/tools"))
        sys.path.insert(0, str(cls.source / "dm/games"))
        spec = importlib.util.spec_from_file_location("ipa_ci_test_builder", cls.source / "scripts/build_ipa_from_nsp.py")
        cls.builder = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.builder
        spec.loader.exec_module(cls.builder)

    def test_reclaim_requires_explicit_ci_scope_and_keeps_extracted_files(self):
        m = self.builder
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); nsp = root / "nsp"; nsp.mkdir()
            original = root / "original.nsp"; original.write_bytes(b"original")
            copies = [nsp / name for name in ("base.nsp", "update.NSP", "dlc.nsz", "other.xci")]
            for p in copies: p.write_bytes(b"downloaded copy")
            extracted = nsp / "01007EF00011E000_build/extract/romfs.img"
            extracted.parent.mkdir(parents=True); extracted.write_bytes(b"extracted")
            self.assertEqual(m.reclaim_ci_inputs(nsp, {}), 0)
            for env in ({"DM_CI_RECLAIM_INPUTS": "1", "RUNNER_TEMP": str(root)},
                        {"DM_CI_RECLAIM_INPUTS": "1", "GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(root / "wrong")}):
                with self.assertRaises(m.BuildError): m.reclaim_ci_inputs(nsp, env)
                self.assertTrue(all(p.exists() for p in copies))
            self.assertEqual(m.reclaim_ci_inputs(nsp, {"DM_CI_RECLAIM_INPUTS": "1", "GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(root)}), 4)
            self.assertFalse(any(p.exists() for p in copies))
            self.assertEqual(original.read_bytes(), b"original")
            self.assertEqual(extracted.read_bytes(), b"extracted")

    def test_shader_reuse_survives_new_checkout_times_but_rebuilds_changed_binaries(self):
        m = self.builder
        import game
        g = game.load("01007EF00011E000")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); sh, logs = root / "shader", root / "logs"
            sh.mkdir(); logs.mkdir()
            xlate = root / "translator/dmsh_xlate"
            xlate.parent.mkdir(); xlate.write_bytes(b"translator v1")
            chain = m.chain_of(self.source)
            calls = []
            def compile_shaders(argv, log, env=None, **kwargs):
                calls.append(argv)
                lib = Path(env["IOSOUT"]) / f"b-{chain}"
                lib.mkdir(parents=True, exist_ok=True)
                (lib / "one_b.metallib").write_bytes(b"compiled")
                log.write_text("shaders 1: ok 1\n")
            previous = {name: getattr(m, name) for name in ("run", "chain_tables", "toolchain")}
            try:
                m.run = compile_shaders
                m.chain_tables = lambda *args: True
                m.toolchain = lambda *args: ["unchanged Metal compiler"]
                env = {"WORK": str(root / "scratch")}
                m.build_shaders(sys.executable, self.source, sh, xlate, env, logs, g, "same shader package")
                self.assertEqual(len(calls), 1)
                os.utime(xlate, ns=(1_700_000_000_000_000_000,) * 2)
                m.build_shaders(sys.executable, self.source, sh, xlate, env, logs, g, "same shader package")
                self.assertEqual(len(calls), 1, "a new checkout timestamp must not recompile identical shader inputs")
                xlate.write_bytes(b"translator v2")
                m.build_shaders(sys.executable, self.source, sh, xlate, env, logs, g, "same shader package")
                self.assertEqual(len(calls), 2, "changed translator contents must invalidate the cached build")
            finally:
                for name, value in previous.items(): setattr(m, name, value)


if __name__ == "__main__":
    unittest.main()
