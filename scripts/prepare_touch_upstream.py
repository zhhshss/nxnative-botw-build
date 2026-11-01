#!/usr/bin/env python3
"""Prepare the current iOS touch source and verified legacy-data compatibility."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile

try:
    from . import prepare_source_tree as source
except ImportError:
    import prepare_source_tree as source

TOUCH_PIN = "c671dd0b27f4ee4691d26e86dd7ad9de58c9f577"
LEGACY_PIN = "e1b4196c589735c8b74cfe1f555167a6f380ae88"
FORK_PIN = "d1ba3e97876da86ff616c6d8e8d684b968fb8c5d"
CHAIN = "ca8b9c5b4e34"


def apply_compatibility(stage: Path, engine) -> dict:
    patch_path = source.PATCH_ROOT / "current-touch-compat.patch"
    manifest_path = patch_path.with_suffix(".json")
    patch, manifest_bytes = patch_path.read_bytes(), manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if (manifest["source_commit"] != TOUCH_PIN or manifest["legacy_source_commit"] != LEGACY_PIN or
            manifest["shader_chain"] != CHAIN or manifest["patch_sha256"] != source.digest(patch)):
        raise source.PreparationError("Current touch compatibility patch identity mismatch")
    patches = {}
    for chunk in re.split(rb"(?=^diff --git )", patch, flags=re.MULTILINE):
        if not chunk.strip():
            continue
        target, _ = engine._parse(chunk, None)
        source.safe_relative(target)
        if target in patches:
            raise source.PreparationError("Duplicate current touch patch target: " + target)
        patches[target] = chunk
    records = manifest["files"]
    if len({r["path"] for r in records}) != len(records) or set(patches) != {r["path"] for r in records}:
        raise source.PreparationError("Current touch patch and manifest target sets differ")
    pending = []
    for record in records:
        target = source.safe_relative(record["path"])
        path = stage / target
        before = path.read_bytes()
        if source.digest(before) != record["before_sha256"]:
            raise source.PreparationError("Current touch patch input differs: " + target)
        after = engine.apply_bytes(before, patches[target], target)
        if source.digest(after) != record["after_sha256"]:
            raise source.PreparationError("Current touch patch result differs: " + target)
        pending.append((path, after))
    for path, after in pending:
        path.write_bytes(after)
    provenance = stage / "build-provenance"
    (provenance / patch_path.name).write_bytes(patch)
    (provenance / manifest_path.name).write_bytes(manifest_bytes)
    return {"patch": patch_path.name, "patch_sha256": source.digest(patch), "files": records,
            "manifest_sha256": source.digest(manifest_bytes), "strict_replay_verified": True}


def prepare(upstream: Path, legacy: Path, fork: Path, output: Path, lite=False):
    source.verify_pin(upstream, TOUCH_PIN)
    source.verify_pin(legacy, LEGACY_PIN)
    source.verify_pin(fork, FORK_PIN)
    if output.exists() or output.is_symlink():
        raise source.PreparationError("Use a new current-touch source output directory")
    entries = [row for row in source.tree_entries(upstream) if source.keep_source(row["path"], "upstream", lite)]
    source.source_sizes(upstream, entries)
    manifest = json.loads((source.PATCH_ROOT / "current-touch-compat.json").read_text())
    engine = source.load_diffapply(fork)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="current-touch-", dir=output.parent) as name:
        temporary = Path(name)
        copied = source.copy_entries(upstream, entries, temporary)
        (temporary / "build-provenance").mkdir()
        restored = []
        for record in manifest["legacy_files"]:
            target = source.safe_relative(record["path"])
            path = temporary / target
            if path.exists():
                raise source.PreparationError("Legacy compatibility file unexpectedly exists: " + target)
            data = source.blob(legacy, target)
            if source.digest(data) != record["sha256"]:
                raise source.PreparationError("Legacy compatibility source differs: " + target)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            restored.append(record)
        compatibility = apply_compatibility(temporary, engine)
        header = temporary / "dm/gfx/nvn/dmsh_chain.h"
        if f'#define DMSH_CHAIN "{CHAIN}"' not in header.read_text():
            raise source.PreparationError("The current source has an unexpected shader chain")
        (temporary / "build-provenance/diffapply.py").write_bytes(source.blob(fork, "webui/diffapply.py"))
        provenance = {"schema_version": 3, "status": "prepared_current_touch_ios", "lite": lite,
                      "upstream": {"repository": "devinhurry/touch-the-wild", "commit": TOUCH_PIN, "shader_chain": CHAIN},
                      "legacy_data": {"source_commit": LEGACY_PIN, "files": restored, "format": "NXROM001"},
                      "compatibility_patch": compatibility, "copied": copied,
                      "limitations": {"game_data_included": False, "device_tested": False, "shader_binaries_included": False}}
        (temporary / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        os.replace(temporary, output)
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--legacy-source", type=Path, required=True)
    parser.add_argument("--fork", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lite", action="store_true")
    args = parser.parse_args()
    try:
        result = prepare(args.upstream.resolve(), args.legacy_source.resolve(), args.fork.resolve(), args.output.resolve(), args.lite)
    except (OSError, ValueError, source.PreparationError) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps({"commit": result["upstream"]["commit"], "chain": CHAIN, "source_files": result["copied"]["files"],
                      "legacy_data": "retained NXROM import", "strict_replay_verified": True}))


if __name__ == "__main__":
    main()
