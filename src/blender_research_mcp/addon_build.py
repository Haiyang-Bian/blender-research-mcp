"""Build the Blender 4.2-compatible development add-on ZIP."""

from __future__ import annotations

import zipfile
from pathlib import Path

PACKAGE_NAME = "blender_research_mcp_addon"
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "blender_addon" / PACKAGE_NAME
DEFAULT_OUTPUT = ROOT / "artifacts" / "blender-research-mcp-addon-0.4.0.zip"


def build(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(SOURCE.rglob("*.py")):
            archive.write(path, Path(PACKAGE_NAME) / path.relative_to(SOURCE))
    return output
