"""Command-line entry point for the external MCP server."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from blender_research_mcp.constants import DEFAULT_PORT, PACKAGE_VERSION
from blender_research_mcp.lifecycle import resolve_launch_timeout


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
    parser.add_argument(
        "--blender-executable",
        help=(
            "Blender executable used by application.launch "
            "(overrides BLENDER_RESEARCH_MCP_BLENDER_EXECUTABLE)"
        ),
    )
    parser.add_argument(
        "--launch-timeout",
        type=float,
        default=None,
        help=(
            "seconds to wait for application.launch "
            "(default: BLENDER_RESEARCH_MCP_LAUNCH_TIMEOUT_SECONDS or 90)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        print(PACKAGE_VERSION)
        return 0
    if not 1 <= args.port <= 65535:
        build_parser().error("--port must be between 1 and 65535")

    try:
        launch_timeout = resolve_launch_timeout(args.launch_timeout)
    except ValueError as exc:
        build_parser().error(str(exc))

    from blender_research_mcp.server import create_server

    create_server(
        port=args.port,
        blender_executable=args.blender_executable,
        launch_timeout=launch_timeout,
    ).run(transport="stdio")
    return 0
