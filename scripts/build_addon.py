"""Build the Blender 4.2-compatible development add-on ZIP."""

from __future__ import annotations

import argparse
from pathlib import Path

from blender_research_mcp.addon_build import build


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        dest="expected_version",
        help="expected X.Y.Z version; fails if project and add-on metadata disagree",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve() if args.output is not None else None
    try:
        built = build(output, expected_version=args.expected_version)
    except ValueError as error:
        parser.error(str(error))
    print(built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
