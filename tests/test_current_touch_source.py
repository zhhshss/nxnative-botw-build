"""Current pinned source compatibility remains exact and transactional."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts import prepare_touch_upstream


@unittest.skipUnless(os.environ.get("BOTW_CI_STAGE"), "requires current prepared touch source")
class CurrentTouchSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ["BOTW_CI_STAGE"])
        if not (cls.source / "build-provenance/current-touch-compat.json").is_file():
            raise unittest.SkipTest("requires the current touch compatibility stage")
        cls.patch = prepare_touch_upstream.source.PATCH_ROOT / "current-touch-compat.patch"
        cls.manifest = json.loads(cls.patch.with_suffix(".json").read_text())
        spec = importlib.util.spec_from_file_location("current_source_diff", cls.source / "build-provenance/diffapply.py")
        cls.engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.engine)

    def fixture(self, root):
        for record in self.manifest["files"]:
            original = self.source / record["path"]
            self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), record["after_sha256"])
            path = root / record["path"]; path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, path)
        subprocess.run(["git", "apply", "--reverse", str(self.patch)], cwd=root, capture_output=True, check=True)
        (root / "build-provenance").mkdir()

    def test_current_patch_replays_against_the_actual_pinned_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            result = prepare_touch_upstream.apply_compatibility(root, self.engine)
            self.assertTrue(result["strict_replay_verified"])
            for record in self.manifest["files"]:
                self.assertEqual((root / record["path"]).read_bytes(), (self.source / record["path"]).read_bytes())

    def test_mixed_source_is_rejected_before_any_patch_file_is_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            path = root / "dm/ios/tools/build_ipa.sh"
            path.write_bytes(path.read_bytes() + b"\n# unrelated source\n")
            before = {r["path"]: (root / r["path"]).read_bytes() for r in self.manifest["files"]}
            with self.assertRaisesRegex(prepare_touch_upstream.source.PreparationError, "input differs"):
                prepare_touch_upstream.apply_compatibility(root, self.engine)
            self.assertEqual(before, {name: (root / name).read_bytes() for name in before})

    def test_legacy_importer_restoration_uses_only_the_reviewed_original_bytes(self):
        for record in self.manifest["legacy_files"]:
            self.assertEqual(hashlib.sha256((self.source / record["path"]).read_bytes()).hexdigest(), record["sha256"])


if __name__ == "__main__":
    unittest.main()
