"""Length-prefixed JSON framing for the loopback Blender transport."""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Mapping
from typing import Any


class FramingError(ValueError):
    """Raised for malformed or over-sized frames."""


def encode_frame(payload: Mapping[str, Any], *, max_bytes: int) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not body:
        raise FramingError("empty JSON payload")
    if len(body) > max_bytes:
        raise FramingError(f"payload is {len(body)} bytes; maximum is {max_bytes}")
    return struct.pack(">I", len(body)) + body


def decode_payload(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FramingError("frame payload is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise FramingError("frame payload must be a JSON object")
    return value


async def read_frame(reader: asyncio.StreamReader, *, max_bytes: int) -> dict[str, Any]:
    header = await reader.readexactly(4)
    (length,) = struct.unpack(">I", header)
    if length == 0:
        raise FramingError("zero-length frames are not allowed")
    if length > max_bytes:
        raise FramingError(f"frame declares {length} bytes; maximum is {max_bytes}")
    return decode_payload(await reader.readexactly(length))


class FrameDecoder:
    """Incremental decoder used by the Blender socket thread and unit tests."""

    def __init__(self, *, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []
        while len(self._buffer) >= 4:
            (length,) = struct.unpack(">I", self._buffer[:4])
            if length == 0:
                raise FramingError("zero-length frames are not allowed")
            if length > self._max_bytes:
                raise FramingError(
                    f"frame declares {length} bytes; maximum is {self._max_bytes}"
                )
            if len(self._buffer) < 4 + length:
                break
            body = bytes(self._buffer[4 : 4 + length])
            del self._buffer[: 4 + length]
            messages.append(decode_payload(body))
        return messages
