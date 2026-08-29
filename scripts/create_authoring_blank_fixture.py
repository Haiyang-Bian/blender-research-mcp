"""Create an empty deterministic .blend fixture for the 0.8 live smoke."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(values)


def main() -> None:
    args = arguments()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for image in list(bpy.data.images):
        if image.name != "Render Result":
            bpy.data.images.remove(image)
    bpy.context.scene.world = None
    bpy.context.scene.camera = None
    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    bpy.context.scene.render.resolution_x = 512
    bpy.context.scene.render.resolution_y = 512
    bpy.context.scene.render.resolution_percentage = 100
    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save fixture: {sorted(result)}")


if __name__ == "__main__":
    main()
