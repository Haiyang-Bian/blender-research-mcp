"""Bounded semantic object and scene authoring operations."""

from __future__ import annotations

import math
from typing import Any

import bmesh
import bpy

from .lookdev_ops import session_identity
from .structural_ops import make_structure_guard, refresh_structure_guard_if_present
from .transaction_model import StructuralDelta, Transaction


class AuthoringOperationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: str = "precondition",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.kind = kind
        self.details = details or {}


def _hex_linear_rgb(value: str) -> tuple[float, float, float]:
    channels = [int(value[index : index + 2], 16) / 255.0 for index in (1, 3, 5)]

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    return tuple(linear(channel) for channel in channels)  # type: ignore[return-value]


def _vector(values: dict[str, Any]) -> tuple[float, float, float]:
    return (float(values["x"]), float(values["y"]), float(values["z"]))


def _apply_transform(obj: Any, transform: dict[str, Any]) -> None:
    obj.location = _vector(transform["location"])
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = tuple(
        math.radians(value) for value in _vector(transform["rotation_euler_degrees"])
    )
    obj.scale = _vector(transform["scale"])


def require_collection(name: str | None, expected_identity: str | None) -> Any:
    if name is None:
        return bpy.context.scene.collection
    collection = bpy.data.collections.get(name)
    if collection is None:
        raise AuthoringOperationError(
            "COLLECTION_NOT_FOUND",
            f"Collection does not exist: {name}",
            kind="not_found",
        )
    actual = session_identity("collection", collection)
    if actual != expected_identity:
        raise AuthoringOperationError(
            "COLLECTION_IDENTITY_MISMATCH",
            f"Collection identity changed: {name}",
            kind="conflict",
            details={"expected": expected_identity, "actual": actual},
        )
    return collection


def _new_mesh(definition: dict[str, Any]) -> Any:
    mesh = bpy.data.meshes.new(f"{definition['name']} Mesh")
    bm = bmesh.new()
    try:
        kind = definition["type"]
        if kind == "plane":
            bmesh.ops.create_grid(
                bm,
                x_segments=2,
                y_segments=2,
                size=float(definition["size"]) / 2.0,
            )
        elif kind == "grid":
            bmesh.ops.create_grid(
                bm,
                x_segments=int(definition["x_subdivisions"]),
                y_segments=int(definition["y_subdivisions"]),
                size=float(definition["size"]) / 2.0,
            )
        elif kind == "cube":
            bmesh.ops.create_cube(bm, size=float(definition["size"]))
        elif kind == "uv_sphere":
            bmesh.ops.create_uvsphere(
                bm,
                u_segments=int(definition["segments"]),
                v_segments=int(definition["ring_count"]),
                radius=float(definition["radius"]),
            )
        elif kind == "ico_sphere":
            bmesh.ops.create_icosphere(
                bm,
                subdivisions=int(definition["subdivisions"]),
                radius=float(definition["radius"]),
            )
        elif kind == "cylinder":
            bmesh.ops.create_cone(
                bm,
                cap_ends=True,
                cap_tris=False,
                segments=int(definition["vertices"]),
                radius1=float(definition["radius"]),
                radius2=float(definition["radius"]),
                depth=float(definition["depth"]),
            )
        elif kind == "cone":
            bmesh.ops.create_cone(
                bm,
                cap_ends=True,
                cap_tris=False,
                segments=int(definition["vertices"]),
                radius1=float(definition["radius1"]),
                radius2=float(definition["radius2"]),
                depth=float(definition["depth"]),
            )
        else:
            raise AssertionError(f"Unsupported mesh primitive: {kind}")
        bm.to_mesh(mesh)
        mesh.update()
        return mesh
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    finally:
        bm.free()


def _new_object_data(definition: dict[str, Any]) -> tuple[Any | None, str | None]:
    kind = str(definition["type"])
    if kind in {
        "plane",
        "grid",
        "cube",
        "uv_sphere",
        "ico_sphere",
        "cylinder",
        "cone",
    }:
        return _new_mesh(definition), "mesh"
    if kind == "empty":
        return None, None
    if kind == "camera":
        camera = bpy.data.cameras.new(f"{definition['name']} Camera")
        camera.lens = float(definition["lens"])
        camera.sensor_width = float(definition["sensor_width"])
        return camera, "camera"
    light_type = {
        "point_light": "POINT",
        "sun_light": "SUN",
        "spot_light": "SPOT",
        "area_light": "AREA",
    }.get(kind)
    if light_type is None:
        raise AuthoringOperationError(
            "OBJECT_TYPE_INVALID",
            f"Unsupported object type: {kind}",
            kind="validation",
        )
    light = bpy.data.lights.new(f"{definition['name']} Light", type=light_type)
    light.energy = float(definition["energy"])
    light.color = _hex_linear_rgb(str(definition["color"]))
    if light_type == "AREA":
        light.size = float(definition["size"])
    elif light_type == "SPOT":
        light.spot_size = math.radians(float(definition["spot_size_degrees"]))
    return light, "light"


def object_summary(obj: Any) -> dict[str, Any]:
    data = obj.data
    data_kind = data.__class__.__name__.lower() if data is not None else None
    return {
        "name": obj.name,
        "type": obj.type,
        "session_identity": session_identity("object", obj),
        "data": (
            {
                "name": data.name,
                "kind": data_kind,
                "session_identity": session_identity(str(data_kind), data),
                "users": int(data.users),
            }
            if data is not None
            else None
        ),
        "collections": [
            {
                "name": collection.name,
                "session_identity": session_identity("collection", collection),
            }
            for collection in obj.users_collection
        ],
        "location": [float(value) for value in obj.location],
        "rotation_euler_degrees": [math.degrees(float(value)) for value in obj.rotation_euler],
        "scale": [float(value) for value in obj.scale],
    }


def create_object(
    transaction: Transaction,
    definition: dict[str, Any],
) -> tuple[Any, StructuralDelta]:
    transaction.ensure_capacity()
    name = str(definition["name"])
    if bpy.data.objects.get(name) is not None:
        raise AuthoringOperationError(
            "OBJECT_NAME_CONFLICT",
            f"An object already uses the exact name: {name}",
            kind="conflict",
        )
    collection = require_collection(
        definition.get("collection_name"),
        definition.get("expected_collection_identity"),
    )
    data = None
    data_kind = None
    obj = None
    try:
        data, data_kind = _new_object_data(definition)
        obj = bpy.data.objects.new(name, data)
        _apply_transform(obj, definition["transform"])
        if definition["type"] == "empty":
            obj.empty_display_type = str(definition["display_type"])
            obj.empty_display_size = float(definition["display_size"])
        collection.objects.link(obj)
        delta = StructuralDelta(
            kind="object_create",
            action="create_resource",
            before=(),
            after=(make_structure_guard("object", obj),),
            payload={
                "resource": obj,
                "resource_kind": "object",
                "resource_name": name,
                "owned_resources": ((data_kind, data),) if data is not None else (),
            },
        )
        return obj, delta
    except Exception:
        if obj is not None and bpy.data.objects.get(obj.name) is obj:
            bpy.data.objects.remove(obj)
        if data is not None and int(data.users) == 0:
            getattr(bpy.data, f"{data_kind}s").remove(data)
        raise


def duplicate_object(
    transaction: Transaction,
    *,
    source_name: str,
    expected_source_identity: str,
    name: str,
    linked_data: bool,
    collection_name: str | None,
    expected_collection_identity: str | None,
    transform: dict[str, Any] | None,
) -> tuple[Any, StructuralDelta]:
    transaction.ensure_capacity()
    source = bpy.data.objects.get(source_name)
    if source is None:
        raise AuthoringOperationError(
            "OBJECT_NOT_FOUND",
            f"Object does not exist: {source_name}",
            kind="not_found",
        )
    actual_identity = session_identity("object", source)
    if actual_identity != expected_source_identity:
        raise AuthoringOperationError(
            "OBJECT_IDENTITY_MISMATCH",
            f"Object identity changed: {source_name}",
            kind="conflict",
        )
    if bpy.data.objects.get(name) is not None:
        raise AuthoringOperationError(
            "OBJECT_NAME_CONFLICT",
            f"An object already uses the exact name: {name}",
            kind="conflict",
        )
    if collection_name is None:
        collection = (
            source.users_collection[0]
            if source.users_collection
            else bpy.context.scene.collection
        )
    else:
        collection = require_collection(collection_name, expected_collection_identity)
    duplicate = source.copy()
    duplicate.name = name
    data = None
    data_kind = None
    if source.data is not None and not linked_data:
        data = source.data.copy()
        duplicate.data = data
        data_kind = data.__class__.__name__.lower()
    if transform is not None:
        _apply_transform(duplicate, transform)
    collection.objects.link(duplicate)
    duplicate.select_set(False)
    delta = StructuralDelta(
        kind="object_duplicate",
        action="create_resource",
        before=(),
        after=(make_structure_guard("object", duplicate),),
        payload={
            "resource": duplicate,
            "resource_kind": "object",
            "resource_name": name,
            "owned_resources": ((data_kind, data),) if data is not None else (),
        },
    )
    if linked_data and source.data is not None:
        shared_data = source.data
        transaction.refresh_object_data_users(
            session_identity(shared_data.__class__.__name__.lower(), shared_data),
            int(shared_data.users),
        )
        structure_kind = {
            "MESH": "mesh",
            "CAMERA": "camera",
            "LIGHT": "light",
        }.get(str(source.type))
        if structure_kind is not None:
            refresh_structure_guard_if_present(
                transaction,
                structure_kind,
                shared_data,
            )
    return duplicate, delta


def unlink_object(
    transaction: Transaction,
    *,
    object_name: str,
    expected_object_identity: str,
) -> tuple[Any, StructuralDelta]:
    transaction.ensure_capacity()
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise AuthoringOperationError(
            "OBJECT_NOT_FOUND",
            f"Object does not exist: {object_name}",
            kind="not_found",
        )
    actual_identity = session_identity("object", obj)
    if actual_identity != expected_object_identity:
        raise AuthoringOperationError(
            "OBJECT_IDENTITY_MISMATCH",
            f"Object identity changed: {object_name}",
            kind="conflict",
        )
    try:
        selected = bool(obj.select_get())
    except RuntimeError:
        selected = False
    if selected or bpy.context.view_layer.objects.active is obj:
        raise AuthoringOperationError(
            "OBJECT_CONTEXT_CONFLICT",
            "Cannot delete the active or selected object inside a restorable transaction",
        )
    collections = tuple(obj.users_collection)
    if not collections:
        raise AuthoringOperationError(
            "OBJECT_ALREADY_UNLINKED",
            f"Object is not linked to a collection: {object_name}",
            kind="conflict",
        )
    created_delta = next(
        (
            delta
            for delta in transaction.structural_deltas()
            if delta.action == "create_resource" and delta.payload.get("resource") is obj
        ),
        None,
    )
    for collection in collections:
        collection.objects.unlink(obj)
    delta = StructuralDelta(
        kind="object_delete",
        action="unlink_object",
        before=(),
        after=(make_structure_guard("object", obj),),
        payload={
            "object": obj,
            "collections": collections,
            "created_in_transaction": created_delta is not None,
            "owned_resources": (
                tuple(created_delta.payload.get("owned_resources", ()))
                if created_delta is not None
                else ()
            ),
        },
    )
    return obj, delta


def inspect_scene(kinds: list[str], name_filter: str | None, limit: int) -> dict[str, Any]:
    query = name_filter.casefold() if name_filter is not None else None

    def allowed(name: str) -> bool:
        return query is None or query in name.casefold()

    result: dict[str, Any] = {"requested_kinds": kinds, "name_filter": name_filter, "limit": limit}
    if "objects" in kinds:
        result["objects"] = [
            object_summary(obj) for obj in bpy.data.objects if allowed(obj.name)
        ][:limit]
    if "collections" in kinds:
        result["collections"] = [
            {
                "name": collection.name,
                "session_identity": session_identity("collection", collection),
                "users": int(collection.users),
                "object_count": len(collection.objects),
            }
            for collection in bpy.data.collections
            if allowed(collection.name)
        ][:limit]
    if "materials" in kinds:
        result["materials"] = [
            {
                "name": material.name,
                "session_identity": session_identity("material", material),
                "users": int(material.users),
                "use_nodes": bool(material.use_nodes),
            }
            for material in bpy.data.materials
            if allowed(material.name)
        ][:limit]
    if "images" in kinds:
        result["images"] = [
            {
                "name": image.name,
                "session_identity": session_identity("image", image),
                "users": int(image.users),
                "filepath": bpy.path.abspath(image.filepath),
            }
            for image in bpy.data.images
            if allowed(image.name)
        ][:limit]
    if "world" in kinds:
        world = bpy.context.scene.world
        result["world"] = (
            {
                "name": world.name,
                "session_identity": session_identity("world", world),
                "users": int(world.users),
                "use_nodes": bool(world.use_nodes),
            }
            if world is not None and allowed(world.name)
            else None
        )
    if "camera" in kinds:
        camera = bpy.context.scene.camera
        result["camera"] = object_summary(camera) if camera is not None else None
    if "render" in kinds:
        render = bpy.context.scene.render
        result["render"] = {
            "engine": bpy.context.scene.render.engine,
            "resolution_x": int(render.resolution_x),
            "resolution_y": int(render.resolution_y),
            "resolution_percentage": int(render.resolution_percentage),
            "film_transparent": bool(render.film_transparent),
            "filepath": bpy.path.abspath(render.filepath),
        }
    return result
