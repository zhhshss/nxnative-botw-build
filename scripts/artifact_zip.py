"""Read stored Actions ZIP entries by HTTP range using their ZIP64 directory.

A forward-only libarchive reader sees wrapped 32-bit data-descriptor sizes in
large Actions artifacts. The central directory records the complete sizes;
read it first, then stream only the selected entry and verify its CRC.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import struct
from urllib.request import Request, urlopen
import zipfile
import zlib

MAX_METADATA = 4 * 1024 * 1024


class HTTPRangeFile(io.RawIOBase):
    def __init__(self, url: str):
        self.url, self.position = url, 0
        with urlopen(Request(url, method="HEAD"), timeout=60) as response:
            self.size = int(response.headers["Content-Length"])
        if self.size <= 0:
            raise ValueError("the artifact is empty")

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        positions = {io.SEEK_SET: offset, io.SEEK_CUR: self.position + offset,
                     io.SEEK_END: self.size + offset}
        if whence not in positions or positions[whence] < 0:
            raise ValueError("invalid artifact seek")
        self.position = positions[whence]
        return self.position

    def open_range(self, start: int, size: int):
        if size <= 0 or start < 0 or start + size > self.size:
            raise ValueError("artifact entry lies outside the ZIP")
        end = start + size - 1
        response = urlopen(Request(self.url, headers={"Range": f"bytes={start}-{end}"}), timeout=60)
        if (response.status != 206 or
                response.headers.get("Content-Range") != f"bytes {start}-{end}/{self.size}" or
                response.headers.get("Content-Length") != str(size)):
            response.close()
            raise ValueError("artifact storage did not honor the requested byte range")
        return response

    def read(self, size=-1):
        size = min(self.size - self.position, size if size >= 0 else self.size)
        if size <= 0:
            return b""
        if size > MAX_METADATA:
            raise ValueError("artifact ZIP metadata is unexpectedly large")
        with self.open_range(self.position, size) as response:
            data = response.read(size + 1)
        if len(data) != size:
            raise ValueError("artifact ZIP metadata is truncated")
        self.position += size
        return data


@dataclass(frozen=True)
class StoredEntry:
    name: str
    start: int
    size: int
    crc: int


class CheckedEntry:
    def __init__(self, response, entry: StoredEntry):
        self.response, self.entry = response, entry
        self.remaining, self.crc = entry.size, 0

    def read(self, size=-1):
        if self.remaining == 0:
            self.verify_complete()
            return b""
        size = min(self.remaining, size if size >= 0 else MAX_METADATA)
        if not size:
            return b""
        data = self.response.read(size)
        if not data or len(data) > size:
            raise ValueError("artifact ZIP entry is truncated")
        self.remaining -= len(data)
        self.crc = zlib.crc32(data, self.crc)
        if self.remaining == 0:
            self.verify_complete()
        return data

    def verify_complete(self):
        if self.remaining:
            raise ValueError("artifact ZIP entry was not completely received")
        if self.crc != self.entry.crc:
            raise ValueError("artifact ZIP entry CRC differs from its directory")

    def close(self):
        self.response.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class RemoteZip:
    def __init__(self, url: str):
        self.file = HTTPRangeFile(url)
        self.directory = zipfile.ZipFile(self.file)

    def entry(self, name: str) -> StoredEntry:
        matches = [entry for entry in self.directory.infolist() if entry.filename == name]
        if len(matches) != 1:
            raise ValueError("expected one matching entry in the artifact ZIP")
        info = matches[0]
        if info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 1:
            raise ValueError("the outer artifact entry must be stored and unencrypted")
        if info.compress_size != info.file_size:
            raise ValueError("stored artifact entry sizes disagree")
        self.file.seek(info.header_offset)
        header = self.file.read(30)
        if len(header) != 30:
            raise ValueError("truncated artifact ZIP header")
        signature, _, flags, method, _, _, _, _, _, name_size, extra_size = struct.unpack("<4s5H3I2H", header)
        if signature != b"PK\x03\x04" or method != zipfile.ZIP_STORED or flags & 1:
            raise ValueError("artifact ZIP header differs from its directory")
        encoded_name = self.file.read(name_size)
        if encoded_name.decode("utf-8" if flags & 0x800 else "cp437") != name:
            raise ValueError("artifact ZIP filename differs from its directory")
        start = info.header_offset + 30 + name_size + extra_size
        if start + info.compress_size > self.directory.start_dir or info.compress_size <= 0:
            raise ValueError("artifact entry overlaps its directory or is empty")
        return StoredEntry(name, start, info.compress_size, info.CRC)

    def open_entry(self, entry: StoredEntry) -> CheckedEntry:
        return CheckedEntry(self.file.open_range(entry.start, entry.size), entry)

    def close(self):
        self.directory.close()
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
