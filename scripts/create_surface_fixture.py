"""Create the deterministic Blender 4.2 fixture for the 0.12 live smoke."""

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
    node = value.node_tree.nodes.get("Principled BSDF")
    if node is not None:
        node.inputs["Base Color"].default_value = color
        node.inputs["Roughness"].default_value = 0.32
    return value


def add_color(mesh: bpy.types.Mesh) -> None:
    color = mesh.color_attributes.new(name="Fixture Color", type="FLOAT_COLOR", domain="POINT")
    denominator = max(1, len(color.data) - 1)
    for index, item in enumerate(color.data):
        amount = index / denominator
        item.color = (0.1 + amount * 0.3, 0.25, 0.75 - amount * 0.25, 1.0)


def point_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def main() -> None:
    args = arguments()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    source_material = material("Fit Source Material", (0.08, 0.32, 0.82, 1.0))
    target_material = material("Fit Target Material", (0.8, 0.16, 0.08, 1.0))
    plane_material = material("Fit Plane Material", (0.08, 0.7, 0.28, 1.0))

    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=2.0)
    target = bpy.context.object
    target.name = "Evaluated Target"
    target.data.name = "Evaluated Target Data"
    target.data.materials.append(target_material)
    target.shape_key_add(name="Basis")
    bulge = target.shape_key_add(name="Fit Bulge")
    for index, point in enumerate(bulge.data):
        direction = point.co.normalized()
        wave = 0.12 * math.sin(index * 0.37) * max(0.0, direction.z)
        point.co += direction * wave
    bulge.value = 0.65
    subdivision = target.modifiers.new(name="Evaluated Subdivision", type="SUBSURF")
    subdivision.levels = 1
    subdivision.render_levels = 1

    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=2.35)
    source = bpy.context.object
    source.name = "Fit Source"
    source.data.name = "Fit Source Data"
    source.scale = (1.0, 0.88, 1.08)
    source.location.z = 0.18
    source.data.materials.append(source_material)
    add_color(source.data)

    bpy.ops.mesh.primitive_grid_add(x_subdivisions=17, y_subdivisions=17, size=4.0)
    shared_a = bpy.context.object
    shared_a.name = "Fit Shared A"
    shared_a.data.name = "Fit Shared Data"
    shared_a.location = (-5.0, 0.0, 0.4)
    shared_a.data.materials.append(source_material)
    add_color(shared_a.data)
    shared_b = shared_a.copy()
    shared_b.name = "Fit Shared B"
    shared_b.location = (5.0, 0.0, 0.4)
    bpy.context.scene.collection.objects.link(shared_b)

    bpy.ops.mesh.primitive_grid_add(x_subdivisions=9, y_subdivisions=9, size=3.0)
    plane = bpy.context.object
    plane.name = "Query Plane"
    plane.data.name = "Query Plane Data"
    plane.location = (0.0, 5.0, 0.5)
    plane.data.materials.append(plane_material)

    camera_data = bpy.data.cameras.new("Fit Camera Data")
    camera_data.lens = 52.0
    camera = bpy.data.objects.new("Fit Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (10.0, -16.0, 9.0)
    point_at(camera, (0.0, 0.5, 0.4))
    bpy.context.scene.camera = camera

    key_data = bpy.data.lights.new("Fit Key Data", type="AREA")
    key_data.energy = 1800.0
    key_data.shape = "DISK"
    key_data.size = 7.0
    key = bpy.data.objects.new("Fit Key", key_data)
    bpy.context.scene.collection.objects.link(key)
    key.location = (-6.0, -7.0, 10.0)
    point_at(key, (0.0, 0.0, 0.0))

    fill_data = bpy.data.lights.new("Fit Fill Data", type="AREA")
    fill_data.energy = 900.0
    fill_data.color = (0.25, 0.45, 1.0)
    fill_data.size = 6.0
    fill = bpy.data.objects.new("Fit Fill", fill_data)
    bpy.context.scene.collection.objects.link(fill)
    fill.location = (7.0, 3.0, 6.0)
    point_at(fill, (0.0, 0.0, 0.0))

    world = bpy.data.worlds.new("Fit World")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.006, 0.009, 0.025, 1.0)
        background.inputs["Strength"].default_value = 0.3
    bpy.context.scene.world = world
    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    bpy.context.scene.render.resolution_x = 512
    bpy.context.scene.render.resolution_y = 384
    bpy.context.scene.render.resolution_percentage = 100
    bpy.context.scene.render.image_settings.file_format = "PNG"

    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save fixture: {sorted(result)}")


if __name__ == "__main__":
    main()
