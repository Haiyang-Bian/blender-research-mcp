import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_smoke_module():
    path = ROOT / "scripts" / "live_smoke_090.py"
    spec = importlib.util.spec_from_file_location("live_smoke_090_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def inspected(kind: str, data_type: str) -> dict[str, object]:
    return {
        "name": "Target",
        "session_identity": "object:1",
        "data": {
            "session_identity": f"{kind}:1",
            "users": 2,
            "settings": {f"{kind}_type": data_type},
        },
    }


def test_live_smoke_builds_exact_shared_light_and_camera_patches() -> None:
    smoke = load_smoke_module()

    light = smoke.data_patch(
        inspected("light", "AREA"),
        "light",
        {"shape": "RECTANGLE", "size": 4.0, "size_y": 2.0},
        allow_shared_data=True,
    )
    camera = smoke.data_patch(
        inspected("camera", "PERSP"),
        "camera",
        {"lens": 65.0},
    )

    assert light == {
        "type": "light",
        "expected_data_identity": "light:1",
        "expected_data_users": 2,
        "expected_light_type": "AREA",
        "allow_shared_data": True,
        "shape": "RECTANGLE",
        "size": 4.0,
        "size_y": 2.0,
    }
    assert camera["expected_camera_type"] == "PERSP"
    assert camera["allow_shared_data"] is False


def test_live_smoke_covers_required_object_setting_domains() -> None:
    source = (ROOT / "scripts" / "live_smoke_090.py").read_text(encoding="utf-8")
    for expected in (
        '"Point Light"',
        '"Spot Light"',
        '"Sun Light"',
        '"Area Light"',
        '"Perspective Camera"',
        '"Orthographic Camera"',
        '"SHARED_OBJECT_DATA_CONFIRMATION_REQUIRED"',
        '"object_setting"',
        '"transaction.commit"',
        "project_reload",
        "request_render_preview",
        "source_unchanged",
    ):
        assert expected in source

    fixture = ROOT / "scripts" / "create_object_settings_fixture.py"
    ast.parse(fixture.read_text(encoding="utf-8"), filename=str(fixture), feature_version=(3, 11))
