"""Install the repository-owned Blender workflow skill into Codex."""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "blender-research-workflow"
SOURCE = ROOT / "skills" / SKILL_NAME
MARKER = ".blender-research-source.json"
SOURCE_ID = "blender-research-mcp/blender-research-workflow"


def default_target_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    return Path(codex_home) / "skills" if codex_home else Path.home() / ".codex" / "skills"


def _owned(target: Path) -> bool:
    try:
        marker = json.loads((target / MARKER).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return marker.get("id") == SOURCE_ID


def _same_tree(left: Path, right: Path) -> bool:
    comparison = filecmp.dircmp(left, right, ignore=["__pycache__"])
    if comparison.left_only or comparison.right_only or comparison.funny_files:
        return False
    if any(
        not filecmp.cmp(left / name, right / name, shallow=False)
        for name in comparison.common_files
    ):
        return False
    return all(_same_tree(left / name, right / name) for name in comparison.common_dirs)


def install(target_root: Path, *, check: bool) -> Path:
    source = SOURCE.resolve(strict=True)
    target_root = target_root.expanduser().resolve()
    target = target_root / SKILL_NAME
    if check:
        if not target.is_dir() or not _owned(target) or not _same_tree(source, target):
            raise RuntimeError(f"installed skill is missing or out of sync: {target}")
        return target
    if target.exists() and not _owned(target):
        raise RuntimeError(f"refusing to overwrite an unmanaged skill: {target}")

    target_root.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}-", dir=target_root))
    staging = staging_root / SKILL_NAME
    backup = target_root / f".{SKILL_NAME}.backup"
    try:
        shutil.copytree(source, staging)
        if target.exists():
            if backup.exists():
                raise RuntimeError(f"stale skill backup requires manual inspection: {backup}")
            target.rename(backup)
        staging.rename(target)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if not target.exists() and backup.exists():
            backup.rename(target)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", type=Path, default=default_target_root())
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    print(install(args.target_root, check=args.check))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
