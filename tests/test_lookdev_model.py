import importlib.util
from pathlib import Path

import pytest


def load_lookdev_model():
    path = (
        Path(__file__).parents[1]
        / "blender_addon"
        / "blender_research_mcp_addon"
        / "lookdev_model.py"
    )
    spec = importlib.util.spec_from_file_location("lookdev_model_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_material_value_validation_is_exact_and_bounded() -> None:
    model = load_lookdev_model()

    assert model.normalize_material_value(
        "BOOLEAN", True, minimum=None, maximum=None
    ) is True
    assert model.normalize_material_value("INT", 3, minimum=0, maximum=5) == 3
    assert model.normalize_material_value("FLOAT", 0.5, minimum=0, maximum=1) == 0.5
    assert model.normalize_material_value(
        "VECTOR", [0.1, 0.2, 0.3], minimum=-1, maximum=1
    ) == (0.1, 0.2, 0.3)
    assert model.normalize_material_value(
        "COLOR", [0.1, 0.2, 0.3, 1.0], minimum=0, maximum=1
    ) == (0.1, 0.2, 0.3, 1.0)

    with pytest.raises(model.LookdevModelError, match="floating-point"):
        model.normalize_material_value("FLOAT", 1, minimum=0, maximum=2)
    with pytest.raises(model.LookdevModelError, match="components"):
        model.normalize_material_value("VECTOR", [1.0, 2.0], minimum=0, maximum=2)
    with pytest.raises(model.LookdevModelError, match="outside") as exc_info:
        model.normalize_material_value("COLOR", [0.0, 0.0, 0.0, 1.5], minimum=0, maximum=1)
    assert exc_info.value.code == "MATERIAL_SOCKET_VALUE_OUT_OF_RANGE"


def test_material_value_validation_rejects_nonfinite_and_unsupported_values() -> None:
    model = load_lookdev_model()

    with pytest.raises(model.LookdevModelError) as exc_info:
        model.normalize_material_value("FLOAT", float("nan"), minimum=None, maximum=None)
    assert exc_info.value.code == "MATERIAL_SOCKET_TYPE_MISMATCH"
    with pytest.raises(model.LookdevModelError) as exc_info:
        model.normalize_material_value("SHADER", 1.0, minimum=None, maximum=None)
    assert exc_info.value.code == "MATERIAL_SOCKET_UNSUPPORTED"
