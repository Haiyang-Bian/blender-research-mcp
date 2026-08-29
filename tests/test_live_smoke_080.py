import importlib.util
import sys
from pathlib import Path

from PIL import Image


def load_smoke_module():
    path = Path(__file__).parents[1] / "scripts" / "live_smoke_080.py"
    spec = importlib.util.spec_from_file_location("live_smoke_080_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_transform_builds_complete_absolute_xyz_payload() -> None:
    smoke = load_smoke_module()

    assert smoke.transform((1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (7.0, 8.0, 9.0)) == {
        "location": {"x": 1.0, "y": 2.0, "z": 3.0},
        "rotation_euler_degrees": {"x": 4.0, "y": 5.0, "z": 6.0},
        "scale": {"x": 7.0, "y": 8.0, "z": 9.0},
    }


def test_live_smoke_textures_are_deterministic_and_nonblank(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    first_wave = tmp_path / "first-wave.png"
    first_stars = tmp_path / "first-stars.png"
    second_wave = tmp_path / "second-wave.png"
    second_stars = tmp_path / "second-stars.png"

    smoke.make_textures(first_wave, first_stars)
    smoke.make_textures(second_wave, second_stars)

    assert smoke.sha256(first_wave) == smoke.sha256(second_wave)
    assert smoke.sha256(first_stars) == smoke.sha256(second_stars)
    with Image.open(first_wave) as wave:
        assert wave.size == (512, 512)
        assert wave.convert("L").getextrema()[0] < wave.convert("L").getextrema()[1]
    with Image.open(first_stars) as stars:
        assert stars.size == (1024, 512)
        assert stars.convert("L").getextrema()[0] < stars.convert("L").getextrema()[1]
