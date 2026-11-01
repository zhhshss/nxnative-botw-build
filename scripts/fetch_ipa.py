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
import threading
import struct
import stat
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

try:
    from .artifact_zip import RemoteZip
except ImportError:
    from artifact_zip import RemoteZip

NAME = "BOTW-iPad-Touch.ipa"
APP_NAME = "BOTW-iPad-Touch-App.ipa"
DATA_NAME = "BOTW-iPad-Touch.nxrom"
GRAPHICS_NAME = "BOTW-iPad-Touch.graphics.zip"
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


def artifact_download_url(repo: str, artifact: int) -> str:
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    request = Request(f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact}/zip",
                      headers={"Authorization": "Bearer " + token,
                               "Accept": "application/vnd.github+json", "User-Agent": "botw-ipa-pickup"})
    try:
        with build_opener(NoRedirect).open(request, timeout=60):
            raise ValueError("the artifact endpoint did not return a download redirect")
    except HTTPError as exc:
        if exc.code != 302:
            raise ValueError(f"artifact download authorization failed (HTTP {exc.code})") from exc
        url = exc.headers.get("Location", "")
        exc.close()
    if not url.startswith("https://"):
        raise ValueError("the artifact download redirect must use HTTPS")
    return url


def extract_stream(source, output: Path, password: str, name: str, expected: str) -> None:
    """Extract a legacy encrypted ZIP entry, whose outer CRC is checked by source."""
    part = output.with_name(output.name + ".partial")
    if part.exists():
        raise ValueError(f"unfinished pickup already exists: {part}")
    created, transfer_errors = False, []
    with tempfile.TemporaryFile() as inner_errors:
        inner = subprocess.Popen(["bsdtar", "--passphrase", password, "-xOf", "-", name],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=inner_errors)
        def transfer():
            try:
                accepting = True
                for chunk in iter(lambda: source.read(CHUNK), b""):
                    if accepting:
                        try:
                            inner.stdin.write(chunk)
                        except BrokenPipeError:
                            accepting = False
            except Exception as exc:
                transfer_errors.append(exc)
            finally:
                try:
                    inner.stdin.close()
                except BrokenPipeError:
                    pass
        producer = threading.Thread(target=transfer, daemon=True)
        producer.start()
        try:
            h, received, reported = hashlib.sha256(), 0, 0
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(fd, "wb") as dest:
                for chunk in iter(lambda: inner.stdout.read(CHUNK), b""):
                    dest.write(chunk)
                    h.update(chunk)
                    received += len(chunk)
                    if received - reported >= 2**30:
                        print(f"IPA pickup: {received / 2**30:.1f} GiB", flush=True)
                        reported = received
            inner.stdout.close()
            status = inner.wait()
            if status:
                source.close()
            producer.join(timeout=5)
            if status:
                inner_errors.seek(0)
                detail = inner_errors.read().decode(errors="replace")
                raise ValueError("archive extraction failed: " + detail.replace(password, "<redacted>")[-1200:])
            if producer.is_alive() or transfer_errors:
                raise ValueError("artifact ZIP entry transfer did not finish successfully")
            if h.hexdigest() != expected:
                raise ValueError("IPA SHA256 differs from the successful build")
            with zipfile.ZipFile(part) as ipa:
                names = set(ipa.namelist())
                if not {"Payload/DmIOS.app/Info.plist", "Payload/DmIOS.app/DmIOS"} <= names:
                    raise ValueError("archive is missing the app executable or Info.plist")
            os.replace(part, output)
        finally:
            if inner.poll() is None:
                inner.kill()
            inner.wait()
            inner.stdout.close()
            if producer.is_alive():
                source.close()
                producer.join(timeout=5)
            if created:
                part.unlink(missing_ok=True)


def extract_aes_stream(source, output: Path, password: str, name: str, expected: str) -> None:
    try:
        from .shader_cache import decrypt_ipa_stream
    except ImportError:
        from shader_cache import decrypt_ipa_stream
    part = output.with_name(output.name + ".partial")
    if part.exists():
        raise ValueError(f"unfinished pickup already exists: {part}")
    created = False
    try:
        decrypt_ipa_stream(source, part, expected, password.encode())
        created = True
        with zipfile.ZipFile(part) as ipa:
            if not {"Payload/DmIOS.app/Info.plist", "Payload/DmIOS.app/DmIOS"} <= set(ipa.namelist()):
                raise ValueError("archive is missing the app executable or Info.plist")
        os.replace(part, output)
    except Exception as exc:
        raise ValueError("encrypted IPA verification failed") from exc
    finally:
        if created:
            part.unlink(missing_ok=True)


def validate_game_data(path: Path, descriptor: dict) -> None:
    if path.stat().st_size != descriptor["size"]:
        raise ValueError("game-data file size differs from the app descriptor")
    with path.open("rb") as stream:
        header = stream.read(16)
        if len(header) != 16 or header[:8] != b"NXROM001":
            raise ValueError("the game-data artifact is not an original .nxrom package")
        length = struct.unpack_from("<Q", header, 8)[0]
        if not 0 < length <= 64 << 20 or length > descriptor["size"] - 16:
            raise ValueError("invalid .nxrom file index size")
        table = json.loads(stream.read(length))
    directories = table["directories"]
    files = table["files"]
    paths = directories + [record["path"] for record in files]
    if "romfs" not in directories or len(set(paths)) != len(paths):
        raise ValueError("game-data package has duplicate paths or no RomFS")
    for name in paths:
        parts = name.split("/")
        if (parts[0] not in {"romfs", "aoc"} or any(p in {"", ".", ".."} for p in parts) or
                "\\" in name or any(ord(c) < 32 for c in name)):
            raise ValueError("unsafe game-data path")
        if len(parts) > 1 and "/".join(parts[:-1]) not in directories:
            raise ValueError("game-data package is missing a parent directory")
    sizes = [record["size"] for record in files]
    if any(not isinstance(size, int) or size < 0 for size in sizes):
        raise ValueError("invalid game-data file size")
    if 16 + length + sum(sizes) != descriptor["size"]:
        raise ValueError("game-data file sizes differ from the package size")
    if "romfs/" + descriptor["marker"] not in paths:
        raise ValueError("game-data package is missing its required marker")


def extract_game_data_stream(source, output: Path, password: str, descriptor: dict) -> None:
    try:
        from .shader_cache import decrypt_ipa_stream
    except ImportError:
        from shader_cache import decrypt_ipa_stream
    part = output.with_name(output.name + ".partial")
    if part.exists():
        raise ValueError("unfinished game-data pickup already exists")
    created = False
    try:
        decrypt_ipa_stream(source, part, descriptor["sha256"], password.encode(), kind="game-data")
        created = True
        validate_game_data(part, descriptor)
        os.replace(part, output)
    except Exception as exc:
        raise ValueError("game-data verification failed") from exc
    finally:
        if created:
            part.unlink(missing_ok=True)


def validate_graphics_package(path: Path, required: dict) -> dict:
    """Verify the original stored-ZIP inventory and exact app compatibility."""
    if (not isinstance(required, dict) or required.get("schema") != 1 or
            not isinstance(required.get("compatibility"), dict) or
            not isinstance(required.get("series"), str) or
            type(required.get("minimum_version")) is not int or required["minimum_version"] < 1):
        raise ValueError("app graphics requirements are invalid")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if not entries or names[0] != "manifest.json" or len(set(names)) != len(names):
            raise ValueError("graphics archive has no leading manifest or duplicate paths")
        if not 0 < entries[0].file_size <= 64 << 20:
            raise ValueError("graphics manifest size is invalid")
        for entry in entries:
            parts = entry.filename.split("/")
            mode = entry.external_attr >> 16
            if (entry.is_dir() or not re.fullmatch(r"[A-Za-z0-9_./-]+", entry.filename) or
                    any(part in {"", ".", ".."} for part in parts) or
                    stat.S_IFMT(mode) not in {0, stat.S_IFREG} or
                    entry.compress_type != zipfile.ZIP_STORED or entry.flag_bits & 1):
                raise ValueError("graphics archive has an unsafe or unsupported entry")
        manifest = json.loads(archive.read(entries[0]))
        if (not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("kind") != "graphics" or
                manifest.get("series") != required["series"] or
                manifest.get("compatibility") != required["compatibility"] or
                type(manifest.get("version")) is not int or manifest["version"] < required["minimum_version"]):
            raise ValueError("graphics package does not meet this app's requirements")
        files = manifest["files"]
        if not isinstance(files, list) or any(not isinstance(record, dict) or not isinstance(record.get("path"), str) for record in files):
            raise ValueError("graphics package inventory is invalid")
        expected_names = [record["path"] for record in files]
        if (expected_names != sorted(expected_names) or len(set(expected_names)) != len(expected_names) or
                set(names) != {"manifest.json", *expected_names}):
            raise ValueError("graphics archive inventory differs from its manifest")
        directory = required["compatibility"].get("library")
        if not isinstance(directory, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", directory):
            raise ValueError("graphics library directory is invalid")
        library = "msl/" + directory + "/"
        if "tables/shader_map.tsv" not in expected_names or not any(name.startswith(library) and name.endswith(".metallib") for name in expected_names):
            raise ValueError("graphics package lacks its shader map or library")
        for record in files:
            name, size, expected = record["path"], record["size"], record["sha256"]
            if (not name.startswith((library, "tables/", "pso/")) or
                    Path(name).suffix not in {".metallib", ".json", ".tsv", ".mtl4a"} or
                    type(size) is not int or size < 0 or size > path.stat().st_size or
                    archive.getinfo(name).file_size != size or not re.fullmatch(r"[0-9a-f]{64}", expected)):
                raise ValueError("graphics package has an invalid resource record")
            h = hashlib.sha256()
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(CHUNK), b""):
                    h.update(block)
            if h.hexdigest() != expected:
                raise ValueError("graphics resource hash differs from its manifest")
        compiler = hashlib.sha256(json.dumps(manifest["compilers"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        identity = f"nxnative-graphics-v1\n{manifest['series']}\n{manifest['version']}\n{compiler}\n"
        identity += "".join(f"{r['path']}\t{r['size']}\t{r['sha256']}\n" for r in files)
        if manifest["compiler_id"] != compiler or manifest["id"] != hashlib.sha256(identity.encode()).hexdigest():
            raise ValueError("graphics package identity differs from its inventory")
        return manifest


def extract_graphics_stream(source, output: Path, password: str, expected: str, required: dict) -> None:
    try:
        from .shader_cache import decrypt_ipa_stream
    except ImportError:
        from shader_cache import decrypt_ipa_stream
    part = output.with_name(output.name + ".partial")
    if part.exists():
        raise ValueError("unfinished graphics pickup already exists")
    created = False
    try:
        decrypt_ipa_stream(source, part, expected, password.encode(), kind="graphics")
        created = True
        validate_graphics_package(part, required)
        os.replace(part, output)
    except Exception as error:
        raise ValueError("graphics package verification failed") from error
    finally:
        if created:
            part.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=int)
    parser.add_argument("--repo", default="zhhshss/nxnative-botw-build")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--public-base", default="http://3.14.160.195:9443")
    parser.add_argument("--with-game-data", action="store_true", help="pick up the independent .nxrom once; app-only is the default")
    parser.add_argument("--data-dir", type=Path, help="optional separate disk for the large .nxrom")
    parser.add_argument("--with-graphics", action="store_true", help="pick up the separate graphics ZIP only when local graphics are incompatible")
    parser.add_argument("--graphics-dir", type=Path, help="optional separate disk for the graphics ZIP")
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
    separate = "external game data:" in log or "graphics package:" in log
    output = args.output_dir / (APP_NAME if separate else NAME)
    current = output.exists() and digest_file(output) == expected
    if output.exists() and not current and not separate:
        raise ValueError("an IPA from another build already exists at the output path")
    if not current:
        if shutil.disk_usage(args.output_dir).free < artifact["size_in_bytes"] + 512 * 2**20:
            raise ValueError("not enough room for the IPA plus 512 MiB of working space")
        password = args.password_file.read_text().strip()
        if not password:
            raise ValueError("the IPA password file is empty")
        aes = "artifact is AES-256-GCM encrypted" in log
        with RemoteZip(artifact_download_url(args.repo, artifact["id"])) as archive:
            entry = archive.entry(NAME + (".enc" if aes else ".zip"))
            with archive.open_entry(entry) as source:
                pickup = extract_aes_stream if aes else extract_stream
                pickup(source, output, password, NAME, expected)
    graphics_output = None
    if args.with_graphics:
        with zipfile.ZipFile(output) as app:
            required = json.loads(app.read("Payload/DmIOS.app/dm/graphics-required.json"))
        directory = args.graphics_dir or args.output_dir
        directory.mkdir(parents=True, exist_ok=True)
        graphics_output = directory / GRAPHICS_NAME
        compatible = False
        if graphics_output.exists():
            try:
                validate_graphics_package(graphics_output, required)
                compatible = True
            except (ValueError, KeyError, zipfile.BadZipFile):
                pass
        if not compatible:
            expected_graphics = expected_digest(log, GRAPHICS_NAME)
            packages = [a for a in artifacts["artifacts"] if a["name"] == "botw-graphics-encrypted" and not a["expired"]]
            if len(packages) != 1:
                raise ValueError("expected one unexpired independent graphics artifact")
            if shutil.disk_usage(directory).free < packages[0]["size_in_bytes"] + 512 * 2**20:
                raise ValueError("not enough room for the graphics package plus working space")
            password = args.password_file.read_text().strip()
            with RemoteZip(artifact_download_url(args.repo, packages[0]["id"])) as archive:
                with archive.open_entry(archive.entry(GRAPHICS_NAME + ".enc")) as source:
                    extract_graphics_stream(source, graphics_output, password, expected_graphics, required)
        else:
            print("Existing graphics ZIP is compatible and verified; no repeated graphics download.")
        graphics_hash = digest_file(graphics_output)
        graphics_output.with_name(graphics_output.name + ".sha256").write_text(f"{graphics_hash}  {graphics_output.name}\n")
        print(f"Verified graphics: {graphics_output} ({graphics_output.stat().st_size / 2**30:.2f} GiB)")
    data_output = None
    if args.with_game_data:
        if not separate:
            raise ValueError("this build did not produce an independent .nxrom")
        with zipfile.ZipFile(output) as app:
            descriptor = json.loads(app.read("Payload/DmIOS.app/dm/game-data.json"))
        data_expected = descriptor["sha256"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", descriptor["version"]):
            raise ValueError("unsupported data version filename")
        directory = args.data_dir or args.output_dir
        directory.mkdir(parents=True, exist_ok=True)
        data_output = directory / f"BOTW-{descriptor['version']}-DLC.nxrom"
        if data_output.exists():
            if digest_file(data_output) != data_expected:
                raise ValueError("another data package already occupies this version's output path")
            validate_game_data(data_output, descriptor)
            print("Existing .nxrom is verified; no repeated data download.")
        else:
            if expected_digest(log, DATA_NAME) != data_expected:
                raise ValueError("published game data does not match the app descriptor")
            data_artifacts = [a for a in artifacts["artifacts"] if a["name"] == "botw-game-data-encrypted" and not a["expired"]]
            if len(data_artifacts) != 1:
                raise ValueError("expected one unexpired independent game-data artifact")
            if shutil.disk_usage(directory).free < descriptor["size"] + 512 * 2**20:
                raise ValueError("not enough room for the independent game-data package")
            password = args.password_file.read_text().strip()
            with RemoteZip(artifact_download_url(args.repo, data_artifacts[0]["id"])) as archive:
                with archive.open_entry(archive.entry(DATA_NAME + ".enc")) as source:
                    extract_game_data_stream(source, data_output, password, descriptor)
        data_output.with_name(data_output.name + ".sha256").write_text(f"{data_expected}  {data_output.name}\n")
        print(f"Verified game data: {data_output} ({descriptor['size'] / 2**30:.2f} GiB)")
    (args.output_dir / (output.name + ".sha256")).write_text(f"{expected}  {output.name}\n")
    receipt = {"run": args.run, "head_sha": run["headSha"], "workflow": run["url"],
               "file": output.name, "sha256": expected, "bytes": output.stat().st_size}
    (args.output_dir / ("botw-app-receipt.json" if separate else "botw-ipa-receipt.json")).write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Verified IPA: {output} ({output.stat().st_size / 2**30:.1f} GiB)")
    print(f"SHA256: {expected}")
    if args.token_file:
        token = args.token_file.read_text().strip()
        print(f"Download: {args.public_base.rstrip('/')}/{quote(token, safe='')}/{quote(output.name, safe='')}")
        if data_output and data_output.parent == args.output_dir:
            print(f"Game data: {args.public_base.rstrip('/')}/{quote(token, safe='')}/{quote(data_output.name, safe='')}")
        if graphics_output and graphics_output.parent == args.output_dir:
            print(f"Graphics: {args.public_base.rstrip('/')}/{quote(token, safe='')}/{quote(graphics_output.name, safe='')}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"IPA pickup failed: {exc}")
