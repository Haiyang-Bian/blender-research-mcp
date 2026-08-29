"""Command-line entry point for the external MCP server."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from importlib.metadata import version

from blender_research_mcp.constants import DEFAULT_PORT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blender-research-mcp",
        description="Semantic MCP bridge for Blender research.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the installed package version and exit",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Blender add-on loopback port (default: 9877)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        print(version("blender-research-mcp"))
        return 0
    if not 1 <= args.port <= 65535:
        build_parser().error("--port must be between 1 and 65535")

    from blender_research_mcp.server import create_server

    create_server(port=args.port).run(transport="stdio")
    return 0
