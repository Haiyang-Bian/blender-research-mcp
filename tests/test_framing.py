import asyncio
import struct

import pytest

from blender_research_mcp.framing import (
    FrameDecoder,
    FramingError,
    decode_payload,
    encode_frame,
    read_frame,
)


def test_frame_decoder_handles_fragmentation_coalescing_and_unicode() -> None:
    first = encode_frame({"name": "眼球.左"}, max_bytes=1024)
    second = encode_frame({"value": 2}, max_bytes=1024)
    decoder = FrameDecoder(max_bytes=1024)

    assert decoder.feed(first[:3]) == []
    assert decoder.feed(first[3:] + second) == [
        {"name": "眼球.左"},
        {"value": 2},
    ]


def test_framing_rejects_invalid_or_oversized_payloads() -> None:
    with pytest.raises(FramingError, match="maximum"):
        encode_frame({"value": "too large"}, max_bytes=2)
    with pytest.raises(FramingError, match="zero-length"):
        FrameDecoder(max_bytes=10).feed(struct.pack(">I", 0))
    with pytest.raises(FramingError, match="UTF-8 JSON"):
        decode_payload(b"not-json")
    with pytest.raises(FramingError, match="JSON object"):
        decode_payload(b"[]")


def test_async_reader_reassembles_a_split_frame() -> None:
    async def scenario() -> dict[str, object]:
        reader = asyncio.StreamReader()
        frame = encode_frame({"ok": True}, max_bytes=100)
        reader.feed_data(frame[:2])
        reader.feed_data(frame[2:])
        reader.feed_eof()
        return await read_frame(reader, max_bytes=100)

    assert asyncio.run(scenario()) == {"ok": True}
