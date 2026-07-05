#!/usr/bin/env python3
"""Generate simple extension icons (no external deps).

Draws a rounded brand-blue tile with a white 'cursor/target' dot — enough to
look intentional in the Chrome toolbar. Run: python scripts/generate_icons.py
"""
import math
import struct
import zlib
from pathlib import Path

BRAND = (110, 168, 254)      # --brand
BRAND_DARK = (30, 44, 90)
DOT = (247, 250, 255)
OUT = Path(__file__).resolve().parent.parent / "extension" / "icons"


def _chunk(typ: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))


def write_png(path: Path, size: int) -> None:
    cx = cy = (size - 1) / 2
    radius_tile = size * 0.46
    dot_r = size * 0.17
    ring_r = size * 0.30
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            # rounded-square mask via chebyshev-ish falloff
            dx, dy = abs(x - cx), abs(y - cy)
            in_tile = max(dx, dy) <= radius_tile or math.hypot(
                max(dx - radius_tile * 0.7, 0), max(dy - radius_tile * 0.7, 0)) <= radius_tile * 0.3
            d = math.hypot(x - cx, y - cy)
            if not in_tile:
                row += bytes((0, 0, 0, 0))
                continue
            # gradient background
            t = y / size
            r = int(BRAND_DARK[0] + (BRAND[0] - BRAND_DARK[0]) * t)
            g = int(BRAND_DARK[1] + (BRAND[1] - BRAND_DARK[1]) * t)
            b = int(BRAND_DARK[2] + (BRAND[2] - BRAND_DARK[2]) * t)
            if d <= dot_r:
                row += bytes((*DOT, 255))
            elif ring_r - size * 0.05 <= d <= ring_r:
                row += bytes((*DOT, 255))
            else:
                row += bytes((r, g, b, 255))
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + r for r in rows)
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(raw, 9))
           + _chunk(b"IEND", b""))
    path.write_bytes(png)
    print(f"wrote {path} ({size}x{size})")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (16, 48, 128):
        write_png(OUT / f"icon{size}.png", size)


if __name__ == "__main__":
    main()
