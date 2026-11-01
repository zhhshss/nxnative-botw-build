#!/usr/bin/env python3
"""Split a verified BOTW IPA into its original .nxrom data and an import-ready IPA.

Uses the pinned source tree's game_data.py format and preserves every app code
and shader byte. A rebuilt app can use the same data descriptor without opening
or copying its large game entries again.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sys
import zipfile

try:
    from .build_incremental_update import BUNDLE_ID, Source, macho_type, safe_name
except ImportError:
    from build_incremental_update import BUNDLE_ID, Source, macho_type, safe_name

CHUNK = 1024 * 1024
DESCRIPTOR = "dm/game-data.json"


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_packer(source: Path):
    path = source / "dm/ios/tools/game_data.py"
    if not path.is_file():
        raise ValueError("--source must be the prepared nxnative source tree")
    sys.path.insert(0, str(source / "dm/tools"))
    spec = importlib.util.spec_from_file_location("nxnative_split_game_data_packer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def app_info(source: Source) -> dict:
    if source.kind != "ipa" or "Info.plist" not in source.files or "DmIOS" not in source.files:
        raise ValueError("source must be a complete BOTW IPA")
    info = plistlib.loads(source.read_small("Info.plist"))
    if info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleExecutable") != "DmIOS":
        raise ValueError("source must use the existing BOTW bundle identifier and executable")
    if macho_type(source) != 2:
        raise ValueError("source must contain an application executable")
    chain = info.get("DMChain")
    if not isinstance(chain, str) or not re.fullmatch(r"[0-9a-f]{12}", chain):
        raise ValueError("source must record its shader DMChain")
    if not any(n.startswith(f"msl/b-{chain}/") and n.endswith(".metallib") for n in source.files):
        raise ValueError("source is missing its shader libraries")
    # Both C bridge exports are part of the pinned Swift importer. Repacking
    # cannot add import support to an executable built without that code.
    found, tail = set(), b""
    with source.open("DmIOS") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            block = tail + chunk
            for symbol in (b"dm_imported_game_ready", b"dm_show_game_import"):
                if symbol in block:
                    found.add(symbol)
            tail = block[-64:]
    if len(found) != 2:
        raise ValueError("source executable has no original game-data import bridge")
    return info


def validate_descriptor(descriptor: dict) -> None:
    if (descriptor.get("format") != 1 or descriptor.get("title_id", "").upper() != "01007EF00011E000" or
            not isinstance(descriptor.get("size"), int) or descriptor["size"] < 16 or
            not re.fullmatch(r"[0-9a-f]{64}", descriptor.get("sha256", ""))):
        raise ValueError("invalid BOTW .nxrom descriptor")
    marker = safe_name(descriptor.get("marker", ""))
    if marker.startswith("/") or not isinstance(descriptor.get("version"), str):
        raise ValueError("invalid game-data identity")


def game_entry(name: str) -> bool:
    parts = name.rstrip("/").casefold().split("/")
    return bool(parts) and parts[0] in {"romfs", "romfs.img", "romfs.id", "aoc"}


def build_app(ipa: Path, output: Path, descriptor: dict) -> dict:
    validate_descriptor(descriptor)
    if output.exists() or output.is_symlink() or output.resolve() == ipa.resolve():
        raise ValueError("the app output must be a new file")
    output.parent.mkdir(parents=True, exist_ok=True)
    part = output.with_name(output.name + ".partial")
    created = False
    with closing(Source(ipa)) as source:
        info = app_info(source)
        members = source.zip.infolist()
        prefix = next(m.filename[:-len("Info.plist")] for m in members if m.filename.endswith(".app/Info.plist"))
        descriptor = dict(descriptor, version=info["CFBundleShortVersionString"])
        app_code, copied = {}, 0
        try:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(fd, "wb") as file:
                with zipfile.ZipFile(file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as target:
                    for member in members:
                        if not member.filename.startswith(prefix):
                            if member.is_dir() and member.filename in {"Payload/", prefix}:
                                target.writestr(copy.copy(member), b"")
                            continue
                        relative = member.filename[len(prefix):]
                        if game_entry(relative) or relative == DESCRIPTOR:
                            continue
                        out_info = copy.copy(member)
                        if member.is_dir():
                            target.writestr(out_info, b"")
                            continue
                        digest = hashlib.sha256()
                        with source.zip.open(member) as reader, target.open(out_info, "w", force_zip64=True) as writer:
                            for chunk in iter(lambda: reader.read(CHUNK), b""):
                                writer.write(chunk); digest.update(chunk)
                                copied += len(chunk)
                        if relative in {"DmIOS", "Info.plist"}:
                            app_code[relative] = digest.hexdigest()
                    target.writestr(prefix + DESCRIPTOR, json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode())
                file.flush(); os.fsync(file.fileno())
            with zipfile.ZipFile(part) as check:
                if any(game_entry(n[len(prefix):]) for n in check.namelist() if n.startswith(prefix)):
                    raise ValueError("game data remains in the app-only IPA")
                if json.loads(check.read(prefix + DESCRIPTOR)) != descriptor:
                    raise ValueError("app data descriptor changed during packing")
            os.replace(part, output)
        finally:
            if created:
                part.unlink(missing_ok=True)
    report = {"file": output.name, "bytes": output.stat().st_size, "sha256": digest_file(output),
              "bundle_id": BUNDLE_ID, "descriptor": descriptor, "preserved_app_sha256": app_code,
              "copied_uncompressed_bytes": copied}
    output.with_name(output.name + ".sha256").write_text(f"{report['sha256']}  {output.name}\n")
    output.with_name(output.name + ".json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def extract_game(ipa: Path, work: Path, aoc_work: Path) -> tuple[Path, Path | None, dict]:
    work.mkdir(parents=True, exist_ok=True); aoc_work.mkdir(parents=True, exist_ok=True)
    checkpoint = work / "extraction.json"
    saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    metadata = ipa.stat()
    identity = [metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns]
    records = saved.get("files", {}) if saved.get("source") == identity else {}
    with closing(Source(ipa)) as source:
        info = app_info(source)
        if "romfs.img" not in source.files:
            raise ValueError("the bundled IPA must contain a RomFS image")
        selected = ["romfs.img"] + [n for n in source.files if n.startswith("aoc/")]
        for name in selected:
            destination = work / "RomFS.img" if name == "romfs.img" else aoc_work / safe_name(name[4:])
            _, size, _ = source.files[name]
            if destination.exists():
                current = destination.stat()
                if records.get(name) == [current.st_size, current.st_mtime_ns, current.st_ctime_ns] and current.st_size == size:
                    print(f"Reused extracted data: {name}", flush=True)
                    continue
                raise ValueError(f"unverified extraction already exists: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(destination.parent).free < size + (128 << 20):
                raise ValueError(f"not enough space to extract {name}")
            part = destination.with_name(destination.name + ".partial")
            part.unlink(missing_ok=True)
            print(f"Extracting {name}: {size / 2**30:.2f} GiB", flush=True)
            try:
                with source.open(name) as reader, part.open("xb") as writer:
                    count = 0
                    for chunk in iter(lambda: reader.read(CHUNK), b""):
                        writer.write(chunk); count += len(chunk)
                    writer.flush(); os.fsync(writer.fileno())
                if count != size:
                    raise ValueError("truncated source game data")
                os.replace(part, destination)
                current = destination.stat()
                records[name] = [current.st_size, current.st_mtime_ns, current.st_ctime_ns]
                checkpoint.write_text(json.dumps({"source": identity, "files": records}, indent=2) + "\n")
            finally:
                part.unlink(missing_ok=True)
        current = ipa.stat()
        if [current.st_size, current.st_mtime_ns, current.st_ctime_ns] != identity:
            raise ValueError("source IPA changed during extraction")
    return work / "RomFS.img", aoc_work if len(selected) > 1 else None, info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ipa", type=Path)
    parser.add_argument("--app-output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--aoc-work", type=Path)
    parser.add_argument("--data-output", type=Path)
    args = parser.parse_args()
    if args.metadata:
        descriptor = json.loads(args.metadata.read_text())
    else:
        if not all((args.source, args.work, args.aoc_work, args.data_output)):
            parser.error("creating .nxrom needs --source, --work, --aoc-work and --data-output")
        packer = load_packer(args.source)
        romfs, aoc, info = extract_game(args.ipa, args.work, args.aoc_work)
        metadata = args.data_output.with_suffix(".json")
        descriptor = packer.pack(args.data_output, romfs, aoc, "01007EF00011E000", info["CFBundleShortVersionString"],
                                 "Pack/Bootup_Graphics.pack", metadata, args.work / "game-data-stamp.json")
        args.data_output.with_name(args.data_output.name + ".sha256").write_text(f"{descriptor['sha256']}  {args.data_output.name}\n")
        print(f"Verified game data: {args.data_output} ({descriptor['size'] / 2**30:.2f} GiB)", flush=True)
    report = build_app(args.ipa, args.app_output, descriptor)
    print(f"Verified app-only IPA: {args.app_output} ({report['bytes'] / 2**20:.0f} MiB)", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"Game-data split failed: {exc}")
