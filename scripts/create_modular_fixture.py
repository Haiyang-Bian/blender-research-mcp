"""Create the deterministic Blender 4.2 fixture for 0.15 modular Mesh acceptance."""

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


def create_mesh() -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new("Modular Source Data")
    vertices = []
    faces = []
    for island in range(3):
        x = float(island * 3)
        start = len(vertices)
        vertices.extend(
            [
                (x - 1.0, -1.0, 0.0),
                (x + 1.0, -1.0, 0.0),
                (x + 1.0, 1.0, 0.0),
                (x - 1.0, 1.0, 0.0),
            ]
        )
        faces.append((start, start + 1, start + 2, start + 3))
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap", do_init=False)
    for face in mesh.polygons:
        for corner, loop_index in enumerate(face.loop_indices):
            uv.data[loop_index].uv = ((corner == 1 or corner == 2), (corner >= 2))
    material = bpy.data.materials.new("Modular Material")
    material.diffuse_color = (0.2, 0.5, 0.9, 1.0)
    mesh.materials.append(material)
    return mesh


def create_armature() -> bpy.types.Object:
    data = bpy.data.armatures.new("Modular Rig Data")
    rig = bpy.data.objects.new("Modular Rig", data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bone = data.edit_bones.new("Root")
    bone.head = (0.0, 0.0, -1.0)
    bone.tail = (0.0, 0.0, 1.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.select_set(False)
    return rig


def create_render_setup() -> None:
    camera_data = bpy.data.cameras.new("Modular Camera Data")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 11.0
    camera = bpy.data.objects.new("Modular Camera", camera_data)
    camera.location = (3.0, 0.0, 12.0)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera

    light_data = bpy.data.lights.new("Modular Light Data", type="AREA")
    light_data.energy = 1200.0
    light_data.size = 8.0
    light = bpy.data.objects.new("Modular Light", light_data)
    light.location = (3.0, -2.0, 8.0)
    bpy.context.scene.collection.objects.link(light)


def main() -> None:
    output = arguments().output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    mesh = create_mesh()
    source = bpy.data.objects.new("Modular Source", mesh)
    bpy.context.scene.collection.objects.link(source)
    group = source.vertex_groups.new(name="Root")
    group.add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
    source.shape_key_add(name="Basis")
    lift = source.shape_key_add(name="Lift")
    for index in (0, 1, 2, 3, 8, 9, 10, 11):
        lift.data[index].co.z = 0.5
    lift.value = 0.75

    rig = create_armature()
    armature = source.modifiers.new(name="Source Armature", type="ARMATURE")
    armature.object = rig
    solidify = source.modifiers.new(name="Source Solidify", type="SOLIDIFY")
    solidify.thickness = 0.1
    create_render_setup()

    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save modular fixture: {sorted(result)}")


if __name__ == "__main__":
    main()
