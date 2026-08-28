"""Small standard-library helpers for bounded off-screen viewport images."""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def bounded_dimensions(width: int, height: int, max_size: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("viewport dimensions must be positive")
    if max_size <= 0:
        raise ValueError("max_size must be positive")
    scale = min(1.0, max_size / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def is_blank_rgba(data: bytes) -> bool:
    """Return true for an all-black RGBA8 image, ignoring alpha."""
    if not data or len(data) % 4:
        return True
    red = any(data[index] > 1 for index in range(0, len(data), 4))
    green = any(data[index] > 1 for index in range(1, len(data), 4))
    blue = any(data[index] > 1 for index in range(2, len(data), 4))
    return not (red or green or blue)


def encode_rgba_png(
    width: int,
    height: int,
    data: bytes,
    *,
    bottom_up: bool,
) -> bytes:
    """Encode tightly packed RGBA8 pixels as a non-interlaced PNG."""
    expected = width * height * 4
    if width <= 0 or height <= 0 or len(data) != expected:
        raise ValueError("RGBA buffer size does not match image dimensions")

    stride = width * 4
    rows = [data[offset : offset + stride] for offset in range(0, expected, stride)]
    if bottom_up:
        rows.reverse()
    scanlines = b"".join(b"\x00" + row for row in rows)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanlines, level=6))
        + chunk(b"IEND", b"")
    )
