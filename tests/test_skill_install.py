import importlib.util
from pathlib import Path

import pytest


def load_installer():
    path = Path(__file__).parents[1] / "scripts" / "install_codex_skill.py"
    spec = importlib.util.spec_from_file_location("install_codex_skill_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_copies_and_checks_managed_skill(tmp_path: Path) -> None:
    installer = load_installer()

    target = installer.install(tmp_path, check=False)

    assert (target / "SKILL.md").is_file()
    assert installer.install(tmp_path, check=True) == target


def test_installer_refuses_unmanaged_target(tmp_path: Path) -> None:
    installer = load_installer()
    target = tmp_path / installer.SKILL_NAME
    target.mkdir()
    (target / "SKILL.md").write_text("user-owned", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unmanaged skill"):
        installer.install(tmp_path, check=False)

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "user-owned"
