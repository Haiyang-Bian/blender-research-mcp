import base64
import importlib.util
import io
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
