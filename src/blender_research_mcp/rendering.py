"""External render orchestration and bounded image validation."""

from __future__ import annotations

import base64
import hashlib
import io
from typing import Any

from PIL import Image

from blender_research_mcp.authoring import require_capability
from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import MAX_DEADLINE_MS


def decode_render_preview(result: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    encoded = result.pop("png_base64", None)
    if not isinstance(encoded, str):
        raise ValueError("Blender render response did not contain PNG data")
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("Blender render response contained invalid base64") from exc
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Blender render response was not a PNG")
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        expected_size = (int(result["width"]), int(result["height"]))
        if image.size != expected_size:
            raise ValueError(
                f"Blender render dimensions changed: expected {expected_size}, got {image.size}"
            )
        extrema = image.convert("RGB").convert("L").getextrema()
        if extrema[0] == extrema[1]:
            raise ValueError("Blender render PNG has no grayscale variation")
    digest = hashlib.sha256(data).hexdigest()
    if result.get("sha256") != digest:
        raise ValueError("Blender render PNG hash did not match its metadata")
    if result.get("byte_count") != len(data):
        raise ValueError("Blender render PNG byte count did not match its metadata")
    return data, result


async def request_render_preview(
    client: BridgeClient,
    params: dict[str, Any],
    *,
    expected_scene_generation: int,
    idempotency_key: str,
) -> tuple[bytes, dict[str, Any]]:
    await require_capability(client, "render_preview")
    result = await client.call(
        "render.preview",
        params,
        deadline_ms=MAX_DEADLINE_MS,
        expected_scene_generation=expected_scene_generation,
        idempotency_key=idempotency_key,
        read_only=False,
    )
    return decode_render_preview(result)


async def request_render_save(
    client: BridgeClient,
    params: dict[str, Any],
    *,
    expected_scene_generation: int,
    idempotency_key: str,
) -> dict[str, Any]:
    await require_capability(client, "render_export")
    return await client.call(
        "render.save",
        params,
        deadline_ms=MAX_DEADLINE_MS,
        expected_scene_generation=expected_scene_generation,
        idempotency_key=idempotency_key,
        read_only=False,
    )
