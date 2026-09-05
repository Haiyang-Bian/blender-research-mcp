"""Create the deterministic complementary open-Mesh fixture for the 0.17 live gate."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(values)


def material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    value = bpy.data.materials.new(name)
    value.use_nodes = True
    value.diffuse_color = color
    principled = value.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = color
    principled.inputs["Roughness"].default_value = 0.34
    return value


def half_mesh(name: str, *, left: bool, shared: bpy.types.Material) -> bpy.types.Mesh:
    x0, x1 = (-1.0, 0.0) if left else (0.0, 1.0)
    vertices = [
        (x0, -1.0, -1.0),
        (x1, -1.0, -1.0),
        (x1, 1.0, -1.0),
        (x0, 1.0, -1.0),
        (x0, -1.0, 1.0),
        (x1, -1.0, 1.0),
        (x1, 1.0, 1.0),
        (x0, 1.0, 1.0),
    ]
    faces = [
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (3, 7, 6, 2),
        (0, 4, 7, 3) if left else (1, 2, 6, 5),
    ]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    mesh.materials.append(shared)

    uv = mesh.uv_layers.new(name="JoinUV", do_init=False)
    for polygon in mesh.polygons:
        for corner, loop_index in enumerate(polygon.loop_indices):
            uv.data[loop_index].uv = (
                1.0 if corner in {1, 2} else 0.0,
                1.0 if corner >= 2 else 0.0,
            )

    color = mesh.color_attributes.new(name="ModuleTint", type="BYTE_COLOR", domain="CORNER")
    tint = (0.2, 0.55, 0.95, 1.0) if left else (0.95, 0.28, 0.18, 1.0)
    for item in color.data:
        item.color = tint
    return mesh


def point_camera(camera: bpy.types.Object, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def main() -> None:
    output = arguments().output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    sources = bpy.data.collections.new("Join Sources")
    bpy.context.scene.collection.children.link(sources)
    shared = material("Join Shared Material", (0.14, 0.42, 0.82, 1.0))
    matrix_values = {
        "location": (1.25, -0.75, 0.4),
        "rotation_euler": (math.radians(7.0), math.radians(-11.0), math.radians(23.0)),
        "scale": (1.25, 0.8, 1.1),
    }
    for name, left in (("Join Left", True), ("Join Right", False)):
        mesh = half_mesh(f"{name} Mesh", left=left, shared=shared)
        obj = bpy.data.objects.new(name, mesh)
        sources.objects.link(obj)
        obj.location = matrix_values["location"]
        obj.rotation_euler = matrix_values["rotation_euler"]
        obj.scale = matrix_values["scale"]
        group = obj.vertex_groups.new(name="Root")
        group.add(list(range(len(mesh.vertices))), 0.75 if left else 0.5, "REPLACE")

    camera_data = bpy.data.cameras.new("Join Camera Data")
    camera = bpy.data.objects.new("Join Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (7.5, -9.5, 6.2)
    camera_data.lens = 52.0
    point_camera(camera, (1.25, -0.75, 0.4))
    bpy.context.scene.camera = camera

    key_data = bpy.data.lights.new("Join Key Data", type="AREA")
    key_data.energy = 1250.0
    key_data.shape = "DISK"
    key_data.size = 5.0
    key = bpy.data.objects.new("Join Key", key_data)
    bpy.context.scene.collection.objects.link(key)
    key.location = (3.0, -4.0, 7.0)
    point_camera(key, (1.25, -0.75, 0.4))

    world = bpy.data.worlds.new("Join World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (
        0.018,
        0.025,
        0.045,
        1.0,
    )
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.35
    bpy.context.scene.world = world

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 384
    scene.render.resolution_y = 384
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"

    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save Join fixture: {sorted(result)}")


if __name__ == "__main__":
    main()
