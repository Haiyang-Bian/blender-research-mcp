"""Probe the Blender transport directly without starting an MCP stdio server."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.errors import BridgeError


async def probe(
    port: int,
    object_name: str | None,
    capture_object: str | None,
    view: str,
    capture_output: Path | None,
) -> None:
    client = BridgeClient(port=port)
    try:
        handshake = await client.connect()
        ping = await client.call("connection.ping", read_only=True)
        context_before = await client.call("context.get", read_only=True)
        inspected = None
        if object_name is not None:
            inspected = await client.call(
                "object.inspect",
                {"object_name": object_name},
                read_only=True,
            )
        capture = None
        capture_error = None
        if capture_object is not None:
            try:
                capture = await client.call(
                    "viewport.capture",
                    {
                        "object_name": capture_object,
                        "view": view,
                        "max_size": 1000,
                    },
                    read_only=True,
                )
                encoded = capture.pop("png_base64")
                png = base64.b64decode(encoded, validate=True)
                capture["png_bytes"] = len(png)
                if capture_output is not None:
                    capture_output.write_bytes(png)
                    capture["png_output"] = str(capture_output.resolve())
            except BridgeError as exc:
                capture_error = exc.error.model_dump(mode="json")
        context_after = await client.call("context.get", read_only=True)
    finally:
        await client.close()
    print(
        json.dumps(
            {
                "handshake": handshake.model_dump(mode="json"),
                "ping": ping,
                "context_before": context_before,
                "object": inspected,
                "capture": capture,
                "capture_error": capture_error,
                "context_after": context_after,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9877)
    parser.add_argument("--object-name")
    parser.add_argument("--capture-object")
    parser.add_argument("--view", default="FRONT")
    parser.add_argument("--capture-output", type=Path)
    args = parser.parse_args()
    asyncio.run(
        probe(
            args.port,
            args.object_name,
            args.capture_object,
            args.view,
            args.capture_output,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
