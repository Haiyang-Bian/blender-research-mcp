import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from blender_research_mcp import addon_build
from blender_research_mcp.constants import PACKAGE_VERSION

ROOT = Path(__file__).parents[1]


def load_quality_gate_module():
    path = ROOT / "scripts" / "quality_gate.py"
    spec = importlib.util.spec_from_file_location("quality_gate_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_version_sources_are_synchronized() -> None:
    assert PACKAGE_VERSION == "0.12.0"
    assert addon_build.project_version() == "0.12.0"
    assert addon_build.addon_runtime_version() == "0.12.0"
    assert addon_build.addon_manifest_version() == "0.12.0"
    assert addon_build.resolve_build_version("0.12.0") == "0.12.0"
    assert addon_build.default_output("0.12.0").name == "blender-research-mcp-addon-0.12.0.zip"


def test_build_rejects_requested_version_mismatch() -> None:
    with pytest.raises(ValueError, match="does not match project"):
        addon_build.resolve_build_version("0.5.0")


def test_quality_gate_uses_authoritative_uv_commands() -> None:
    quality_gate = load_quality_gate_module()

    assert quality_gate.gate_commands(("tests/test_server.py",)) == (
        ("uv", "run", "--no-sync", "pytest", "tests/test_server.py"),
        ("uv", "run", "--no-sync", "ruff", "check", "."),
        ("uv", "run", "--no-sync", "mypy"),
    )


def test_shared_pycharm_run_configurations_are_uv_backed() -> None:
    configurations: dict[str, tuple[str, str]] = {}
    for path in (ROOT / ".run").glob("*.run.xml"):
        configuration = ET.parse(path).getroot().find("configuration")
        assert configuration is not None
        name = configuration.attrib["name"]
        script = next(
            (
                option.attrib["value"]
                for option in configuration.findall("option")
                if option.attrib.get("name") == "SCRIPT_TEXT"
            ),
            "",
        )
        configurations[name] = (configuration.attrib["type"], script)

    assert configurations == {
        "Build - Add-on (version)": (
            "ShConfigurationType",
            (
                'uv run --no-sync python scripts/build_addon.py --version '
                '"$Prompt:Release version (for example 0.12.0)$"'
            ),
        ),
        "Tests - Pytest": ("tests", ""),
        "Tests - Full Quality Gate": (
            "ShConfigurationType",
            "uv run --no-sync python scripts/quality_gate.py",
        ),
    }

    pytest_configuration = ET.parse(ROOT / ".run" / "Tests_Pytest.run.xml").getroot().find(
        "configuration"
    )
    assert pytest_configuration is not None
    options = {
        option.attrib["name"]: option.attrib["value"]
        for option in pytest_configuration.findall("option")
        if "name" in option.attrib and "value" in option.attrib
    }
    assert pytest_configuration.attrib["factoryName"] == "py.test"
    assert options["SDK_HOME"] == "$PROJECT_DIR$/.venv/Scripts/python.exe"
    assert options["WORKING_DIRECTORY"] == "$PROJECT_DIR$"
    assert options["_new_target"] == '"$PROJECT_DIR$/tests"'
