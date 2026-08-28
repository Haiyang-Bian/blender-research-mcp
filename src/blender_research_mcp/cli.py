"""Command-line entry point for the external MCP server."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from importlib.metadata import version


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        print(version("blender-research-mcp"))
        return 0

    build_parser().print_help()
    return 0
