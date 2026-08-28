"""Build the Blender 4.2-compatible development add-on ZIP."""

from __future__ import annotations

import argparse
from pathlib import Path

from blender_research_mcp.addon_build import DEFAULT_OUTPUT, build


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(build(args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
