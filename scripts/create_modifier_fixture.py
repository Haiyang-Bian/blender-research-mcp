"""Create a deterministic Mesh/Camera fixture for the 0.10 Modifier live smoke."""

from __future__ import annotations

import argparse
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
        node.inputs["Roughness"].default_value = 0.36
        node.inputs["Metallic"].default_value = 0.08
    return value


def cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def point_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def main() -> None:
    args = arguments()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    blue = material("Modifier Blue", (0.09, 0.24, 0.52, 1.0))
    silver = material("Modifier Silver", (0.55, 0.68, 0.82, 1.0))
    orange = material("Cutter Orange", (0.75, 0.18, 0.04, 1.0))
    dark = material("Floor Dark", (0.018, 0.025, 0.045, 1.0))

    targets = [
        cube("Bevel Target", (-4.5, 1.8, 1.0)),
        cube("Subdivision Target", (-1.5, 1.8, 1.0)),
        cube("Boolean Target", (1.5, 1.8, 1.0)),
        cube("Order Target", (4.5, 1.8, 1.0)),
        cube("Cycle A", (-4.5, -2.0, 1.0)),
        cube("Cycle B", (-1.5, -2.0, 1.0)),
        cube("Disconnect Create Target", (4.5, -2.0, 1.0)),
    ]
    for index, obj in enumerate(targets):
        obj.data.materials.append(blue if index % 2 == 0 else silver)

    mirror = targets[3].modifiers.new(name="Legacy Mirror", type="MIRROR")
    mirror.use_axis[0] = True

    bpy.ops.mesh.primitive_grid_add(x_subdivisions=9, y_subdivisions=9, size=2.0)
    solidify = bpy.context.object
    solidify.name = "Solidify Target"
    solidify.location = (1.5, -2.0, 1.0)
    solidify.rotation_euler.x = 1.5707963267948966
    solidify.data.materials.append(silver)

    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.72, depth=3.2)
    cutter = bpy.context.object
    cutter.name = "Boolean Cutter"
    cutter.location = (1.9, 1.8, 1.0)
    cutter.rotation_euler.y = 1.5707963267948966
    cutter.data.materials.append(orange)
    cutter.hide_viewport = True
    cutter.hide_render = True

    shared_a = cube("Shared Mesh A", (1.5, -5.0, 1.0))
    shared_a.data.materials.append(blue)
    shared_b = shared_a.copy()
    shared_b.name = "Shared Mesh B"
    shared_b.location = (4.5, -5.0, 1.0)
    bpy.context.scene.collection.objects.link(shared_b)

    bpy.ops.mesh.primitive_plane_add(size=24.0, location=(0.0, 0.0, -0.02))
    floor = bpy.context.object
    floor.name = "Floor"
    floor.data.materials.append(dark)

    camera_data = bpy.data.cameras.new("Modifier Camera Data")
    camera_data.lens = 48.0
    camera = bpy.data.objects.new("Modifier Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (12.0, -19.0, 13.0)
    point_at(camera, (0.0, 0.0, 0.8))
    bpy.context.scene.camera = camera

    area_data = bpy.data.lights.new("Modifier Key Data", type="AREA")
    area_data.energy = 1800.0
    area_data.shape = "DISK"
    area_data.size = 8.0
    area = bpy.data.objects.new("Modifier Key", area_data)
    bpy.context.scene.collection.objects.link(area)
    area.location = (-3.0, -5.0, 11.0)
    point_at(area, (0.0, 0.0, 0.5))

    fill_data = bpy.data.lights.new("Modifier Fill Data", type="AREA")
    fill_data.energy = 900.0
    fill_data.color = (0.35, 0.55, 1.0)
    fill_data.size = 7.0
    fill = bpy.data.objects.new("Modifier Fill", fill_data)
    bpy.context.scene.collection.objects.link(fill)
    fill.location = (8.0, 2.0, 7.0)
    point_at(fill, (0.0, 0.0, 1.0))

    world = bpy.data.worlds.new("Modifier World")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.008, 0.012, 0.03, 1.0)
        background.inputs["Strength"].default_value = 0.25
    bpy.context.scene.world = world
    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    bpy.context.scene.render.resolution_x = 480
    bpy.context.scene.render.resolution_y = 320
    bpy.context.scene.render.resolution_percentage = 100
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.render.film_transparent = False
    bpy.context.scene.render.image_settings.color_mode = "RGBA"

    bpy.ops.object.select_all(action="DESELECT")
    targets[0].select_set(True)
    bpy.context.view_layer.objects.active = targets[0]
    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save fixture: {sorted(result)}")


if __name__ == "__main__":
    main()
