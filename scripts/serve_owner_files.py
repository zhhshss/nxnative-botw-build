#!/usr/bin/env python3
"""Serve the owner's build inputs and IPA with a token and correct byte ranges."""
import argparse
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit


def byte_range(header, size):
    if not header:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or not any(match.groups()) or not size:
        raise ValueError("invalid or unsatisfiable range")
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last) if last else size - 1, size - 1)
    else:
        start, end = max(0, size - int(last)), size - 1
    if start > end or start >= size:
        raise ValueError("unsatisfiable range")
    return start, end, 206


class OwnerFileHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.serve(head=True)

    def do_GET(self):
        self.serve()

    def empty(self, status, size=None):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        if size is not None:
            self.send_header("Content-Range", f"bytes */{size}")
        self.end_headers()

    def serve(self, head=False):
        path = unquote(urlsplit(self.path).path)
        prefix = "/" + self.server.token + "/"
        if not path.startswith(prefix):
            self.empty(404); return
        name = os.path.basename(path[len(prefix):])
        file = self.server.root / name
        if not name or name.endswith(".partial") or not file.is_file():
            self.empty(404); return
        try:
            stream = file.open("rb")
        except OSError:
            self.empty(404); return
        with stream:
            st = os.fstat(stream.fileno())
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
            header = self.headers.get("Range")
            if self.headers.get("If-Range") not in (None, etag, formatdate(st.st_mtime, usegmt=True)):
                header = None
            try:
                start, end, status = byte_range(header, st.st_size)
            except ValueError:
                self.empty(416, st.st_size); return
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(name, safe=""))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", formatdate(st.st_mtime, usegmt=True))
            self.send_header("Content-Length", str(end - start + 1))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{st.st_size}")
            self.end_headers()
            if head:
                return
            stream.seek(start)
            left = end - start + 1
            try:
                while left:
                    chunk = stream.read(min(1024 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return


def create_server(root, host, port, token):
    if not token:
        raise ValueError("a nonempty file-server token is required")
    server = ThreadingHTTPServer((host, port), OwnerFileHandler)
    server.root, server.token = Path(root), token
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9443)
    args = parser.parse_args()
    token = args.token_file.read_text().strip()
    server = create_server(args.root, args.host, args.port, token)
    print(f"Serving {args.root} on {args.host}:{server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
