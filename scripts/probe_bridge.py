"""Probe the Blender transport directly without starting an MCP stdio server."""

from __future__ import annotations

import argparse
import asyncio
import json

from blender_research_mcp.client import BridgeClient


async def probe(port: int, object_name: str | None) -> None:
    client = BridgeClient(port=port)
    try:
        handshake = await client.connect()
        ping = await client.call("connection.ping", read_only=True)
        inspected = None
        if object_name is not None:
            inspected = await client.call(
                "object.inspect",
                {"object_name": object_name},
                read_only=True,
            )
    finally:
        await client.close()
    print(
        json.dumps(
            {
                "handshake": handshake.model_dump(mode="json"),
                "ping": ping,
                "object": inspected,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9877)
    parser.add_argument("--object-name")
    args = parser.parse_args()
    asyncio.run(probe(args.port, args.object_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
