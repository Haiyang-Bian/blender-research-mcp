"""Create the deterministic Blender 4.2 fixture for the 0.14 attribute smoke."""

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


def mesh_data(name: str) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(f"{name} Data")
    mesh.from_pydata(
        [
            (-1.0, -1.0, 0.0),
            (0.0, -1.0, 0.0),
            (1.0, -1.0, 0.0),
            (-1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
            (1.0, 1.0, 0.0),
        ],
        [(0, 1), (1, 2), (3, 4), (4, 5), (0, 3), (1, 4), (2, 5)],
        [(0, 1, 4, 3), (1, 2, 5, 4)],
    )
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap", do_init=False)
    coords = (
        (0.0, 0.0),
        (0.5, 0.0),
        (0.5, 1.0),
        (0.0, 1.0),
        (0.5, 0.0),
        (1.0, 0.0),
        (1.0, 1.0),
        (0.5, 1.0),
    )
    for item, value in zip(uv.data, coords, strict=True):
        item.uv = value
    uv.data[0].pin_uv = True
    mesh.uv_layers.new(name="DetailUV", do_init=True)
    mesh.edges[5].use_seam = True
    return mesh


def object_with_groups(name: str, mesh: bpy.types.Mesh, x: float) -> bpy.types.Object:
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location.x = x
    left = obj.vertex_groups.new(name="Bone.L")
    right = obj.vertex_groups.new(name="Bone.R")
    left.add([0, 1, 3, 4], 1.0, "REPLACE")
    right.add([1, 2, 4, 5], 1.0, "REPLACE")
    return obj


def armature_for(obj: bpy.types.Object) -> None:
    armature = bpy.data.armatures.new("Attribute Rig Data")
    rig = bpy.data.objects.new("Attribute Rig", armature)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for name, x in (("Bone.L", -0.5), ("Bone.R", 0.5)):
        bone = armature.edit_bones.new(name)
        bone.head = (x, 0.0, -0.5)
        bone.tail = (x, 0.0, 1.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.select_set(False)
    modifier = obj.modifiers.new(name="Attribute Armature", type="ARMATURE")
    modifier.object = rig
    rig.pose.bones["Bone.L"].location.z = 0.5


def checker_material(obj: bpy.types.Object) -> None:
    material = bpy.data.materials.new("Attribute Checker")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    checker = nodes.new("ShaderNodeTexChecker")
    coordinates = nodes.new("ShaderNodeTexCoord")
    checker.inputs["Color1"].default_value = (0.02, 0.02, 0.02, 1.0)
    checker.inputs["Color2"].default_value = (0.8, 0.8, 0.8, 1.0)
    checker.inputs["Scale"].default_value = 6.0
    emission.inputs["Strength"].default_value = 1.0
    material.node_tree.links.new(coordinates.outputs["UV"], checker.inputs["Vector"])
    material.node_tree.links.new(checker.outputs["Color"], emission.inputs["Color"])
    material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    obj.data.materials.append(material)


def camera_for(obj: bpy.types.Object) -> None:
    camera_data = bpy.data.cameras.new("Attribute Camera Data")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 3.0
    camera = bpy.data.objects.new("Attribute Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (obj.location.x, obj.location.y, 8.0)
    bpy.context.scene.camera = camera


def main() -> None:
    output = arguments().output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    source = object_with_groups("Attribute Source", mesh_data("Attribute Source"), -3.0)
    armature_for(source)
    checker_material(source)
    camera_for(source)
    target = object_with_groups("Attribute Target", mesh_data("Attribute Target"), 0.0)
    for item in target.data.uv_layers["UVMap"].data:
        item.uv.x += 0.25
    target.vertex_groups["Bone.L"].remove([0, 1, 3, 4])

    shared_mesh = mesh_data("Attribute Shared")
    object_with_groups("Attribute Shared A", shared_mesh, 3.0)
    object_with_groups("Attribute Shared B", shared_mesh, 6.0)

    shape = object_with_groups("Attribute ShapeKey", mesh_data("Attribute ShapeKey"), 9.0)
    shape.shape_key_add(name="Basis")
    key = shape.shape_key_add(name="Lift")
    key.data[4].co.z = 0.35
    key.value = 0.5

    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save attribute fixture: {sorted(result)}")


if __name__ == "__main__":
    main()
