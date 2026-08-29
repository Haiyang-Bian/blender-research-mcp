from blender_research_mcp import PROTOCOL_VERSION
from blender_research_mcp.cli import main


def test_protocol_version_starts_at_one() -> None:
    assert PROTOCOL_VERSION == 1


def test_version_command(capsys) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.5.0"
