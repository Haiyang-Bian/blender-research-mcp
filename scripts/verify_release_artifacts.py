"""Verify that release archives contain every managed Blender runtime resource."""

from __future__ import annotations

import argparse
import zipfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON_SOURCE = ROOT / "blender_addon" / "blender_research_mcp_addon"
ADDON_PREFIX = "blender_research_mcp_addon"
WHEEL_ADDON_PREFIX = "blender_research_mcp/managed_addon/blender_research_mcp_addon"
BOOTSTRAP = "blender_research_mcp/resources/managed_bootstrap.py"


def addon_source_names() -> set[str]:
    return {
        path.relative_to(ADDON_SOURCE).as_posix()
        for path in ADDON_SOURCE.rglob("*.py")
    }


def _archive_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def verify_addon_zip(path: Path) -> None:
    names = _archive_names(path)
    expected = {f"{ADDON_PREFIX}/{name}" for name in addon_source_names()}
    missing = sorted(expected - names)
    if missing:
        raise RuntimeError(f"add-on ZIP is missing resources: {', '.join(missing)}")


def verify_wheel(path: Path) -> None:
    names = _archive_names(path)
    expected = {
        BOOTSTRAP,
        *(f"{WHEEL_ADDON_PREFIX}/{name}" for name in addon_source_names()),
    }
    missing = sorted(expected - names)
    if missing:
        raise RuntimeError(f"wheel is missing managed resources: {', '.join(missing)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--addon-zip", type=Path, required=True)
    args = parser.parse_args(argv)
    verify_wheel(args.wheel.resolve(strict=True))
    verify_addon_zip(args.addon_zip.resolve(strict=True))
    print(f"verified wheel: {args.wheel}")
    print(f"verified add-on ZIP: {args.addon_zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
