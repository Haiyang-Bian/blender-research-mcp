"""Build the Blender 4.2-compatible development add-on ZIP."""

from __future__ import annotations

import ast
import re
import tomllib
import zipfile
from pathlib import Path

PACKAGE_NAME = "blender_research_mcp_addon"
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "blender_addon" / PACKAGE_NAME
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _literal_assignment(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return ast.literal_eval(node.value)
    raise ValueError(f"{name} is missing from {path}")


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        value = tomllib.load(handle)["project"]["version"]
    if not isinstance(value, str):
        raise ValueError("project.version must be a string")
    return value


def addon_runtime_version() -> str:
    value = _literal_assignment(SOURCE / "runtime.py", "ADDON_VERSION")
    if not isinstance(value, str):
        raise ValueError("ADDON_VERSION must be a string")
    return value


def addon_manifest_version() -> str:
    value = _literal_assignment(SOURCE / "__init__.py", "bl_info")
    if not isinstance(value, dict) or not isinstance(value.get("version"), tuple):
        raise ValueError("bl_info.version must be a tuple")
    version = value["version"]
    if len(version) != 3 or not all(isinstance(item, int) for item in version):
        raise ValueError("bl_info.version must contain three integers")
    return ".".join(str(item) for item in version)


def resolve_build_version(expected_version: str | None = None) -> str:
    versions = {
        "project": project_version(),
        "add-on runtime": addon_runtime_version(),
        "add-on manifest": addon_manifest_version(),
    }
    for source, version in versions.items():
        if VERSION_PATTERN.fullmatch(version) is None:
            raise ValueError(f"{source} version is not X.Y.Z: {version!r}")
    if len(set(versions.values())) != 1:
        details = ", ".join(f"{source}={version}" for source, version in versions.items())
        raise ValueError(f"version mismatch: {details}")
    version = versions["project"]
    if expected_version is not None and expected_version != version:
        raise ValueError(
            f"requested version {expected_version!r} does not match project {version!r}"
        )
    return version


def default_output(version: str) -> Path:
    return ROOT / "artifacts" / f"blender-research-mcp-addon-{version}.zip"


def build(output: Path | None = None, *, expected_version: str | None = None) -> Path:
    version = resolve_build_version(expected_version)
    if output is None:
        output = default_output(version)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(SOURCE.rglob("*.py")):
            archive.write(path, Path(PACKAGE_NAME) / path.relative_to(SOURCE))
    return output
