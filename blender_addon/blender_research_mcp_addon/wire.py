"""Standard-library framing used inside Blender's Python runtime."""

from __future__ import annotations

import json
import struct
from typing import Any

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 1 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class FramingError(ValueError):
    pass


def encode_frame(payload: dict[str, Any], max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not body or len(body) > max_bytes:
        raise FramingError("payload size is outside the allowed range")
    return struct.pack(">I", len(body)) + body


class FrameDecoder:
    def __init__(self, max_bytes: int = MAX_REQUEST_BYTES) -> None:
        self.max_bytes = max_bytes
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self.buffer.extend(data)
        messages: list[dict[str, Any]] = []
        while len(self.buffer) >= 4:
            (length,) = struct.unpack(">I", self.buffer[:4])
            if length == 0 or length > self.max_bytes:
                raise FramingError("frame size is outside the allowed range")
            if len(self.buffer) < 4 + length:
                break
            body = bytes(self.buffer[4 : 4 + length])
            del self.buffer[: 4 + length]
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FramingError("frame is not valid UTF-8 JSON") from exc
            if not isinstance(value, dict):
                raise FramingError("frame payload must be an object")
            messages.append(value)
        return messages
