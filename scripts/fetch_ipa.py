#!/usr/bin/env python3
"""Stream an encrypted Actions artifact into one verified IPA on the owner's disk."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote
import zipfile

NAME = "BOTW-iPad-Touch.ipa"
CHUNK = 1024 * 1024


def gh_json(*args):
    return json.loads(subprocess.check_output(["gh", *args], text=True))


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_digest(log: str, name: str) -> str:
    hashes = {match.group(1) for match in re.finditer(
        r"(?m)\b([0-9a-f]{64})\s+([^\r\n]+)$", log)
        if Path(match.group(2).strip()).name == name}
    if len(hashes) != 1:
        raise ValueError("the successful build log must contain one unambiguous IPA SHA256")
    return hashes.pop()


def extract_stream(source, output: Path, password: str, name: str, expected: str) -> None:
    """bsdtar handles both ZIP layers, ZIP64, encryption and CRC in bounded memory."""
    part = output.with_name(output.name + ".partial")
    if part.exists():
        raise ValueError(f"unfinished pickup already exists: {part}")
    processes = []
    with tempfile.TemporaryFile() as outer_errors, tempfile.TemporaryFile() as inner_errors:
        try:
            outer = subprocess.Popen(["bsdtar", "-xOf", "-", name + ".zip"],
                                     stdin=source, stdout=subprocess.PIPE, stderr=outer_errors)
            processes.append(outer)
            inner = subprocess.Popen(["bsdtar", "--passphrase", password, "-xOf", "-", name],
                                     stdin=outer.stdout, stdout=subprocess.PIPE, stderr=inner_errors)
            processes.append(inner)
            outer.stdout.close()
            h, received, reported = hashlib.sha256(), 0, 0
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as dest:
                for chunk in iter(lambda: inner.stdout.read(CHUNK), b""):
                    dest.write(chunk)
                    h.update(chunk)
                    received += len(chunk)
                    if received - reported >= 2**30:
                        print(f"IPA pickup: {received / 2**30:.1f} GiB", flush=True)
                        reported = received
            inner.stdout.close()
            statuses = [p.wait() for p in processes]
            if any(statuses):
                outer_errors.seek(0); inner_errors.seek(0)
                detail = (outer_errors.read() + inner_errors.read()).decode(errors="replace")
                raise ValueError("archive extraction failed: " + detail.replace(password, "<redacted>")[-1200:])
            if h.hexdigest() != expected:
                raise ValueError("IPA SHA256 differs from the successful build")
            with zipfile.ZipFile(part) as ipa:
                names = set(ipa.namelist())
                if not {"Payload/DmIOS.app/Info.plist", "Payload/DmIOS.app/DmIOS"} <= names:
                    raise ValueError("archive is missing the app executable or Info.plist")
            os.replace(part, output)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait()
            part.unlink(missing_ok=True)


def extract_aes_stream(source, output: Path, password: str, name: str, expected: str) -> None:
    try:
        from .shader_cache import decrypt_ipa_stream
    except ImportError:
        from shader_cache import decrypt_ipa_stream
    part = output.with_name(output.name + ".partial")
    if part.exists():
        raise ValueError(f"unfinished pickup already exists: {part}")
    with tempfile.TemporaryFile() as errors:
        outer = subprocess.Popen(["bsdtar", "-xOf", "-", name + ".enc"],
                                 stdin=source, stdout=subprocess.PIPE, stderr=errors)
        try:
            decrypt_ipa_stream(outer.stdout, part, expected, password.encode())
            outer.stdout.close()
            if outer.wait():
                raise ValueError("encrypted artifact extraction failed")
            with zipfile.ZipFile(part) as ipa:
                if not {"Payload/DmIOS.app/Info.plist", "Payload/DmIOS.app/DmIOS"} <= set(ipa.namelist()):
                    raise ValueError("archive is missing the app executable or Info.plist")
            os.replace(part, output)
        except Exception as exc:
            raise ValueError("encrypted IPA verification failed") from exc
        finally:
            outer.stdout.close()
            if outer.poll() is None:
                outer.kill()
            outer.wait()
            part.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=int)
    parser.add_argument("--repo", default="zhhshss/nxnative-botw-build")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--public-base", default="http://3.14.160.195:9443")
    args = parser.parse_args()
    if not shutil.which("bsdtar"):
        raise ValueError("install libarchive-tools before picking up the IPA")
    run = gh_json("run", "view", str(args.run), "--repo", args.repo,
                  "--json", "status,conclusion,headSha,jobs,url")
    if run["status"] != "completed" or run["conclusion"] != "success":
        raise ValueError("the requested workflow has not completed successfully")
    jobs = [j for j in run["jobs"] if j["name"].endswith("ipa") and j["conclusion"] == "success"]
    if len(jobs) != 1:
        raise ValueError("expected one successful IPA job")
    log = subprocess.check_output(["gh", "run", "view", "--job", str(jobs[0]["databaseId"]),
                                   "--repo", args.repo, "--log"], text=True)
    expected = expected_digest(log, NAME)
    artifacts = gh_json("api", f"repos/{args.repo}/actions/runs/{args.run}/artifacts")
    matches = [a for a in artifacts["artifacts"] if a["name"] == "botw-ipa-encrypted" and not a["expired"]]
    if len(matches) != 1:
        raise ValueError("expected one unexpired encrypted IPA artifact")
    artifact = matches[0]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / NAME
    if output.exists() and digest_file(output) != expected:
        raise ValueError("an IPA from another build already exists at the output path")
    if not output.exists():
        if shutil.disk_usage(args.output_dir).free < artifact["size_in_bytes"] + 512 * 2**20:
            raise ValueError("not enough room for the IPA plus 512 MiB of working space")
        password = args.password_file.read_text().strip()
        if not password:
            raise ValueError("the IPA password file is empty")
        with tempfile.TemporaryFile() as errors:
            download = subprocess.Popen(["gh", "api", f"repos/{args.repo}/actions/artifacts/{artifact['id']}/zip"],
                                        stdout=subprocess.PIPE, stderr=errors)
            try:
                pickup = extract_aes_stream if "artifact is AES-256-GCM encrypted" in log else extract_stream
                pickup(download.stdout, output, password, NAME, expected)
                if download.wait():
                    output.unlink(missing_ok=True)
                    raise ValueError("artifact download failed")
            finally:
                download.stdout.close()
                if download.poll() is None:
                    download.kill()
                download.wait()
    (args.output_dir / (NAME + ".sha256")).write_text(f"{expected}  {NAME}\n")
    receipt = {"run": args.run, "head_sha": run["headSha"], "workflow": run["url"],
               "file": NAME, "sha256": expected, "bytes": output.stat().st_size}
    (args.output_dir / "botw-ipa-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Verified IPA: {output} ({output.stat().st_size / 2**30:.1f} GiB)")
    print(f"SHA256: {expected}")
    if args.token_file:
        token = args.token_file.read_text().strip()
        print(f"Download: {args.public_base.rstrip('/')}/{quote(token, safe='')}/{quote(NAME, safe='')}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"IPA pickup failed: {exc}")
