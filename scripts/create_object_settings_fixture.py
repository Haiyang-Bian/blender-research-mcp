"""Create a deterministic Light/Camera fixture for the 0.9 live smoke."""

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


def point_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def create_camera(
    name: str,
    location: tuple[float, float, float],
    *,
    camera_type: str,
) -> bpy.types.Object:
    data = bpy.data.cameras.new(f"{name} Data")
    data.type = camera_type
    data.lens = 50.0
    data.ortho_scale = 8.0
    data.clip_start = 0.1
    data.clip_end = 1000.0
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    point_at(obj, (0.0, 0.0, 0.8))
    return obj


def create_light(
    name: str,
    light_type: str,
    location: tuple[float, float, float],
    energy: float,
) -> bpy.types.Object:
    data = bpy.data.lights.new(f"{name} Data", type=light_type)
    data.energy = energy
    data.color = (0.8, 0.9, 1.0)
    if light_type in {"POINT", "SPOT"}:
        data.shadow_soft_size = 0.35
    if light_type == "SPOT":
        data.spot_size = math.radians(45.0)
        data.spot_blend = 0.2
    if light_type == "AREA":
        data.shape = "SQUARE"
        data.size = 3.0
    if light_type == "SUN":
        data.angle = math.radians(0.5)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    if light_type in {"SPOT", "AREA"}:
        point_at(obj, (0.0, 0.0, 0.5))
    return obj


def material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    value = bpy.data.materials.new(name)
    value.use_nodes = True
    node = value.node_tree.nodes.get("Principled BSDF")
    if node is not None:
        node.inputs["Base Color"].default_value = color
        node.inputs["Roughness"].default_value = 0.45
    return value


def main() -> None:
    args = arguments()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    bpy.ops.mesh.primitive_plane_add(size=20.0, location=(0.0, 0.0, 0.0))
    floor = bpy.context.object
    floor.name = "Floor"
    floor.data.materials.append(material("Floor Material", (0.03, 0.05, 0.09, 1.0)))

    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 1.0))
    cube = bpy.context.object
    cube.name = "Cube"
    cube.data.materials.append(material("Cube Material", (0.55, 0.65, 0.8, 1.0)))

    create_light("Point Light", "POINT", (3.0, -4.0, 5.0), 900.0)
    create_light("Spot Light", "SPOT", (-4.0, -4.0, 6.0), 1200.0)
    create_light("Sun Light", "SUN", (0.0, 0.0, 6.0), 2.0)
    create_light("Area Light", "AREA", (4.0, 2.0, 6.0), 1000.0)

    shared_light_data = bpy.data.lights.new("Shared Point Data", type="POINT")
    shared_light_data.energy = 500.0
    for index, location in enumerate(((-3.0, 2.0, 3.0), (3.0, 2.0, 3.0)), start=1):
        obj = bpy.data.objects.new(f"Shared Point {index}", shared_light_data)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = location

    perspective = create_camera("Perspective Camera", (8.0, -10.0, 7.0), camera_type="PERSP")
    create_camera("Orthographic Camera", (-8.0, -10.0, 7.0), camera_type="ORTHO")

    shared_camera_data = bpy.data.cameras.new("Shared Camera Data")
    shared_camera_data.type = "PERSP"
    shared_camera_data.lens = 55.0
    for index, location in enumerate(((-5.0, -8.0, 5.0), (5.0, -8.0, 5.0)), start=1):
        obj = bpy.data.objects.new(f"Shared Camera {index}", shared_camera_data)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = location
        point_at(obj, (0.0, 0.0, 1.0))

    world = bpy.data.worlds.new("Object Settings World")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.015, 0.025, 0.06, 1.0)
        background.inputs["Strength"].default_value = 0.3
    bpy.context.scene.world = world
    bpy.context.scene.camera = perspective
    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    bpy.context.scene.render.resolution_x = 320
    bpy.context.scene.render.resolution_y = 256
    bpy.context.scene.render.resolution_percentage = 100
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.render.film_transparent = False
    bpy.context.view_layer.objects.active = cube
    cube.select_set(True)
    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save fixture: {sorted(result)}")


if __name__ == "__main__":
    main()
