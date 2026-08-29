import hashlib
import io

import pytest
from PIL import Image

from blender_research_mcp.rendering import decode_render_preview


def _png_bytes(*, blank: bool = False) -> bytes:
    image = Image.new("RGB", (256, 256), (20, 40, 60))
    if not blank:
        image.putpixel((128, 128), (220, 230, 240))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _result(data: bytes) -> dict[str, object]:
    import base64

    return {
        "width": 256,
        "height": 256,
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_count": len(data),
        "png_base64": base64.b64encode(data).decode("ascii"),
    }


def test_decode_render_preview_validates_png_size_hash_and_removes_payload() -> None:
    data = _png_bytes()
    decoded, result = decode_render_preview(_result(data))

    assert decoded == data
    assert "png_base64" not in result


def test_decode_render_preview_rejects_blank_or_mismatched_evidence() -> None:
    blank = _png_bytes(blank=True)
    with pytest.raises(ValueError, match="grayscale variation"):
        decode_render_preview(_result(blank))

    data = _png_bytes()
    result = _result(data)
    result["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        decode_render_preview(result)
