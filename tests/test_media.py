import io

from PIL import Image

from blender_research_mcp.media import resize_png


def test_resize_png_preserves_native_metadata_and_bounds_output() -> None:
    source = Image.new("RGB", (2000, 1000), (10, 20, 30))
    raw = io.BytesIO()
    source.save(raw, format="PNG")

    resized, metadata = resize_png(raw.getvalue(), 800)

    assert metadata == {
        "native_width": 2000,
        "native_height": 1000,
        "width": 800,
        "height": 400,
    }
    with Image.open(io.BytesIO(resized)) as result:
        assert result.size == (800, 400)
