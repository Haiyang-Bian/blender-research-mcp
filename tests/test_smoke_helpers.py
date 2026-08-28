import importlib.util
import sys
from pathlib import Path


def load_smoke_module():
    path = Path(__file__).parents[1] / "scripts" / "live_smoke.py"
    spec = importlib.util.spec_from_file_location("live_smoke_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_context_identity_ignores_generation_but_keeps_view() -> None:
    smoke = load_smoke_module()
    first = {
        "scene": "Scene",
        "view": {"distance": 2.0},
        "scene_generation": 1,
    }
    second = {
        "scene": "Scene",
        "view": {"distance": 2.0},
        "scene_generation": 99,
    }

    assert smoke.context_identity(first) == smoke.context_identity(second)


def test_object_identity_ignores_only_generation() -> None:
    smoke = load_smoke_module()
    first = {"name": "目.L", "visible": True, "scene_generation": 1}
    second = {"name": "目.L", "visible": True, "scene_generation": 99}

    assert smoke.object_identity(first) == smoke.object_identity(second)
