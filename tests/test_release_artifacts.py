import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def load_verifier():
    path = ROOT / "scripts" / "verify_release_artifacts.py"
    spec = importlib.util.spec_from_file_location("verify_release_artifacts_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_archive(path: Path, names: set[str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"source")


def test_release_verifier_requires_all_addon_and_bootstrap_resources(tmp_path: Path) -> None:
    verifier = load_verifier()
    source_names = verifier.addon_source_names()
    addon_names = {f"{verifier.ADDON_PREFIX}/{name}" for name in source_names}
    wheel_names = {
        verifier.BOOTSTRAP,
        *(f"{verifier.WHEEL_ADDON_PREFIX}/{name}" for name in source_names),
    }
    addon_zip = tmp_path / "addon.zip"
    wheel = tmp_path / "package.whl"
    write_archive(addon_zip, addon_names)
    write_archive(wheel, wheel_names)

    verifier.verify_addon_zip(addon_zip)
    verifier.verify_wheel(wheel)

    write_archive(wheel, wheel_names - {verifier.BOOTSTRAP})
    with pytest.raises(RuntimeError, match="managed_bootstrap"):
        verifier.verify_wheel(wheel)
