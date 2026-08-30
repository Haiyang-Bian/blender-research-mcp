"""Create the deterministic Blender 4.2 fixture for the 0.13 topology smoke."""

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
        node.inputs["Roughness"].default_value = 0.3
    return value


def protected_data(mesh: bpy.types.Mesh) -> None:
    mesh.uv_layers.new(name="Topology UV")
    color = mesh.color_attributes.new(
        name="Topology Color", type="FLOAT_COLOR", domain="POINT"
    )
    for index, item in enumerate(color.data):
        amount = index / max(1, len(color.data) - 1)
        item.color = (0.1 + amount * 0.5, 0.25, 0.8 - amount * 0.4, 1.0)


def cube(name: str, location: tuple[float, float, float], mat: bpy.types.Material) -> None:
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = f"{name} Data"
    obj.data.materials.append(mat)
    protected_data(obj.data)


def wire_object(
    name: str,
    vertices: list[tuple[float, float, float]],
    edges: list[tuple[int, int]],
    location: tuple[float, float, float],
    mat: bpy.types.Material,
) -> None:
    mesh = bpy.data.meshes.new(f"{name} Data")
    mesh.from_pydata(vertices, edges, [])
    mesh.update()
    mesh.materials.append(mat)
    protected_data(mesh)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location


def square_loop(
    z: float, offset: int
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int]]]:
    vertices = [(-1.0, -1.0, z), (1.0, -1.0, z), (1.0, 1.0, z), (-1.0, 1.0, z)]
    edges = [(offset + index, offset + ((index + 1) % 4)) for index in range(4)]
    return vertices, edges


def ring(
    count: int, radius: float = 1.3
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int]]]:
    vertices = [
        (
            radius * math.cos(index * math.tau / count),
            radius * math.sin(index * math.tau / count),
            0.0,
        )
        for index in range(count)
    ]
    return vertices, [(index, (index + 1) % count) for index in range(count)]


def point_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def main() -> None:
    args = arguments()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    blue = material("Topology Blue", (0.05, 0.22, 0.8, 1.0))
    gold = material("Topology Gold", (0.9, 0.3, 0.04, 1.0))
    for name, location in (
        ("Topology Subdivide", (-6.0, 3.0, 1.0)),
        ("Topology Loop Cut", (-2.0, 3.0, 1.0)),
        ("Topology Bisect", (2.0, 3.0, 1.0)),
        ("Topology Split", (6.0, 3.0, 1.0)),
        ("Topology Legacy Extrude", (-2.0, 7.0, 1.0)),
        ("Topology Legacy Merge", (2.0, 7.0, 1.0)),
        ("Topology Chain", (6.0, 7.0, 1.0)),
        ("Topology Disconnect", (-6.0, 7.0, 1.0)),
        ("Topology Conflict", (6.0, -7.0, 1.0)),
    ):
        cube(name, location, blue)

    lower_vertices, lower_edges = square_loop(-1.0, 0)
    upper_vertices, upper_edges = square_loop(1.0, 4)
    wire_object(
        "Topology Bridge",
        lower_vertices + upper_vertices,
        lower_edges + upper_edges,
        (-4.0, -3.5, 1.0),
        gold,
    )
    fill_vertices, fill_edges = ring(8)
    wire_object("Topology Fill", fill_vertices, fill_edges, (0.0, -3.5, 1.0), gold)
    grid_vertices = [
        (-2.0 + index, -1.0, 0.0) for index in range(5)
    ] + [(-2.0 + index, 1.0, 0.0) for index in range(5)]
    grid_edges = (
        [(index, index + 1) for index in range(4)]
        + [(5 + index, 6 + index) for index in range(4)]
        + [(0, 5), (4, 9)]
    )
    wire_object("Topology Grid Fill", grid_vertices, grid_edges, (4.0, -3.5, 1.0), gold)

    camera_data = bpy.data.cameras.new("Topology Camera Data")
    camera = bpy.data.objects.new("Topology Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (12.0, -20.0, 15.0)
    point_at(camera, (0.0, 0.0, 0.8))
    bpy.context.scene.camera = camera

    key_data = bpy.data.lights.new("Topology Key Data", type="AREA")
    key_data.energy = 2200.0
    key_data.size = 9.0
    key = bpy.data.objects.new("Topology Key", key_data)
    bpy.context.scene.collection.objects.link(key)
    key.location = (-5.0, -7.0, 13.0)
    point_at(key, (0.0, 0.0, 0.0))

    world = bpy.data.worlds.new("Topology World")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.008, 0.012, 0.035, 1.0)
        background.inputs["Strength"].default_value = 0.3
    bpy.context.scene.world = world
    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"

    result = bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not save topology fixture: {sorted(result)}")


if __name__ == "__main__":
    main()
