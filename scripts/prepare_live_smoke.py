"""Create a protected temporary Blender integration fixture and evidence record."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / "blender-projects" / "test-model.blend"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_status(repository: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--short", "--branch"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if source.suffix.lower() != ".blend":
        parser.error("--source must be a .blend file")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid.uuid4().hex[:8]}"
    temporary_root = Path(tempfile.gettempdir()).resolve() / "blender-research-mcp-smoke"
    destination_directory = temporary_root / run_id
    destination_directory.mkdir(parents=True, exist_ok=False)
    destination = destination_directory / source.name
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)

    source_hash = sha256(source)
    status = git_status(source.parent)
    shutil.copy2(source, destination)
    copy_hash = sha256(destination)
    if copy_hash != source_hash:
        raise RuntimeError("temporary fixture hash does not match the source")

    record = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "source_file": str(source),
        "source_sha256": source_hash,
        "source_git_status": status,
        "temporary_blend_file": str(destination),
        "temporary_sha256": copy_hash,
        "artifact_directory": str(artifact_directory),
        "addon_zip": str(ROOT / "artifacts" / "blender-research-mcp-addon-0.11.0.zip"),
    }
    (artifact_directory / "preparation.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
