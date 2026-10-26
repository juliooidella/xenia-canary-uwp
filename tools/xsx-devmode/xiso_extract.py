#!/usr/bin/env python3
"""Extract an XDVDFS image (Xbox 360 / Xbox ISO) into a folder (stdlib only).

The Xenia UWP frontend lists disc games on a USB drive only as extracted
folders with default.xex (game_scanner.h), so full XGD images must be unpacked.
Handles plain XISO and the XGD1/XGD2/XGD3 game-partition offsets.

Usage: xiso_extract.py <image.iso> <dest_dir> [--dry-run]
"""
from __future__ import annotations

import os
import struct
import sys
import time

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
# Game partition start: plain XISO, XGD3, XGD2, XGD1.
PARTITION_OFFSETS = [0x0, 0x2080000, 0xFD90000, 0x18300000]
ATTR_DIRECTORY = 0x10


def find_partition(f) -> int:
    for base in PARTITION_OFFSETS:
        f.seek(base + 32 * SECTOR)
        if f.read(20) == MAGIC:
            return base
    raise SystemExit("XDVDFS magic not found: not an Xbox disc image")


def walk(data: bytes, offset: int, entries: list) -> None:
    """In-order traversal of a directory's binary tree of entries."""
    if offset + 14 > len(data):
        return
    left, right, start, size, attr, nlen = struct.unpack_from("<HHIIBB", data, offset)
    if left == 0xFFFF and right == 0xFFFF:  # end-of-sector padding
        return
    name = data[offset + 14: offset + 14 + nlen].decode("ascii", "replace")
    if left:
        walk(data, left * 4, entries)
    entries.append((name, start, size, attr))
    if right:
        walk(data, right * 4, entries)


def extract_dir(f, base: int, sector: int, size: int, dest: str, stats: dict, dry: bool) -> None:
    entries: list = []
    if size:
        f.seek(base + sector * SECTOR)
        walk(f.read(size), 0, entries)
    for name, start, fsize, attr in entries:
        path = os.path.join(dest, name)
        if attr & ATTR_DIRECTORY:
            if not dry:
                os.makedirs(path, exist_ok=True)
            stats["dirs"] += 1
            extract_dir(f, base, start, fsize, path, stats, dry)
            continue
        stats["files"] += 1
        stats["bytes"] += fsize
        if dry:
            continue
        if os.path.exists(path) and os.path.getsize(path) == fsize:
            stats["skipped"] += 1
            continue
        f.seek(base + start * SECTOR)
        remaining = fsize
        with open(path, "wb") as out:
            while remaining:
                chunk = f.read(min(remaining, 8 * 1024 * 1024))
                if not chunk:
                    raise SystemExit(f"short read in {name}")
                out.write(chunk)
                remaining -= len(chunk)
                stats["done"] += len(chunk)
        now = time.time()
        if now - stats["last"] > 15:
            stats["last"] = now
            mb = stats["done"] / 2 ** 20
            print(f"  {mb:8.0f} MB  ({mb / max(now - stats['t0'], 1):.1f} MB/s)  {name}", flush=True)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    iso, dest = sys.argv[1], sys.argv[2]
    dry = "--dry-run" in sys.argv
    stats = {"files": 0, "dirs": 0, "bytes": 0, "done": 0, "skipped": 0,
             "t0": time.time(), "last": time.time()}
    with open(iso, "rb") as f:
        base = find_partition(f)
        f.seek(base + 32 * SECTOR + 20)
        root_sector, root_size = struct.unpack("<II", f.read(8))
        print(f"partition at 0x{base:X}, root: sector {root_sector}, {root_size} bytes")
        if not dry:
            os.makedirs(dest, exist_ok=True)
        extract_dir(f, base, root_sector, root_size, dest, stats, dry)
    print(f"done: {stats['files']} files, {stats['dirs']} dirs, {stats['bytes'] / 2 ** 30:.2f} GiB"
          + (f", {stats['skipped']} already present" if stats["skipped"] else "")
          + f", {time.time() - stats['t0']:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
