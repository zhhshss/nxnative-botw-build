"""Strict source replay and unchanged compiled-shader input identity."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts import prepare_source_tree, shader_cache


@unittest.skipUnless(os.environ.get("BOTW_CI_STAGE"), "requires the prepared render-backport source tree")
class RenderBackportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ["BOTW_CI_STAGE"])
        cls.patch = prepare_source_tree.PATCH_ROOT / "touchwild-render-pass-20261101.patch"
        cls.manifest = json.loads(cls.patch.with_suffix(".json").read_text())
        spec = importlib.util.spec_from_file_location("render_test_diffapply", cls.source / "webui/diffapply.py")
        cls.engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.engine)

    def fixture(self, root, lite):
        included = [r for r in self.manifest["files"]
                    if not lite or prepare_source_tree.keep_source(r["path"], "upstream", True)]
        for record in included:
            source = self.source / record["path"]
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), record["after_sha256"])
            target = root / record["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        command = ["git", "apply", "--reverse"]
        if lite:
            command.append("--exclude=docs/*")
        subprocess.run([*command, str(self.patch)], cwd=root, check=True, capture_output=True)
        (root / "build-provenance").mkdir()
        return included

    def test_actual_backport_replays_in_full_and_lite_stages(self):
        full_available = all((self.source / r["path"]).is_file() for r in self.manifest["files"])
        for lite in ((False, True) if full_available else (True,)):
            with self.subTest(lite=lite), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                included = self.fixture(root, lite)
                result = prepare_source_tree.apply_render_patch(root, self.engine, lite)
                self.assertTrue(result["strict_replay_verified"])
                for record in included:
                    self.assertEqual((root / record["path"]).read_bytes(), (self.source / record["path"]).read_bytes())
                self.assertEqual((root / "build-provenance" / self.patch.name).read_bytes(), self.patch.read_bytes())

    def test_one_changed_input_rejects_the_whole_backport_before_any_file_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            included = self.fixture(root, True)
            changed = root / "dm/gfx/nvn/nvn_geometry.inc"
            changed.write_bytes(changed.read_bytes() + b"\n// unrelated edit\n")
            before = {r["path"]: (root / r["path"]).read_bytes() for r in included}
            with self.assertRaisesRegex(prepare_source_tree.PreparationError, "input differs"):
                prepare_source_tree.apply_render_patch(root, self.engine, True)
            self.assertEqual({name: (root / name).read_bytes() for name in before}, before)
            self.assertFalse((root / "build-provenance" / self.patch.name).exists())

    def test_backport_preserves_every_compiled_shader_cache_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "synthetic.nsp").write_bytes(b"synthetic game input")
            records = shader_cache.input_records(self.source, root)
        tracked = {r.split(b"\0", 1)[0].decode() for r in records}
        changed = {r["path"] for r in self.manifest["files"]}
        self.assertFalse(tracked & changed, "an actual shader input change requires a new authenticated cache")
        self.assertEqual(self.manifest["shader_chain"], "715bb867230c")


if __name__ == "__main__":
    unittest.main()
