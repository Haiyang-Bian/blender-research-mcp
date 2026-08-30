"""Create the deterministic Blender 4.2 fixture for the 0.11 Mesh live smoke."""

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
    value.diffuse_color = color
    value.use_nodes = True
    node = value.node_tree.nodes.get("Principled BSDF")
    if node is not None:
        node.inputs["Base Color"].default_value = color
        node.inputs["Roughness"].default_value = 0.34
    return value


def add_protected_data(mesh: bpy.types.Mesh) -> None:
    mesh.uv_layers.new(name="Fixture UV")
    color = mesh.color_attributes.new(
        name="Fixture Color",
        type="FLOAT_COLOR",
        domain="POINT",
    )
    for index, item in enumerate(color.data):
        shade = 0.15 + (index % 4) * 0.12
        item.color = (shade, 0.25, 0.7 - shade * 0.3, 1.0)


def cube(
    name: str,
    location: tuple[float, float, float],
    materials: tuple[bpy.types.Material, bpy.types.Material],
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = f"{name} Data"
    obj.data.materials.append(materials[0])
    obj.data.materials.append(materials[1])
    add_protected_data(obj.data)
    return obj


def point_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def main() -> None:
    args = arguments()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    blue = material("Mesh Blue", (0.06, 0.22, 0.62, 1.0))
    gold = material("Mesh Gold", (0.82, 0.32, 0.05, 1.0))
    floor_material = material("Mesh Floor", (0.025, 0.035, 0.065, 1.0))
    materials = (blue, gold)

    operation_names = (
        "Transform",
        "Extrude",
        "Inset",
        "Bevel",
        "Delete",
        "Dissolve",
        "Merge",
        "Face Settings",
        "Normals",
    )
    objects = []
    for index, operation_name in enumerate(operation_names):
        row, column = divmod(index, 5)
        objects.append(
            cube(
                f"Mesh {operation_name}",
                (-6.0 + column * 3.0, 2.0 - row * 4.0, 1.0),
                materials,
            )
        )

    evaluation = objects[0].modifiers.new(name="Evaluation Bevel", type="BEVEL")
    evaluation.width = 0.18
    evaluation.segments = 2

    cube("Mesh Conflict", (6.0, -2.0, 1.0), materials)
    shared_a = cube("Mesh Shared A", (-4.5, -6.0, 1.0), materials)
    shared_b = shared_a.copy()
    shared_b.name = "Mesh Shared B"
    shared_b.location = (-1.5, -6.0, 1.0)
    bpy.context.scene.collection.objects.link(shared_b)

    bpy.ops.mesh.primitive_plane_add(size=28.0, location=(0.0, 0.0, -0.02))
    floor = bpy.context.object
    floor.name = "Mesh Floor"
    floor.data.materials.append(floor_material)

    camera_data = bpy.data.cameras.new("Mesh Camera Data")
    camera_data.lens = 52.0
    camera = bpy.data.objects.new("Mesh Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (14.0, -23.0, 16.0)
    point_at(camera, (0.0, -1.5, 0.8))
    bpy.context.scene.camera = camera

    key_data = bpy.data.lights.new("Mesh Key Data", type="AREA")
    key_data.energy = 2100.0
    key_data.shape = "DISK"
    key_data.size = 9.0
    key = bpy.data.objects.new("Mesh Key", key_data)
    bpy.context.scene.collection.objects.link(key)
    key.location = (-5.0, -5.0, 13.0)
    point_at(key, (0.0, -1.0, 0.5))

    fill_data = bpy.data.lights.new("Mesh Fill Data", type="AREA")
    fill_data.energy = 1000.0
    fill_data.color = (0.3, 0.5, 1.0)
    fill_data.size = 8.0
    fill = bpy.data.objects.new("Mesh Fill", fill_data)
    bpy.context.scene.collection.objects.link(fill)
    fill.location = (8.0, 3.0, 8.0)
    point_at(fill, (0.0, -1.0, 1.0))

    world = bpy.data.worlds.new("Mesh World")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.008, 0.012, 0.035, 1.0)
        background.inputs["Strength"].default_value = 0.28
    bpy.context.scene.world = world
    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    bpy.context.scene.render.resolution_x = 512
    bpy.context.scene.render.resolution_y = 384
    bpy.context.scene.render.resolution_percentage = 100
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.render.image_settings.color_mode = "RGBA"

    bpy.ops.object.select_all(action="DESELECT")
    objects[0].select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save fixture: {sorted(result)}")


if __name__ == "__main__":
    main()
