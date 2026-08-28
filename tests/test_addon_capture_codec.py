import importlib.util
import io
from pathlib import Path

import pytest
from PIL import Image


def load_module(name: str):
    path = (
        Path(__file__).parents[1]
        / "blender_addon"
        / "blender_research_mcp_addon"
        / f"{name}.py"
    )
    spec = importlib.util.spec_from_file_location(f"addon_{name}_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bounded_dimensions_preserve_aspect_and_never_upscale() -> None:
    codec = load_module("capture_codec")

    assert codec.bounded_dimensions(2000, 1000, 800) == (800, 400)
    assert codec.bounded_dimensions(320, 200, 800) == (320, 200)
    with pytest.raises(ValueError):
        codec.bounded_dimensions(0, 100, 800)


def test_png_encoder_flips_bottom_up_rgba_rows() -> None:
    codec = load_module("capture_codec")
    bottom_red_top_blue = bytes(
        [
            255,
            0,
            0,
            255,
            255,
            0,
            0,
            255,
            0,
            0,
            255,
            255,
            0,
            0,
            255,
            255,
        ]
    )

    encoded = codec.encode_rgba_png(2, 2, bottom_red_top_blue, bottom_up=True)

    with Image.open(io.BytesIO(encoded)) as image:
        assert image.mode == "RGBA"
        assert image.size == (2, 2)
        assert image.getpixel((0, 0)) == (0, 0, 255, 255)
        assert image.getpixel((0, 1)) == (255, 0, 0, 255)


def test_blank_detection_ignores_alpha_but_accepts_visible_color() -> None:
    codec = load_module("capture_codec")

    assert codec.is_blank_rgba(bytes([0, 0, 0, 255] * 4)) is True
    assert codec.is_blank_rgba(bytes([0, 0, 2, 255] * 4)) is False


def test_generation_classifier_ignores_ui_only_updates() -> None:
    generation = load_module("generation")

    class Update:
        is_updated_transform = False
        is_updated_geometry = False
        is_updated_shading = False

    class Depsgraph:
        updates = [Update()]

    assert generation.has_persistent_scene_update(Depsgraph()) is False
    Depsgraph.updates[0].is_updated_shading = True
    assert generation.has_persistent_scene_update(Depsgraph()) is True
