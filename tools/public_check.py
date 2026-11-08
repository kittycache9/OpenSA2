#!/usr/bin/env python3
"""Check public source, documentation, and contributor privacy.

Run `python3 tools/public_check.py` before a push. For a private identity audit,
pass `--deny-file` with a local JSON object containing a `terms` array. Keep
that file outside the repository: it must never publish the identities it checks.
This source check does not establish gameplay parity or release readiness.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_ROOTS = {"private", "build", ".venv", ".claude", "agents-journals"}
# The skills folders Claude Code, Cursor, and OpenCode read: links to the one copy in
# .agents/skills, the folder Codex reads. No other symlink is accepted.
SKILL_LINKS = {".claude/skills", ".cursor/skills", ".opencode/skills"}
FORBIDDEN_SUFFIXES = set(".exe .dll .so .a .o .obj .apk .zip .7z .jsonl .prs .pak .adx .ahx .gvr .gvm .pvr .pvm .sfd .csb .dds .afs .sa2s .y4m .mp4".split())
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9]{40,})\b")
LINK = re.compile(r"\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)|(?:src|href)=[\"']([^\"']+)[\"']")


def validate_screenshot_png(data: bytes) -> None:
    """Accept only bounded RGB/RGBA PNG pixels without metadata chunks."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("invalid PNG signature")
    offset = 8
    header = None
    compressed = bytearray()
    has_idat = False
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("truncated PNG chunk")
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("truncated PNG chunk")
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:end - 4]
        crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + payload) != crc:
            raise ValueError("invalid PNG chunk checksum")
        if kind == b"IHDR":
            if offset != 8 or length != 13:
                raise ValueError("invalid PNG header order or length")
            header = struct.unpack(">IIBBBBB", payload)
            width, height, depth, color, compression, filtering, interlace = header
            if not (1 <= width <= 4096 and 1 <= height <= 4096):
                raise ValueError("PNG dimensions must be between 1 and 4096")
            if (depth != 8 or color not in {2, 6} or
                    (compression, filtering, interlace) != (0, 0, 0)):
                raise ValueError("PNG must use noninterlaced 8-bit RGB or RGBA")
        elif kind == b"IDAT":
            if header is None:
                raise ValueError("PNG image data precedes its header")
            compressed.extend(payload)
            has_idat = True
        elif kind == b"IEND":
            if length or header is None or not has_idat:
                raise ValueError("invalid PNG end chunk")
            if end != len(data):
                raise ValueError("PNG has trailing bytes")
            break
        else:
            raise ValueError("PNG contains metadata or an unsupported chunk")
        offset = end
    else:
        raise ValueError("PNG end chunk is missing")

    width, height, _, color, _, _, _ = header
    row_bytes = 1 + width * (3 if color == 2 else 4)
    expected_bytes = height * row_bytes
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(compressed, expected_bytes + 1)
    except zlib.error as error:
        raise ValueError("invalid PNG pixel compression") from error
    if (len(pixels) != expected_bytes or not decoder.eof or
            decoder.unused_data or decoder.unconsumed_tail):
        raise ValueError("invalid PNG pixel data length or stream")
    if any(pixels[index] > 4 for index in range(0, expected_bytes, row_bytes)):
        raise ValueError("invalid PNG row filter")


def source_paths(root: Path) -> list[Path]:
    if (root / ".git").exists():
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                                check=True, capture_output=True)
        return [root / name.decode() for name in result.stdout.split(b"\0") if name]
    return [p for p in root.rglob("*") if p.is_file() and
            not any(x in FORBIDDEN_ROOTS | {".git", "__pycache__", ".pytest_cache"}
                    for x in p.relative_to(root).parts)]


def check(root: Path, paths: list[Path], terms: list[str]) -> list[str]:
    failures = []
    pins = json.loads((root / "tools/pins.json").read_text())
    hashes = {row["sha256"] for row in pins["executables"].values()}
    receipt = root / "Documentation/receipts/install-hashes-v1.json"
    if not receipt.is_file():
        failures.append("installation checksum metadata is missing")
    else:
        hashes.update(row["sha256"] for row in json.loads(receipt.read_text())["files"] if row["bytes"])
    for path in paths:
        rel = path.relative_to(root)
        if path.is_symlink():
            if rel.as_posix() in SKILL_LINKS and path.readlink().as_posix() == "../.agents/skills":
                continue
            failures.append(f"{rel}: symlinks are not accepted in the public source")
            continue
        if not path.is_file():
            failures.append(f"{rel}: tracked file missing")
            continue
        if rel.parts[0] in FORBIDDEN_ROOTS or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            failures.append(f"{rel}: private data, game asset, or build output")
        data = path.read_bytes()
        if len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() in hashes:
            failures.append(f"{rel}: size boundary or shipped-file checksum")
        if re.fullmatch(r"(?:sub_[0-9a-fA-F]{8}|lifted_.*|function_table)\.c", path.name) or path.name == "lifted.h":
            failures.append(f"{rel}: lifted output filename")
        if data.startswith(b"/* sa2-recomp lifted */"):
            failures.append(f"{rel}: lifted output banner")
        private_name = any(term.casefold() in str(rel).casefold() for term in terms)
        if private_name:
            failures.append(f"{rel}: private identity term")
        if rel.parts[:2] == ("docs", "screenshots") and path.suffix == ".png":
            try:
                validate_screenshot_png(data)
            except ValueError as error:
                failures.append(f"{rel}: {error}")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            failures.append(f"{rel}: binary file in source export")
            continue
        if TOKEN.search(text) or "-----BEGIN " + "PRIVATE KEY-----" in text:
            failures.append(f"{rel}: credential-shaped text")
        if not private_name and any(term.casefold() in text.casefold() for term in terms):
            failures.append(f"{rel}: private identity term")
        try:
            if path.suffix == ".py":
                ast.parse(text, filename=str(rel))
            elif path.suffix == ".json":
                json.loads(text)
            elif path.suffix == ".svg":
                svg = ElementTree.fromstring(text)
                for node in svg.iter():
                    if node.tag.rsplit("}", 1)[-1] in {"script", "foreignObject", "image"}:
                        raise ValueError("SVG includes executable or embedded content")
                    for attr, value in node.attrib.items():
                        if attr.rsplit("}", 1)[-1] == "href" and not value.startswith("#"):
                            raise ValueError("SVG includes an external reference")
        except (SyntaxError, ValueError, ElementTree.ParseError) as error:
            failures.append(f"{rel}: {error}")
        if path.suffix == ".md" and (str(rel).startswith("docs/") or len(rel.parts) == 1):
            for match in LINK.finditer(text):
                target = (match[1] or match[2]).strip("<>")
                if target.startswith(("https://", "http://", "mailto:", "#")):
                    continue
                target = unquote(target.split("#", 1)[0])
                if target and not (path.parent / target).is_file():
                    failures.append(f"{rel}: missing local link {target}")
    ignore = (root / ".gitignore").read_text().splitlines()
    if not {"/private", "/build"}.issubset(ignore):
        failures.append(".gitignore must ignore /private and /build")
    # The method pages and their records (tools/method_check.py).
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import method_check  # noqa: PLC0415
    failures.extend(method_check.run_all(root))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--deny-file", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    terms = json.loads(args.deny_file.read_text())["terms"] if args.deny_file else []
    paths = source_paths(root)
    failures = check(root, paths, terms)
    for failure in failures:
        print(failure)
    print(f"Public source check: {len(paths)} files, {len(failures)} failures.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
