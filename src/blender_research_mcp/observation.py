"""Focus-independent image decoding and consistent multi-view orchestration."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import time
from collections import defaultdict
from typing import Any

from PIL import UnidentifiedImageError

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import CAPTURE_DEADLINE_MS
from blender_research_mcp.errors import BridgeError, ErrorInfo, ErrorKind
from blender_research_mcp.media import resize_png


def observation_error(
    kind: ErrorKind,
    code: str,
    message: str,
    *,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> BridgeError:
    return BridgeError(
        ErrorInfo(
            kind=kind,
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
    )


async def settle_scene_generation(
    client: BridgeClient,
    *,
    timeout_seconds: float = 0.5,
    poll_seconds: float = 0.05,
) -> dict[str, Any]:
    """Wait until two consecutive pings report the same generation."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    previous_generation: int | None = None
    last_ping: dict[str, Any] | None = None
    while True:
        last_ping = await client.call("connection.ping", read_only=True)
        generation = int(last_ping["scene_generation"])
        if generation == previous_generation:
            return last_ping
        previous_generation = generation
        if loop.time() >= deadline:
            break
        await asyncio.sleep(poll_seconds)
    raise observation_error(
        ErrorKind.CONFLICT,
        "SCENE_UNSTABLE",
        "Blender scene generation did not stabilize within 500 ms",
        retryable=True,
        details={"last_scene_generation": previous_generation},
    )


async def capture_image(
    client: BridgeClient,
    *,
    object_name: str,
    view: str,
    max_size: int,
    viewport_id: str | None,
) -> tuple[bytes, dict[str, Any]]:
    result = await client.call(
        "viewport.capture",
        {
            "object_name": object_name,
            "view": view,
            "max_size": max_size,
            "viewport_id": viewport_id,
        },
        deadline_ms=CAPTURE_DEADLINE_MS,
        read_only=True,
    )
    encoded = result.pop("png_base64", None)
    if not isinstance(encoded, str):
        raise observation_error(
            ErrorKind.BLENDER_API,
            "CAPTURE_INVALID",
            "Blender capture response did not contain PNG image data",
            retryable=True,
        )
    try:
        decoded = base64.b64decode(encoded, validate=True)
        image_bytes, sizes = resize_png(decoded, max_size)
    except (ValueError, OSError, binascii.Error, UnidentifiedImageError) as exc:
        raise observation_error(
            ErrorKind.BLENDER_API,
            "CAPTURE_INVALID",
            "Blender capture response was not a valid PNG image",
            retryable=True,
        ) from exc
    result.update(sizes)
    result["mime_type"] = "image/png"
    result["sha256"] = hashlib.sha256(image_bytes).hexdigest()
    return image_bytes, result


def _identity(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "scene_generation"}


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


async def collect_observation_bundle(
    client: BridgeClient,
    *,
    object_name: str,
    views: tuple[str, ...],
    max_size: int,
    viewport_id: str | None,
) -> tuple[list[bytes], dict[str, Any]]:
    if len(set(views)) != len(views):
        raise observation_error(
            ErrorKind.VALIDATION,
            "VIEWS_DUPLICATE",
            "observation.bundle views must not contain duplicates",
            retryable=False,
        )

    started = time.perf_counter()
    ping_before = await settle_scene_generation(client)
    context_before = await client.call("context.get", read_only=True)
    object_before = await client.call(
        "object.inspect",
        {"object_name": object_name},
        read_only=True,
    )
    images: list[bytes] = []
    captures: list[dict[str, Any]] = []
    for index, view in enumerate(views):
        capture_started = time.perf_counter()
        image, metadata = await capture_image(
            client,
            object_name=object_name,
            view=view,
            max_size=max_size,
            viewport_id=viewport_id,
        )
        metadata["content_index"] = index
        metadata["elapsed_ms"] = round((time.perf_counter() - capture_started) * 1000, 3)
        images.append(image)
        captures.append(metadata)

    ping_after = await settle_scene_generation(client)
    context_after = await client.call("context.get", read_only=True)
    object_after = await client.call(
        "object.inspect",
        {"object_name": object_name},
        read_only=True,
    )
    generation_start = int(ping_before["scene_generation"])
    generation_end = int(ping_after["scene_generation"])
    if generation_start != generation_end:
        raise observation_error(
            ErrorKind.CONFLICT,
            "OBSERVATION_SCENE_CHANGED",
            "Blender scene data changed while the observation bundle was captured",
            retryable=True,
            details={"before": generation_start, "after": generation_end},
        )

    context_before_identity = _identity(context_before)
    context_after_identity = _identity(context_after)
    if context_before_identity != context_after_identity:
        raise observation_error(
            ErrorKind.CONFLICT,
            "OBSERVATION_CONTEXT_DRIFT",
            "Blender user context changed while the observation bundle was captured",
            retryable=True,
            details={
                "changed_fields": _changed_fields(
                    context_before_identity,
                    context_after_identity,
                )
            },
        )

    object_before_identity = _identity(object_before)
    object_after_identity = _identity(object_after)
    if object_before_identity != object_after_identity:
        raise observation_error(
            ErrorKind.CONFLICT,
            "OBSERVATION_SCENE_CHANGED",
            "The observed object changed while the bundle was captured",
            retryable=True,
            details={
                "changed_fields": _changed_fields(
                    object_before_identity,
                    object_after_identity,
                )
            },
        )

    hashes: defaultdict[str, list[str]] = defaultdict(list)
    for capture in captures:
        hashes[str(capture["sha256"])].append(str(capture["view"]))
    duplicate_views = [group for group in hashes.values() if len(group) > 1]
    warnings = []
    if duplicate_views:
        warnings.append(
            {
                "code": "DUPLICATE_VIEW_HASHES",
                "views": duplicate_views,
            }
        )
    return images, {
        "object_name": object_name,
        "views": list(views),
        "context_before": context_before,
        "context_after": context_after,
        "object_before": object_before,
        "object_after": object_after,
        "captures": captures,
        "context_unchanged": True,
        "object_unchanged": True,
        "scene_generation_start": generation_start,
        "scene_generation_end": generation_end,
        "scene_generation": generation_end,
        "heartbeat_before": int(ping_before["heartbeat"]),
        "heartbeat_after": int(ping_after["heartbeat"]),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "warnings": warnings,
    }
