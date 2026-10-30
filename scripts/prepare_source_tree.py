#!/usr/bin/env python3
"""Prepare pinned public host sources; never read game inputs or build an IPA.

The output combines Touch the Wild, a reviewed seedds merge, and xbunax's
Windows hooks and ObjC launcher. Source bytes come from pinned Git blobs and
explicit, hashed patch manifests, not untracked source working files.
Only exact, uniquely located hunk context is accepted; no fuzzy application.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import types

UPSTREAM_PIN = "e1b4196c589735c8b74cfe1f555167a6f380ae88"
MERGED_UPSTREAM_PIN = "fe6d3dfadb899e2a3525ea7a1d85a4255ca4e8a3"
FORK_PIN = "d1ba3e97876da86ff616c6d8e8d684b968fb8c5d"
UPSTREAM_CHAIN = "715bb867230c"
FORK_BASE = "3950976088c066cc9e40f33ee244404d2031e163"
GAME = "01007EF00011E000"
SYNTHETIC_PROFILE = "010015100B514000"
SWIFT_REMOVED = tuple("dm/ios/Sources/" + name for name in (
    "Launcher.swift", "LauncherRow.swift", "LauncherSettings.swift"))
OVERLAY = "dm/ios/Sources/DmLauncher.m"
PATCH_ROOT = Path(__file__).resolve().parents[1] / "patches"
WINDOWS_ADAPTED = {
    "0002-gen-embed-posix-paths-sections.patch": "windows-0002-gen-embed.patch",
    "0005-build-ipa-from-nsp-webui.patch": "windows-0005-build-ipa.patch",
}
SWIFT_UNCOMPILED = tuple("dm/ios/Sources/" + name for name in (
    "BotwTouchSettings.swift", "GameData.swift", "GameDataImport.swift"))
ROOT_FILES = {"LICENSE", "AGENTS.md", "README.md", "CONTRIBUTING.md",
              "pyproject.toml", "Makefile", ".gitattributes"}
FORK_ROOT_FILES = {"FORK.md", "start-webui.sh", "start-webui.command", "start-webui.bat"}
EXCLUDED_PARTS = {".git", ".webui-data", "__pycache__", "node_modules", "romfs", "RomFS", "ExeFS"}
EXCLUDED_SUFFIXES = {".nsp", ".nsz", ".xci", ".xcz", ".nso", ".nro", ".nca",
                     ".keys", ".ipa", ".metallib", ".air", ".dll", ".exe", ".pdb",
                     ".dylib", ".a", ".o", ".obj", ".pyc", ".zip", ".7z"}


class PreparationError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> bytes:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if p.returncode:
        raise PreparationError(f"git {args[0]} failed: {p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout


def verify_pin(repo: Path, wanted: str) -> None:
    actual = git(repo, "rev-parse", "HEAD").decode().strip()
    if actual != wanted:
        raise PreparationError(f"Unexpected HEAD in {repo}: {actual}; require {wanted}")


def safe_relative(name: str) -> str:
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or ".." in p.parts or "\\" in name or
            any(c in name for c in "\r\n\0") or str(p) != name):
        raise PreparationError(f"Unsafe source path: {name!r}")
    return name


def tree_entries(repo: Path):
    # Do not ask ls-tree for sizes: in a filtered clone that can fetch every
    # game profile and bundled tool before the source-only filter runs.
    for row in git(repo, "ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if not row:
            continue
        metadata, raw_name = row.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        name = safe_relative(raw_name.decode("utf-8"))
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            raise PreparationError(f"Unsupported Git entry {mode.decode()} {name}; refusing links/submodules")
        yield {"path": name, "mode": int(mode, 8), "oid": oid.decode()}


def source_sizes(repo: Path, entries: list[dict]) -> None:
    """Fetch only selected public source blobs, in one partial-clone request."""
    missing = {row[1:].decode() for row in git(repo, "rev-list", "--objects",
               "--no-object-names", "--missing=print", "HEAD").splitlines() if row.startswith(b"?")}
    needed = sorted({entry["oid"] for entry in entries} & missing)
    if needed:
        proc = subprocess.run(["git", "-C", str(repo), "-c", "fetch.negotiationAlgorithm=noop",
            "fetch", "--no-tags", "--no-write-fetch-head", "--recurse-submodules=no",
            "--filter=blob:none", "--stdin", "origin"],
            input=("\n".join(needed) + "\n").encode(), capture_output=True)
        if proc.returncode:
            raise PreparationError("Cannot fetch selected pinned source blobs: " + proc.stderr.decode("utf-8", "replace"))
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check"],
        input=b"".join(entry["oid"].encode() + b"\n" for entry in entries), capture_output=True)
    if proc.returncode:
        raise PreparationError("Cannot inspect selected source blob sizes")
    rows = proc.stdout.splitlines()
    if len(rows) != len(entries):
        raise PreparationError("Source blob size inventory is incomplete")
    for entry, row in zip(entries, rows):
        fields = row.split()
        if len(fields) != 3 or fields[0].decode() != entry["oid"] or fields[1] != b"blob":
            raise PreparationError(f"Invalid pinned source blob: {entry['path']}")
        entry["size"] = int(fields[2])


def keep_source(name: str, origin: str, lite: bool) -> bool:
    p = PurePosixPath(name)
    if any(part in EXCLUDED_PARTS for part in p.parts) or p.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if origin == "fork":
        return name.startswith("webui/") or name in FORK_ROOT_FILES
    if name.startswith(("dm/gfx/tools/dmsh_xlate_bin/", "dm/gfx/tools/spirv_cross_bin/")):
        return p.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
    if name.startswith(("docs/images/", "dm/tools/fuzz/corpus/")):
        return False
    if name in ROOT_FILES:
        return True
    if not name.startswith(("dm/", "scripts/", "docs/")):
        return False
    if lite and name.startswith("docs/"):
        return name == "docs/launcher.schema.json"
    if lite and name.startswith("dm/games/"):
        parts = p.parts
        if len(parts) >= 4 and re.fullmatch(r"[0-9A-F]{16}", parts[2]):
            return parts[2] in (GAME, SYNTHETIC_PROFILE)
    return True


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob(repo: Path, path: str) -> bytes:
    return git(repo, "show", "HEAD:" + safe_relative(path))


def copy_entries(repo: Path, entries: list[dict], output: Path) -> dict:
    """Read immutable blobs through one process; no source symlinks are followed."""
    count = total = 0
    hashes = []
    proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None and proc.stdout is not None
    try:
        for entry in entries:
            proc.stdin.write(entry["oid"].encode() + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().strip().split()
            if len(header) != 3 or header[1] != b"blob" or header[0].decode() != entry["oid"]:
                raise PreparationError(f"Cannot read pinned blob: {entry['path']}")
            size = int(header[2])
            if size != entry["size"]:
                raise PreparationError(f"Blob size changed: {entry['path']}")
            data = proc.stdout.read(size)
            if len(data) != size or proc.stdout.read(1) != b"\n":
                raise PreparationError(f"Truncated blob: {entry['path']}")
            # The selected entry's object ID is itself a content identity.
            actual = hashlib.sha1(b"blob " + str(size).encode() + b"\0" + data).hexdigest()
            if actual != entry["oid"]:
                raise PreparationError(f"Blob identity mismatch: {entry['path']}")
            target = output / entry["path"]
            if target.exists():
                raise PreparationError(f"Two origins supplied {entry['path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if entry["mode"] & 0o111 else 0o644)
            count += 1
            total += size
            hashes.append(f"{entry['path']}\0{entry['oid']}\n")
        proc.stdin.close()
        if proc.wait() != 0:
            raise PreparationError("git cat-file failed")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return {"files": count, "bytes": total,
            "git_blob_manifest_sha256": digest("".join(hashes).encode())}


def load_diffapply(fork: Path):
    code = blob(fork, "webui/diffapply.py")
    module = types.ModuleType("pinned_fork_diffapply")
    exec(compile(code, "pinned-fork/webui/diffapply.py", "exec"), module.__dict__)
    return module


def relocate_exact(before: bytes, patch: bytes, target: str, engine) -> tuple[bytes, bytes, list[dict]]:
    """Relocate by the complete hunk text, then verify a strict regenerated diff.

    No whitespace relaxation, fuzz or three-way merge is accepted. Ambiguous
    context fails even if Git could guess a likely location.
    """
    parsed_target, hunks = engine._parse(patch, target)
    if parsed_target != target:
        raise PreparationError(f"Patch target mismatch: {target}")
    normalized = engine._normalize(before.decode("utf-8"))
    trailing = normalized.endswith("\n")
    lines = normalized.splitlines()
    result = []
    cursor = 0
    offsets = []
    for old_start, ops in hunks:
        old = [text for op, text, _ in ops if op in " -"]
        new = [text for op, text, _ in ops if op in " +"]
        if not old:
            raise PreparationError(f"Context-free hunk is not allowed: {target}")
        candidates = [i for i in range(cursor, len(lines) - len(old) + 1)
                      if lines[i:i + len(old)] == old]
        if len(candidates) != 1:
            raise PreparationError(f"Expected unique exact hunk in {target}, found {len(candidates)}")
        start = candidates[0]
        result.extend(lines[cursor:start])
        result.extend(new)
        cursor = start + len(old)
        offsets.append({"old_start": old_start, "new_context_start": start + 1,
                        "line_offset": start + 1 - old_start})
    result.extend(lines[cursor:])
    after_text = "\n".join(result) + ("\n" if trailing else "")
    regenerated = "".join(difflib.unified_diff(normalized.splitlines(keepends=True),
                         after_text.splitlines(keepends=True), fromfile="a/" + target,
                         tofile="b/" + target, n=3)).encode()
    if not regenerated:
        raise PreparationError(f"Patch made no change: {target}")
    after = after_text.encode()
    if engine.apply_bytes(before, regenerated, target) != after:
        raise PreparationError(f"Strict regenerated patch verification failed: {target}")
    return after, regenerated, offsets


def apply_upstream_merge(source: Path, stage: Path, lite: bool) -> dict:
    """Replay the reviewed merge exactly, including files omitted by --lite."""
    patch_path = PATCH_ROOT / "touchwild-upstream-20261030.patch"
    manifest_path = patch_path.with_suffix(".json")
    patch = patch_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if (manifest["touch_head"] != UPSTREAM_PIN or manifest["upstream_head"] != MERGED_UPSTREAM_PIN or
            manifest["patch_sha256"] != digest(patch)):
        raise PreparationError("Reviewed upstream merge identity mismatch")
    records = manifest["files"]
    seen = set()
    for record in records:
        target = safe_relative(record["path"])
        if target in seen:
            raise PreparationError(f"Duplicate reviewed merge path: {target}")
        seen.add(target)
        path = stage / target
        before = record["sources"]["touch"]
        if before is None:
            if path.exists():
                raise PreparationError(f"Reviewed addition already exists: {target}")
            continue
        data = path.read_bytes() if path.is_file() else blob(source, target)
        if digest(data) != before["sha256"]:
            raise PreparationError(f"Reviewed merge input differs: {target}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(int(before["mode"], 8) & 0o777)
    # A stage can be nested in the orchestration repository on Actions. Stop
    # discovery there so git apply always resolves paths relative to this stage.
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(stage.parent))
    for check in (True, False):
        # Git for Windows commonly enables core.autocrlf globally. Applying an
        # addition outside a repository must still preserve the reviewed LF
        # bytes; the orchestration checkout's attributes do not cover this stage.
        args = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                "apply", "--whitespace=nowarn"]
        if check:
            args.append("--check")
        proc = subprocess.run([*args, "--", str(patch_path)], cwd=stage, env=env, capture_output=True)
        if proc.returncode:
            raise PreparationError("Strict upstream merge replay failed: " + proc.stderr.decode("utf-8", "replace"))
    included = []
    for record in records:
        target = record["path"]
        path = stage / target
        result = record["result"]
        if result is None:
            if path.exists():
                raise PreparationError(f"Reviewed merge deletion failed: {target}")
        elif not path.is_file() or digest(path.read_bytes()) != result["sha256"]:
            raise PreparationError(f"Reviewed merge result differs: {target}")
        elif keep_source(target, "upstream", lite):
            path.chmod(int(result["mode"], 8) & 0o777)
            included.append(target)
        else:
            path.unlink()
    provenance = stage / "build-provenance"
    provenance.mkdir(exist_ok=True)
    (provenance / patch_path.name).write_bytes(patch)
    (provenance / manifest_path.name).write_bytes(manifest_bytes)
    return {"repository": "https://github.com/seedds/nxnative", "commit": MERGED_UPSTREAM_PIN,
            "common_base": manifest["common_base"], "patch_sha256": digest(patch),
            "manifest_sha256": digest(manifest_bytes), "reviewed_files": len(records),
            "excluded_upstream_files": manifest.get("excluded_upstream_files", []),
            "included_paths": included, "strict_replay_verified": True}


def apply_camera_patch(stage: Path, engine) -> dict:
    patch_path = PATCH_ROOT / "touchwild-camera-ownership.patch"
    manifest_path = patch_path.with_suffix(".json")
    patch = patch_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest["source_commit"] != UPSTREAM_PIN or manifest["patch_sha256"] != digest(patch):
        raise PreparationError("Reviewed camera patch identity mismatch")
    chunks = re.split(rb"(?=^diff --git )", patch, flags=re.MULTILINE)
    patches = {}
    for chunk in chunks:
        if not chunk.strip():
            continue
        target, _ = engine._parse(chunk, None)
        safe_relative(target)
        if target in patches:
            raise PreparationError(f"Duplicate camera patch target: {target}")
        patches[target] = chunk
    if set(patches) != {record["path"] for record in manifest["files"]}:
        raise PreparationError("Camera patch and manifest target sets differ")
    for record in manifest["files"]:
        target = safe_relative(record["path"])
        path = stage / target
        before = path.read_bytes()
        if digest(before) != record["before_sha256"]:
            raise PreparationError(f"Camera patch input differs: {target}")
        after = engine.apply_bytes(before, patches[target], target)
        if digest(after) != record["after_sha256"]:
            raise PreparationError(f"Camera patch result differs: {target}")
        path.write_bytes(after)
    provenance = stage / "build-provenance"
    (provenance / patch_path.name).write_bytes(patch)
    (provenance / manifest_path.name).write_bytes(manifest_bytes)
    return {"patch": patch_path.name, "patch_sha256": digest(patch),
            "manifest_sha256": digest(manifest_bytes), "files": manifest["files"],
            "strict_replay_verified": True, "device_tested": False}


def prepare(upstream: Path, fork: Path, output: Path, lite: bool) -> dict:
    verify_pin(upstream, UPSTREAM_PIN)
    verify_pin(fork, FORK_PIN)
    if output.exists() or output.is_symlink():
        raise PreparationError(f"Output must not exist: {output}")
    # Resolve existing parent symlinks before the source-tree containment check.
    output = output.resolve()
    for source in (upstream, fork):
        if output == source or source in output.parents:
            raise PreparationError("Output must be outside both source repositories")
    chain = blob(upstream, "dm/gfx/nvn/dmsh_chain.h").decode()
    if f'#define DMSH_CHAIN "{UPSTREAM_CHAIN}"' not in chain:
        raise PreparationError("Pinned upstream has an unexpected shader chain")
    base_metadata = blob(fork, "webui/patches/UPSTREAM_BASE").decode()
    if f"base {FORK_BASE}\n" not in base_metadata:
        raise PreparationError("Unexpected fork patch base")
    selected = {
        "upstream": [e for e in tree_entries(upstream) if keep_source(e["path"], "upstream", lite)],
        "fork": [e for e in tree_entries(fork) if keep_source(e["path"], "fork", lite)],
    }
    source_sizes(upstream, selected["upstream"])
    source_sizes(fork, selected["fork"])
    total = sum(e["size"] for entries in selected.values() for e in entries)
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < total + 32 * 1024 * 1024:
        raise PreparationError(f"Need at least {total + 32 * 1024 * 1024} free bytes for this source stage")
    temporary = Path(tempfile.mkdtemp(prefix=".nxk-prepare-", dir=output.parent))
    try:
        copied = {"upstream": copy_entries(upstream, selected["upstream"], temporary),
                  "fork": copy_entries(fork, selected["fork"], temporary)}
        upstream_merge = apply_upstream_merge(upstream, temporary, lite)
        engine = load_diffapply(fork)
        records = []
        series = blob(fork, "webui/patches/series").decode().splitlines()
        patch_dir = temporary / "build-provenance/patches"
        patch_dir.mkdir(parents=True)
        for row in series:
            if not row.strip() or row.lstrip().startswith("#"):
                continue
            fields = row.split()
            if len(fields) != 2:
                raise PreparationError("Malformed pinned patch series")
            name, target = map(safe_relative, fields)
            if "/" in name or not target.startswith(("dm/", "scripts/")):
                raise PreparationError("Patch outside the reviewed source scope")
            patch = blob(fork, "webui/patches/" + name)
            before = (temporary / target).read_bytes()
            if target in SWIFT_REMOVED:
                records.append({"name": name, "target": target,
                    "source_patch_sha256": digest(patch), "before_sha256": digest(before),
                    "status": "superseded_by_objc_overlay",
                    "reason": "The touch fork has evolved Swift localization and settings; this Windows stage uses the pinned ObjC launcher instead.",
                    "removed_by_objc_overlay": True})
                continue
            adapted = PATCH_ROOT / WINDOWS_ADAPTED[name] if name in WINDOWS_ADAPTED else None
            effective = adapted.read_bytes() if adapted else patch
            after, regenerated, offsets = relocate_exact(before, effective, target, engine)
            (temporary / target).write_bytes(after)
            (patch_dir / name).write_bytes(regenerated)
            records.append({"name": name, "target": target, "source_patch_sha256": digest(patch),
                            "relocated_patch_sha256": digest(regenerated), "before_sha256": digest(before),
                            "after_sha256": digest(after), "hunks": offsets,
                            "status": "applied", "reviewed_adapter": adapted.name if adapted else None,
                            "effective_patch_sha256": digest(effective),
                            "strict_replay_verified": True, "removed_by_objc_overlay": target in SWIFT_REMOVED})
        if len(records) != 9:
            raise PreparationError("Expected exactly the nine reviewed fork hooks")
        camera_patch = apply_camera_patch(temporary, engine)
        removed = []
        for target in SWIFT_REMOVED:
            path = temporary / target
            removed.append({"path": target, "patched_sha256": digest(path.read_bytes())})
            path.unlink()
        overlay = blob(fork, "webui/overlays/" + OVERLAY)
        (temporary / OVERLAY).write_bytes(overlay)
        project = temporary / "dm/ios/project.yml"
        old = project.read_text()
        original = '        UILaunchStoryboardName: ""'
        desired = "        UILaunchScreen: {}"
        if old.count(original) != 1 or desired in old:
            raise PreparationError("Unexpected launch-screen project context")
        new = old.replace(original, desired)
        project.write_bytes(new.encode())
        fork_license = blob(fork, "LICENSE")
        licenses = temporary / "build-provenance/licenses"
        licenses.mkdir()
        (licenses / "xbunax-nxnative-LICENSE").write_bytes(fork_license)
        provenance = {
            "schema_version": 2, "status": "prepared_source_only", "lite": lite,
            "upstream": {"repository": "https://github.com/devinhurry/touch-the-wild", "commit": UPSTREAM_PIN,
                         "shader_chain": UPSTREAM_CHAIN},
            "upstream_merge": upstream_merge,
            "camera_input_patch": camera_patch,
            "fork": {"repository": "https://github.com/xbunax/nxnative", "commit": FORK_PIN,
                     "patch_base": FORK_BASE},
            "copied": copied, "patch_application": "unique full-context relocation, regenerated diff, strict replay",
            "patches": records, "objc_overlay": {"path": OVERLAY, "sha256": digest(overlay)},
            "removed_swift": removed, "launch_screen": {"path": "dm/ios/project.yml",
                "before_sha256": digest(old.encode()), "after_sha256": digest(new.encode())},
            "uncompiled_swift": [{"path": name, "sha256": digest((temporary / name).read_bytes())}
                                  for name in SWIFT_UNCOMPILED],
            "licenses": {"upstream": "LICENSE", "fork": "build-provenance/licenses/xbunax-nxnative-LICENSE",
                         "fork_sha256": digest(fork_license), "third_party": "Original notices retained in copied source paths"},
            "limitations": {"native_aspect_ready": False, "plugin_integrated": False,
                            "native_aspect_implementation_included": True,
                            "touch_fork_controls_included": True,
                            "host_compiled": False, "game_data_included": False, "device_tested": False,
                            "shader_tool_binaries_included": False,
                            "windows_shader_chain_must_be_recomputed": True,
                            "objc_launcher_missing_features": [
                                "Visual touch layout editor and stick response chart",
                                "Cloud save folder selection UI",
                                "External .nxrom import and installed-data receipt validation"],
                            "required_swift_bridge_exports_for_final_link": [
                                "dm_imported_game_ready", "dm_show_game_import"],
                            "final_link_ready": False},
        }
        (temporary / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        os.rename(temporary, output)
        return provenance
    except BaseException:
        shutil.rmtree(temporary)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--fork", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="new directory outside the two source checkouts")
    parser.add_argument("--lite", action="store_true", help="host compile stage: keep BOTW and synthetic Mario profiles; omit most docs")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.upstream.resolve(), args.fork.resolve(), args.output.absolute(), args.lite)
    except (PreparationError, OSError, ValueError) as exc:
        print(f"Source preparation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(args.output.resolve()), "status": result["status"],
                      "patches": len(result["patches"]), "bytes": sum(x["bytes"] for x in result["copied"].values()),
                      "native_aspect_ready": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
