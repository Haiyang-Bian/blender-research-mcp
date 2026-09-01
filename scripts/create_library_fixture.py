"""Create the deterministic static .blend Library used by the 0.16 live gate."""

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


def template_mesh(name: str, *, scale: float) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(name)
    vertices = [
        (-scale, -scale, -scale),
        (scale, -scale, -scale),
        (scale, scale, -scale),
        (-scale, scale, -scale),
        (-scale, -scale, scale),
        (scale, -scale, scale),
        (scale, scale, scale),
        (-scale, scale, scale),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (4, 0, 3, 7),
    ]
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap", do_init=False)
    for polygon in mesh.polygons:
        for corner, loop_index in enumerate(polygon.loop_indices):
            uv.data[loop_index].uv = (
                1.0 if corner in {1, 2} else 0.0,
                1.0 if corner >= 2 else 0.0,
            )
    material = bpy.data.materials.get("Template Skin")
    if material is None:
        material = bpy.data.materials.new("Template Skin")
        material.use_nodes = True
        material.diffuse_color = (0.45, 0.65, 0.9, 1.0)
    mesh.materials.append(material)
    return mesh


def armature() -> bpy.types.Object:
    data = bpy.data.armatures.new("Template Rig Data")
    rig = bpy.data.objects.new("Template Rig", data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    root = data.edit_bones.new("Root")
    root.head = (0.0, 0.0, -2.0)
    root.tail = (0.0, 0.0, 2.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.select_set(False)
    return rig


def main() -> None:
    output = arguments().output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    assembly = bpy.data.collections.new("Template Assembly")
    nested = bpy.data.collections.new("Template Nested")
    bpy.context.scene.collection.children.link(assembly)
    assembly.children.link(nested)

    rig = armature()
    for collection in tuple(rig.users_collection):
        collection.objects.unlink(rig)
    assembly.objects.link(rig)

    head_mesh = template_mesh("Template Head Mesh", scale=1.0)
    head = bpy.data.objects.new("Template Head", head_mesh)
    head.location = (0.0, 0.0, 1.5)
    assembly.objects.link(head)
    head_group = head.vertex_groups.new(name="Root")
    head_group.add(list(range(len(head_mesh.vertices))), 1.0, "REPLACE")
    head_rig = head.modifiers.new(name="Template Armature", type="ARMATURE")
    head_rig.object = rig

    body_mesh = template_mesh("Template Body Mesh", scale=1.5)
    body = bpy.data.objects.new("Template Body", body_mesh)
    body.scale = (1.0, 0.75, 1.75)
    nested.objects.link(body)
    body_group = body.vertex_groups.new(name="Root")
    body_group.add(list(range(len(body_mesh.vertices))), 1.0, "REPLACE")

    loose_mesh = template_mesh("Loose Template Mesh", scale=0.5)
    loose = bpy.data.objects.new("Loose Template Carrier", loose_mesh)
    loose.location = (4.0, 0.0, 0.0)
    bpy.context.scene.collection.objects.link(loose)

    unsupported_mesh = template_mesh("Unsupported Mesh", scale=0.25)
    unsupported = bpy.data.objects.new("Unsupported Constrained", unsupported_mesh)
    unsupported.constraints.new(type="COPY_LOCATION")
    bpy.context.scene.collection.objects.link(unsupported)

    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save Library fixture: {sorted(result)}")


if __name__ == "__main__":
    main()
