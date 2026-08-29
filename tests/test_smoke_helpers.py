import base64
import importlib.util
import io
import math
import sys
from pathlib import Path

from PIL import Image


def load_smoke_module():
    path = Path(__file__).parents[1] / "scripts" / "live_smoke.py"
    spec = importlib.util.spec_from_file_location("live_smoke_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_051_smoke_module():
    scripts = Path(__file__).parents[1] / "scripts"
    path = scripts / "live_smoke_051.py"
    spec = importlib.util.spec_from_file_location("live_smoke_051_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(scripts))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(scripts))
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


def test_bundle_images_follow_structured_content_indices(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    raw_images = []
    for color in ((10, 20, 30), (30, 20, 10)):
        image = Image.new("RGB", (8, 8), color)
        image.putpixel((0, 0), (255, 255, 255))
        output = io.BytesIO()
        image.save(output, format="PNG")
        raw_images.append(output.getvalue())
    result = smoke.types.CallToolResult(
        content=[
            smoke.types.ImageContent(
                type="image",
                data=base64.b64encode(raw).decode("ascii"),
                mimeType="image/png",
            )
            for raw in raw_images
        ]
    )
    metadata = {
        "captures": [
            {"view": "RIGHT", "content_index": 1},
            {"view": "FRONT", "content_index": 0},
        ]
    }

    hashes = smoke.save_bundle_images(result, metadata, tmp_path, "bundle")

    assert set(hashes) == {"FRONT", "RIGHT"}
    assert (tmp_path / "bundle-front.png").read_bytes() == raw_images[0]
    assert (tmp_path / "bundle-right.png").read_bytes() == raw_images[1]


def test_maximum_pixel_difference_reports_largest_channel_delta(tmp_path: Path) -> None:
    smoke = load_smoke_module()
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGBA", (2, 2), (10, 20, 30, 255)).save(first)
    Image.new("RGBA", (2, 2), (10, 24, 30, 255)).save(second)

    assert smoke.maximum_pixel_difference(first, second) == 4


def test_image_difference_statistics_ignore_alpha_and_report_structure(
    tmp_path: Path,
) -> None:
    smoke = load_smoke_module()
    first = tmp_path / "first.png"
    identical = tmp_path / "identical.png"
    changed = tmp_path / "changed.png"
    Image.new("RGBA", (16, 8), (10, 20, 30, 255)).save(first)
    Image.new("RGBA", (16, 8), (10, 20, 30, 0)).save(identical)
    Image.new("RGBA", (16, 8), (50, 60, 70, 255)).save(changed)

    assert smoke.image_difference_statistics(first, identical) == {
        "max_channel_difference": 0,
        "mean_absolute_difference": 0.0,
        "rms_difference": 0.0,
        "structure_mean_absolute_difference": 0.0,
    }
    statistics = smoke.image_difference_statistics(first, changed)
    assert statistics["mean_absolute_difference"] == 40.0
    assert statistics["structure_mean_absolute_difference"] == 40.0
    assert smoke.images_match_within_render_noise(statistics) is False
    assert smoke.images_match_within_render_noise(
        smoke.image_difference_statistics(first, identical)
    ) is True


def test_validate_raycast_accepts_finite_unit_vectors() -> None:
    smoke = load_smoke_module()
    result = {
        "ray": {
            "origin": [1.0, 2.0, 3.0],
            "direction": [0.0, 0.0, -1.0],
            "max_distance": 100.0,
        },
        "hit": True,
        "hit_object": {"name": "网格", "type": "MESH"},
        "location": [1.0, 2.0, 0.0],
        "normal": [0.0, 0.0, 1.0],
        "face_index": 4,
        "distance": 3.0,
    }

    smoke.validate_raycast(result)


def test_validate_raycast_rejects_non_finite_data() -> None:
    smoke = load_smoke_module()
    result = {
        "ray": {
            "origin": [0.0, 0.0, 0.0],
            "direction": [0.0, math.nan, 1.0],
            "max_distance": 100.0,
        },
        "hit": False,
    }

    try:
        smoke.validate_raycast(result, require_hit=False)
    except RuntimeError as exc:
        assert "non-finite" in str(exc)
    else:
        raise AssertionError("non-finite raycast vectors must be rejected")


def test_validate_geometry_summary_checks_counts_and_bounds() -> None:
    smoke = load_smoke_module()
    result = {
        "counts": {
            "vertices": 8,
            "edges": 12,
            "polygons": 6,
            "loop_triangles": 12,
        },
        "local_bounds": [[float(index), 0.0, 0.0] for index in range(8)],
        "world_bounds": [[float(index), 1.0, 0.0] for index in range(8)],
    }

    smoke.validate_geometry_summary(result)


def test_051_material_preview_values_preserve_type_and_range() -> None:
    smoke = load_051_smoke_module()

    assert smoke.choose_material_value(
        {"socket_kind": "BOOLEAN", "value": False, "minimum": None, "maximum": None}
    ) is True
    assert smoke.choose_material_value(
        {"socket_kind": "INT", "value": 2, "minimum": 0, "maximum": 3}
    ) == 3
    assert math.isclose(
        smoke.choose_material_value(
            {"socket_kind": "FLOAT", "value": 0.5, "minimum": 0.0, "maximum": 1.0}
        ),
        0.55,
    )
    vector = smoke.choose_material_value(
        {
            "socket_kind": "VECTOR",
            "value": [0.0, 0.5, 1.0],
            "minimum": -1.0,
            "maximum": 1.0,
        }
    )
    assert vector == [0.1, 0.5, 1.0]
    assert all(type(component) is float for component in vector)


def test_051_shape_key_selection_skips_drivers_and_fixed_ranges() -> None:
    smoke = load_051_smoke_module()
    lookdev = {
        "name": "脸",
        "shape_keys": [
            {
                "name": "Driven",
                "value": 0.0,
                "slider_min": 0.0,
                "slider_max": 1.0,
                "driven": True,
            },
            {
                "name": "Fixed",
                "value": 0.5,
                "slider_min": 0.5,
                "slider_max": 0.5,
                "driven": False,
            },
            {
                "name": "Smile",
                "value": 0.0,
                "slider_min": 0.0,
                "slider_max": 1.0,
                "driven": False,
            },
        ],
    }

    target, value = smoke.choose_shape_key(lookdev)

    assert target["name"] == "Smile"
    assert value == 0.1
