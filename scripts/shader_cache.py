#!/usr/bin/env python3
"""Keep completed IPA shader and pipeline outputs in an authenticated cache.

Only generated shader/pipeline directories are archived. Game containers,
RomFS, keys, executables, and Xcode intermediates never enter this cache.
The archive is encrypted before it reaches GitHub's Actions cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import struct
import subprocess
import tarfile
import tempfile

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"NXBOTW-CACHE-1\n"
CHUNK = 1024 * 1024
GAME = "01007EF00011E000"
ROOTS = ("shader", "ipa/build/ios/pso")


def file_digest(path: Path) -> bytes:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.digest()


def cache_key(source: Path, containers: Path, package: str, gpu: str,
              compiler_versions: list[str], password_namespace: bytes = b"") -> str:
    h = hashlib.sha256()
    h.update(json.dumps(["ipa-shaders-v1", platform.machine(), package,
                         gpu or "g18p", compiler_versions]).encode())
    h.update(hashlib.sha256(password_namespace).digest())
    paths = set((source / "dm/gfx/tools").rglob("*"))
    paths.update((source / "dm/gfx/nvn").glob("*.h"))
    paths.update((source / f"dm/games/{GAME}").glob("*.tsv"))
    paths.update((source / "dm/games").glob("*.py"))
    paths.update(source / name for name in (
        "dm/ios/tools/ios_metal.py", "dm/tools/romfs.py", "dm/tools/nso.py"))
    paths = {p for p in paths if p.is_file() and "__pycache__" not in p.parts
             and p.name not in {"c1_full.tsv", "cb_extent.tsv"}}
    if not paths:
        raise ValueError("no shader sources found")
    for p in sorted(paths):
        h.update(p.relative_to(source).as_posix().encode() + b"\0")
        h.update(file_digest(p))
    games = sorted(p for p in containers.iterdir() if p.is_file()
                   and p.suffix.lower() in {".nsp", ".nsz", ".xci", ".xcz"})
    if not games:
        raise ValueError("no downloaded game containers found")
    for p in games:
        h.update(p.name.encode() + b"\0")
        h.update(file_digest(p))
    return h.hexdigest()


def derive_key(password: bytes, salt: bytes) -> bytes:
    if not password:
        raise ValueError("a cache password is required")
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=salt, iterations=200000).derive(password)


def encrypt_archive(archive: Path, output: Path, key: str, password: bytes,
                    kind: str = "shader-cache") -> None:
    salt, nonce = os.urandom(16), os.urandom(12)
    metadata = json.dumps({"key": key, "salt": salt.hex(), "nonce": nonce.hex(), "kind": kind},
                          sort_keys=True).encode()
    header = MAGIC + struct.pack(">I", len(metadata)) + metadata
    encryptor = Cipher(algorithms.AES(derive_key(password, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    part = output.with_name(output.name + ".partial")
    try:
        with archive.open("rb") as src, part.open("wb") as dst:
            dst.write(header)
            for chunk in iter(lambda: src.read(CHUNK), b""):
                dst.write(encryptor.update(chunk))
            dst.write(encryptor.finalize())
            dst.write(encryptor.tag)
        os.replace(part, output)
    finally:
        part.unlink(missing_ok=True)


def decrypt_archive(cache: Path, archive: Path, key: str, password: bytes) -> None:
    try:
        with cache.open("rb") as src:
            if src.read(len(MAGIC)) != MAGIC:
                raise ValueError("unsupported shader cache format")
            size = struct.unpack(">I", src.read(4))[0]
            if not 0 < size <= 4096:
                raise ValueError("invalid shader cache header")
            encoded = src.read(size)
            metadata = json.loads(encoded)
            if metadata["key"] != key:
                raise ValueError("shader cache key mismatch")
            if metadata.get("kind", "shader-cache") != "shader-cache":
                raise ValueError("unexpected encrypted archive type")
            header = MAGIC + struct.pack(">I", size) + encoded
            start = src.tell()
            src.seek(-16, os.SEEK_END)
            end, tag = src.tell(), src.read(16)
            if end < start:
                raise ValueError("truncated shader cache")
            decryptor = Cipher(algorithms.AES(derive_key(password, bytes.fromhex(metadata["salt"]))),
                               modes.GCM(bytes.fromhex(metadata["nonce"]), tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            src.seek(start)
            with archive.open("wb") as dst:
                remaining = end - start
                while remaining:
                    chunk = src.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ValueError("truncated shader cache")
                    dst.write(decryptor.update(chunk))
                    remaining -= len(chunk)
                dst.write(decryptor.finalize())
    except BaseException:
        archive.unlink(missing_ok=True)
        raise


def decrypt_ipa_stream(source, output: Path, expected: str, password: bytes) -> None:
    """Authenticate a forward-only encrypted IPA while writing one partial file."""
    created = False
    try:
        if source.read(len(MAGIC)) != MAGIC:
            raise ValueError("unsupported encrypted IPA format")
        encoded_size = source.read(4)
        if len(encoded_size) != 4:
            raise ValueError("truncated encrypted IPA header")
        size = struct.unpack(">I", encoded_size)[0]
        if not 0 < size <= 4096:
            raise ValueError("invalid encrypted IPA header")
        encoded = source.read(size)
        metadata = json.loads(encoded)
        if metadata.get("kind") != "ipa" or metadata.get("key") != expected:
            raise ValueError("encrypted IPA identity does not match the build")
        header = MAGIC + encoded_size + encoded
        decryptor = Cipher(algorithms.AES(derive_key(password, bytes.fromhex(metadata["salt"]))),
                           modes.GCM(bytes.fromhex(metadata["nonce"]))).decryptor()
        decryptor.authenticate_additional_data(header)
        digest, tail, received, reported = hashlib.sha256(), b"", 0, 0
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, "wb") as dest:
            for chunk in iter(lambda: source.read(CHUNK), b""):
                block = tail + chunk
                if len(block) <= 16:
                    tail = block
                    continue
                plain = decryptor.update(block[:-16])
                tail = block[-16:]
                dest.write(plain); digest.update(plain)
                received += len(plain)
                if received - reported >= 2**30:
                    print(f"IPA pickup: {received / 2**30:.1f} GiB", flush=True)
                    reported = received
            if len(tail) != 16:
                raise ValueError("truncated encrypted IPA")
            plain = decryptor.finalize_with_tag(tail)
            dest.write(plain); digest.update(plain)
        if digest.hexdigest() != expected:
            raise ValueError("IPA SHA256 differs from the successful build")
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise


def permitted(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    return any(name == root or name.startswith(root + "/") for root in ROOTS)


def restore(cache: Path, work: Path, key: str, password: bytes) -> None:
    work.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shader-cache-restore-", dir=work.parent) as tmp:
        scratch = Path(tmp)
        archive = scratch / "outputs.tar.gz"
        decrypt_archive(cache, archive, key, password)
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            if any(not permitted(m.name) or not (m.isfile() or m.isdir()) for m in members):
                raise ValueError("shader cache contains an unexpected path or file type")
            tar.extractall(scratch / "stage", members=members, filter="data")
        for root in ROOTS:
            source = scratch / "stage" / root
            if source.is_dir():
                shutil.copytree(source, work / root, dirs_exist_ok=True)
    print("Restored authenticated shader and pipeline outputs; build fingerprints will validate reuse.")


def save(work: Path, output: Path, key: str, password: bytes) -> bool:
    ios = work / "shader/ios"
    complete = any((p / "inputs.tsv").is_file() and
                   p.with_name(p.name + ".key").is_file() and
                   next(p.glob("*_b.metallib"), None) is not None
                   for p in ios.glob("b-*") if p.is_dir())
    if not complete:
        print("No completed shader build to cache.")
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shader-cache-save-", dir=output.parent) as tmp:
        archive = Path(tmp) / "outputs.tar.gz"
        def include(member):
            if (not (member.isfile() or member.isdir()) or ".partial" in member.name or
                    Path(member.name).suffix.lower() in {".keys", ".nsp", ".nsz", ".xci", ".xcz", ".nca", ".nso", ".ipa"}):
                return None
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            return member
        with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
            for root in ROOTS:
                if (work / root).is_dir():
                    tar.add(work / root, arcname=root, filter=include)
        encrypt_archive(archive, output, key, password)
    print(f"Saved encrypted shader cache ({output.stat().st_size / 2**20:.0f} MiB).")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("key")
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--containers", type=Path, required=True)
    p.add_argument("--package", default="")
    p.add_argument("--gpu", default="")
    p.add_argument("--output", type=Path, required=True)
    for command in ("restore", "save"):
        p = sub.add_parser(command)
        p.add_argument("--work", type=Path, required=True)
        p.add_argument("--cache", type=Path, required=True)
        p.add_argument("--key-file", type=Path, required=True)
    p = sub.add_parser("encrypt-ipa")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--sha256", required=True)
    args = parser.parse_args()
    if args.command == "key":
        versions = [subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
                    for cmd in (["xcodebuild", "-version"],
                                ["xcrun", "-sdk", "iphoneos", "metal", "--version"],
                                ["xcrun", "-sdk", "iphoneos", "metal-tt", "-version"],
                                ["xcrun", "--sdk", "iphoneos", "--show-sdk-version"])]
        key = cache_key(args.source, args.containers, args.package, args.gpu, versions,
                        os.environ.get("IPA_ZIP_PASSWORD", "").encode())
        args.output.write_text(key + "\n")
        print(f"Shader cache key: {key}")
        if path := os.environ.get("GITHUB_OUTPUT"):
            with open(path, "a") as f:
                f.write(f"key={key}\n")
        return
    password = os.environ["IPA_ZIP_PASSWORD"].encode()
    if args.command == "encrypt-ipa":
        encrypt_archive(args.input, args.output, args.sha256, password, kind="ipa")
        return
    key = args.key_file.read_text().strip()
    if args.command == "restore":
        restore(args.cache, args.work, key, password)
    else:
        ready = save(args.work, args.cache, key, password)
        if path := os.environ.get("GITHUB_OUTPUT"):
            with open(path, "a") as f:
                f.write(f"ready={'true' if ready else 'false'}\n")


if __name__ == "__main__":
    main()
