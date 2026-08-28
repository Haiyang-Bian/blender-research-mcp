"""External-process image decoding and bounded resizing."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image


def resize_png(data: bytes, max_size: int) -> tuple[bytes, dict[str, Any]]:
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        native_size = source.size
        image = source.convert("RGBA")
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue(), {
            "native_width": native_size[0],
            "native_height": native_size[1],
            "width": image.width,
            "height": image.height,
        }
