"""Exact UV-layer inspection and topology-stable transactional authoring."""

from __future__ import annotations

import contextlib
import hashlib
import math
import struct
from collections import deque
from typing import Any

import bpy
from mathutils import Vector

from .lookdev_ops import session_identity
from .mesh_ops import (
    MeshEditDelta,
    MeshOperationError,
    _create_guard,
    _remove_new_guard,
    _remove_temporary_mesh,
    _restore_failed_edit,
    _validate_guard,
    mesh_counts,
    mesh_fingerprint,
    mesh_revision_id,
    mesh_user_refs,
    topology_fingerprint,
    validate_mesh_attribute_target,
)
from .mesh_query_ops import validate_selection
from .mesh_resource_model import MeshResourceBook, MeshResourceError, SelectionRecord
from .structural_ops import refresh_structure_guard_if_present
from .transaction_model import Transaction

MAX_UV_LAYERS = 16
MAX_UV_CORNERS = 4096
UV_LIMIT = 1_000_000.0


class MeshUVOperationError(MeshOperationError):
    pass


def _hash_value(hasher: Any, value: Any) -> None:
    encoded = str(value).encode("utf-8")
    hasher.update(struct.pack("<I", len(encoded)))
    hasher.update(encoded)


def uv_fingerprint(mesh: Any) -> str:
    hasher = hashlib.sha256()
    _hash_value(hasher, len(mesh.uv_layers))
    _hash_value(hasher, int(getattr(mesh.uv_layers, "active_index", -1)))
    for index, layer in enumerate(mesh.uv_layers):
        _hash_value(hasher, index)
        _hash_value(hasher, layer.name)
        _hash_value(hasher, bool(getattr(layer, "active_render", False)))
        _hash_value(hasher, bool(getattr(layer, "active_clone", False)))
        for item in layer.data:
            uv = item.uv
            hasher.update(struct.pack("<ff", float(uv[0]), float(uv[1])))
            hasher.update(bytes((1 if bool(getattr(item, "pin_uv", False)) else 0,)))
    for edge in mesh.edges:
        hasher.update(bytes((1 if bool(edge.use_seam) else 0,)))
    return hasher.hexdigest()


def _mesh_object(object_name: str) -> tuple[Any, Any]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise MeshUVOperationError(
            "OBJECT_NOT_FOUND", f"Object does not exist: {object_name}", kind="not_found"
        )
    if obj.type != "MESH" or obj.data is None:
        raise MeshUVOperationError(
            "MESH_OBJECT_UNSUPPORTED", f"UV operations require a MESH object: {object_name}"
        )
    return obj, obj.data


def _layer(mesh: Any, name: str, identity: str | None = None) -> Any:
    layer = mesh.uv_layers.get(name)
    if layer is None:
        raise MeshUVOperationError(
            "MESH_UV_LAYER_NOT_FOUND", f"UV layer does not exist: {name}", kind="not_found"
        )
    if identity is not None and session_identity("uv_layer", layer) != identity:
        raise MeshUVOperationError(
            "MESH_UV_LAYER_IDENTITY_MISMATCH",
            f"UV layer identity changed: {name}",
            kind="conflict",
        )
    return layer


def _layer_ref(mesh: Any, raw: Any) -> Any:
    if not isinstance(raw, dict):
        raise MeshUVOperationError("MESH_UV_OPERATION_INVALID", "layer must be an object")
    name = raw.get("layer_name")
    identity = raw.get("expected_layer_identity")
    if not isinstance(name, str) or not name or not isinstance(identity, str) or not identity:
        raise MeshUVOperationError(
            "MESH_UV_OPERATION_INVALID", "layer requires exact name and identity"
        )
    return _layer(mesh, name, identity)


def _finite_uv(raw: Any, field: str) -> tuple[float, float]:
    if not isinstance(raw, list) or len(raw) != 2:
        raise MeshUVOperationError("MESH_UV_OPERATION_INVALID", f"{field} must have two components")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw):
        raise MeshUVOperationError(
            "MESH_UV_OPERATION_INVALID", f"{field} must contain JSON numbers"
        )
    value = (float(raw[0]), float(raw[1]))
    if any(not math.isfinite(item) or abs(item) > UV_LIMIT for item in value):
        raise MeshUVOperationError(
            "MESH_UV_OPERATION_INVALID", f"{field} is outside the finite UV range"
        )
    return value


def _loop_evidence(mesh: Any, raw: Any) -> int:
    if not isinstance(raw, dict):
        raise MeshUVOperationError(
            "MESH_UV_OPERATION_INVALID", "UV corner evidence must be an object"
        )
    names = ("loop_index", "face_index", "corner_index", "vertex_index")
    if any(
        isinstance(raw.get(name), bool) or not isinstance(raw.get(name), int) or raw[name] < 0
        for name in names
    ):
        raise MeshUVOperationError(
            "MESH_UV_OPERATION_INVALID", "UV corner evidence contains invalid indices"
        )
    loop_index = int(raw["loop_index"])
    face_index = int(raw["face_index"])
    corner_index = int(raw["corner_index"])
    vertex_index = int(raw["vertex_index"])
    if loop_index >= len(mesh.loops) or face_index >= len(mesh.polygons):
        raise MeshUVOperationError(
            "MESH_UV_CORNER_MISMATCH", "UV corner evidence is outside the Mesh", kind="conflict"
        )
    face = mesh.polygons[face_index]
    if corner_index >= int(face.loop_total) or int(face.loop_start) + corner_index != loop_index:
        raise MeshUVOperationError(
            "MESH_UV_CORNER_MISMATCH", "UV face/corner evidence changed", kind="conflict"
        )
    if int(mesh.loops[loop_index].vertex_index) != vertex_index:
        raise MeshUVOperationError(
            "MESH_UV_CORNER_MISMATCH", "UV vertex evidence changed", kind="conflict"
        )
    return loop_index


def _face_neighbors(mesh: Any, layer: Any) -> list[set[int]]:
    edge_faces: dict[int, list[tuple[int, int]]] = {}
    for face in mesh.polygons:
        for loop_index in range(int(face.loop_start), int(face.loop_start + face.loop_total)):
            edge_faces.setdefault(int(mesh.loops[loop_index].edge_index), []).append(
                (int(face.index), loop_index)
            )
    result = [set() for _ in mesh.polygons]
    for edge_index, uses in edge_faces.items():
        if len(uses) != 2 or bool(mesh.edges[edge_index].use_seam):
            continue
        (first_face, first_loop), (second_face, second_loop) = uses
        first_next = int(mesh.polygons[first_face].loop_start) + (
            (first_loop - int(mesh.polygons[first_face].loop_start) + 1)
            % int(mesh.polygons[first_face].loop_total)
        )
        second_next = int(mesh.polygons[second_face].loop_start) + (
            (second_loop - int(mesh.polygons[second_face].loop_start) + 1)
            % int(mesh.polygons[second_face].loop_total)
        )
        first_uvs = {
            tuple(round(float(v), 7) for v in layer.data[index].uv)
            for index in (first_loop, first_next)
        }
        second_uvs = {
            tuple(round(float(v), 7) for v in layer.data[index].uv)
            for index in (second_loop, second_next)
        }
        if first_uvs == second_uvs:
            result[first_face].add(second_face)
            result[second_face].add(first_face)
    return result


def _islands(mesh: Any, layer: Any) -> list[tuple[int, ...]]:
    neighbors = _face_neighbors(mesh, layer)
    remaining = set(range(len(mesh.polygons)))
    islands: list[tuple[int, ...]] = []
    while remaining:
        seed = min(remaining)
        queue = deque((seed,))
        found: set[int] = set()
        while queue:
            face = queue.popleft()
            if face in found:
                continue
            found.add(face)
            queue.extend(neighbors[face] - found)
        remaining -= found
        islands.append(tuple(sorted(found)))
    return islands


def _uv_area(points: list[tuple[float, float]]) -> float:
    return abs(
        sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
        * 0.5
    )


def _layer_summary(mesh: Any, layer: Any, index: int) -> dict[str, Any]:
    coords = [(float(item.uv[0]), float(item.uv[1])) for item in layer.data]
    mapped = [value for pair in coords for value in pair]
    return {
        "name": layer.name,
        "session_identity": session_identity("uv_layer", layer),
        "index": index,
        "active": index == int(getattr(mesh.uv_layers, "active_index", -1)),
        "display": index == int(getattr(mesh.uv_layers, "active_index", -1)),
        "render": bool(getattr(layer, "active_render", False)),
        "clone": bool(getattr(layer, "active_clone", False)),
        "corner_count": len(layer.data),
        "pinned_count": sum(bool(getattr(item, "pin_uv", False)) for item in layer.data),
        "finite": all(math.isfinite(value) for value in mapped),
    }


def inspect_uv(
    object_name: str, layer_name: str | None, component: str, offset: int, limit: int
) -> dict[str, Any]:
    obj, mesh = _mesh_object(object_name)
    if component not in {"SUMMARY", "FACES", "LOOPS", "ISLANDS", "SEAMS"}:
        raise MeshUVOperationError(
            "MESH_UV_COMPONENT_INVALID", f"Unsupported UV component: {component}"
        )
    if offset < 0 or not 1 <= limit <= 512:
        raise MeshUVOperationError(
            "MESH_PAGINATION_INVALID", "offset must be non-negative and limit must be 1-512"
        )
    layer = mesh.uv_layers.get(layer_name) if layer_name is not None else mesh.uv_layers.active
    if layer_name is not None and layer is None:
        raise MeshUVOperationError(
            "MESH_UV_LAYER_NOT_FOUND", f"UV layer does not exist: {layer_name}", kind="not_found"
        )
    summaries = [_layer_summary(mesh, item, index) for index, item in enumerate(mesh.uv_layers)]
    items: list[dict[str, Any]] = []
    total = 0
    warnings: list[dict[str, Any]] = []
    islands: list[tuple[int, ...]] = _islands(mesh, layer) if layer is not None else []
    if component == "FACES" and layer is not None:
        total = len(mesh.polygons)
        for face in mesh.polygons[offset : min(total, offset + limit)]:
            loop_indices = list(range(int(face.loop_start), int(face.loop_start + face.loop_total)))
            coords = [
                tuple(float(value) for value in layer.data[index].uv) for index in loop_indices
            ]
            items.append(
                {
                    "face_index": int(face.index),
                    "loop_indices": loop_indices,
                    "vertex_indices": [
                        int(mesh.loops[index].vertex_index) for index in loop_indices
                    ],
                    "uv": [list(value) for value in coords],
                    "uv_area": _uv_area(coords),
                    "degenerate": _uv_area(coords) <= 1e-12,
                }
            )
    elif component == "LOOPS" and layer is not None:
        total = len(mesh.loops)
        face_by_loop = [0] * total
        corner_by_loop = [0] * total
        for face in mesh.polygons:
            for corner, loop_index in enumerate(
                range(int(face.loop_start), int(face.loop_start + face.loop_total))
            ):
                face_by_loop[loop_index] = int(face.index)
                corner_by_loop[loop_index] = corner
        for loop_index in range(offset, min(total, offset + limit)):
            uv = layer.data[loop_index]
            items.append(
                {
                    "loop_index": loop_index,
                    "face_index": face_by_loop[loop_index],
                    "corner_index": corner_by_loop[loop_index],
                    "vertex_index": int(mesh.loops[loop_index].vertex_index),
                    "uv": list(uv.uv),
                    "pinned": bool(getattr(uv, "pin_uv", False)),
                    "udim_tile": [math.floor(float(uv.uv[0])), math.floor(float(uv.uv[1]))],
                }
            )
    elif component == "ISLANDS" and layer is not None:
        total = len(islands)
        for island_index in range(offset, min(total, offset + limit)):
            faces = islands[island_index]
            loop_indices = [
                loop
                for face_index in faces
                for loop in range(
                    int(mesh.polygons[face_index].loop_start),
                    int(
                        mesh.polygons[face_index].loop_start + mesh.polygons[face_index].loop_total
                    ),
                )
            ]
            coords = [
                tuple(float(value) for value in layer.data[index].uv) for index in loop_indices
            ]
            minimum = (
                [min(value[axis] for value in coords) for axis in (0, 1)] if coords else [0.0, 0.0]
            )
            maximum = (
                [max(value[axis] for value in coords) for axis in (0, 1)] if coords else [0.0, 0.0]
            )
            items.append(
                {
                    "island_index": island_index,
                    "face_indices": list(faces),
                    "loop_count": len(loop_indices),
                    "bounds": {"minimum": minimum, "maximum": maximum},
                    "tiles": sorted(
                        {(math.floor(value[0]), math.floor(value[1])) for value in coords}
                    ),
                }
            )
    elif component == "SEAMS":
        seams = [int(edge.index) for edge in mesh.edges if edge.use_seam]
        total = len(seams)
        items = [
            {"edge_index": index, "vertices": list(mesh.edges[index].vertices)}
            for index in seams[offset : min(total, offset + limit)]
        ]
    if offset > total:
        raise MeshUVOperationError(
            "MESH_PAGINATION_INVALID", f"offset {offset} exceeds UV item count {total}"
        )
    stop = min(total, offset + limit)
    if stop < total:
        warnings.append({"code": "MESH_UV_ITEMS_TRUNCATED", "next_offset": stop})
    if layer is None and component != "SUMMARY":
        warnings.append({"code": "MESH_UV_LAYER_MISSING"})
    return {
        "object": {"name": obj.name, "session_identity": session_identity("object", obj)},
        "mesh": {
            "name": mesh.name,
            "session_identity": session_identity("mesh", mesh),
            "users": int(mesh.users),
        },
        "mesh_fingerprint": mesh_fingerprint(mesh),
        "mesh_revision_id": mesh_revision_id(mesh),
        "uv_fingerprint": uv_fingerprint(mesh),
        "layer": _layer_summary(mesh, layer, list(mesh.uv_layers).index(layer))
        if layer is not None
        else None,
        "layers": summaries,
        "counts": {
            "layers": len(mesh.uv_layers),
            "seams": sum(bool(edge.use_seam) for edge in mesh.edges),
            "pinned": sum(
                bool(getattr(item, "pin_uv", False))
                for current in mesh.uv_layers
                for item in current.data
            ),
            "unmapped": 0
            if layer is not None and len(layer.data) == len(mesh.loops)
            else len(mesh.loops),
            "degenerate_faces": sum(
                _uv_area(
                    [
                        tuple(float(value) for value in layer.data[index].uv)
                        for index in range(
                            int(face.loop_start), int(face.loop_start + face.loop_total)
                        )
                    ]
                )
                <= 1e-12
                for face in mesh.polygons
            )
            if layer is not None
            else 0,
            "islands": len(islands),
        },
        "component": component,
        "items": items,
        "pagination": {
            "offset": offset,
            "limit": limit,
            "total": total,
            "returned": len(items),
            "truncated": stop < total,
            "next_offset": stop if stop < total else None,
        },
        "warnings": warnings,
    }


def _selection(
    resources: MeshResourceBook, selection_id: Any, obj: Any, mesh: Any, domain: str
) -> SelectionRecord:
    if not isinstance(selection_id, str) or not selection_id:
        raise MeshUVOperationError("MESH_UV_OPERATION_INVALID", "selection_id must be non-empty")
    record = resources.selection(selection_id)
    selected_obj, selected_mesh = validate_selection(record)
    if selected_obj is not obj or selected_mesh is not mesh or record.domain != domain:
        raise MeshUVOperationError(
            "MESH_UV_SELECTION_INVALID",
            f"UV operation requires a {domain} SelectionSet on the exact target revision",
        )
    return record


def _selected_faces(mesh: Any, record: SelectionRecord, layer: Any, scope: str) -> tuple[int, ...]:
    selected = set(record.indices)
    if scope == "ISLANDS":
        selected = {
            face
            for island in _islands(mesh, layer)
            if selected.intersection(island)
            for face in island
        }
    return tuple(sorted(selected))


def _apply_roles(mesh: Any, layer: Any, operation: dict[str, Any]) -> None:
    index = list(mesh.uv_layers).index(layer)
    if operation.get("display") is True:
        mesh.uv_layers.active_index = index
    elif operation.get("display") is False and int(mesh.uv_layers.active_index) == index:
        replacement = next((item for item in range(len(mesh.uv_layers)) if item != index), index)
        mesh.uv_layers.active_index = replacement
    for field, prop in (("render", "active_render"), ("clone", "active_clone")):
        if field in operation:
            requested = bool(operation[field])
            if requested:
                for current in mesh.uv_layers:
                    if hasattr(current, prop):
                        setattr(current, prop, current is layer)
            elif hasattr(layer, prop):
                setattr(layer, prop, False)


def _operator_kwargs(operator: Any, values: dict[str, Any]) -> dict[str, Any]:
    with contextlib.suppress(Exception):
        names = {item.identifier for item in operator.get_rna_type().properties}
        return {key: value for key, value in values.items() if key in names}
    return values


def _run_uv_operator(
    mesh: Any, layer_name: str, faces: tuple[int, ...], operation: dict[str, Any]
) -> None:
    before_topology = topology_fingerprint(mesh)
    temporary_mesh = mesh.copy()
    temporary_mesh.name = f"{mesh.name}.MCP-UV-Temporary"
    temporary_object = bpy.data.objects.new(f"{mesh.name}.MCP-UV-Temporary", temporary_mesh)
    bpy.context.scene.collection.objects.link(temporary_object)
    previous_active = bpy.context.view_layer.objects.active
    previous_selected = tuple(bpy.context.selected_objects)
    previous_mode = str(bpy.context.mode)
    try:
        if previous_mode != "OBJECT" and previous_active is not None:
            bpy.ops.object.mode_set(mode="OBJECT")
        for item in tuple(bpy.context.selected_objects):
            item.select_set(False)
        temporary_object.select_set(True)
        bpy.context.view_layer.objects.active = temporary_object
        temporary_mesh.uv_layers.active = temporary_mesh.uv_layers.get(layer_name)
        for face in temporary_mesh.polygons:
            face.select = int(face.index) in faces
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.object.mode_set(mode="OBJECT")
        for face in temporary_mesh.polygons:
            face.select = int(face.index) in faces
        bpy.ops.object.mode_set(mode="EDIT")
        if operation["type"] == "unwrap":
            kwargs = _operator_kwargs(
                bpy.ops.uv.unwrap,
                {
                    "method": operation.get("method", "ANGLE_BASED"),
                    "fill_holes": operation.get("fill_holes", True),
                    "correct_aspect": operation.get("correct_aspect", True),
                    "use_subsurf_data": operation.get("use_subsurf_data", False),
                    "margin": operation.get("margin", 0.001),
                },
            )
            result = bpy.ops.uv.unwrap(**kwargs)
        else:
            kwargs = _operator_kwargs(
                bpy.ops.uv.pack_islands,
                {
                    "rotate": operation.get("rotate", True),
                    "scale": operation.get("scale", True),
                    "margin_method": "SCALED",
                    "margin": operation.get("margin", 0.001),
                    "pin": operation.get("pinned_policy", "KEEP") == "KEEP",
                },
            )
            result = bpy.ops.uv.pack_islands(**kwargs)
        if "FINISHED" not in result:
            raise MeshUVOperationError(
                "MESH_UV_OPERATOR_FAILED",
                f"Blender UV operator returned {sorted(result)}",
                kind="blender_api",
            )
        bpy.ops.object.mode_set(mode="OBJECT")
        if topology_fingerprint(temporary_mesh) != before_topology:
            raise MeshUVOperationError(
                "MESH_UV_TOPOLOGY_DRIFT",
                "Isolated UV operation changed topology",
                kind="blender_api",
            )
        source = temporary_mesh.uv_layers.get(layer_name)
        target = mesh.uv_layers.get(layer_name)
        if source is None or target is None or len(source.data) != len(target.data):
            raise MeshUVOperationError(
                "MESH_UV_OPERATOR_FAILED",
                "Isolated UV result does not match the target layer",
                kind="blender_api",
            )
        for index in range(len(target.data)):
            target.data[index].uv = source.data[index].uv
        if operation["type"] == "pack":
            tile = Vector((float(operation.get("tile_u", 0)), float(operation.get("tile_v", 0))))
            for index in range(len(target.data)):
                target.data[index].uv += tile
    finally:
        with contextlib.suppress(Exception):
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        with contextlib.suppress(Exception):
            bpy.data.objects.remove(temporary_object, do_unlink=True)
        if bpy.data.meshes.get(temporary_mesh.name) is temporary_mesh:
            bpy.data.meshes.remove(temporary_mesh)
        for item in tuple(bpy.context.selected_objects):
            item.select_set(False)
        for item in previous_selected:
            if bpy.data.objects.get(item.name) is item:
                item.select_set(True)
        if (
            previous_active is not None
            and bpy.data.objects.get(previous_active.name) is previous_active
        ):
            bpy.context.view_layer.objects.active = previous_active
            if previous_mode != "OBJECT":
                mode = {
                    "EDIT_MESH": "EDIT",
                    "SCULPT": "SCULPT",
                    "VERTEX_PAINT": "VERTEX_PAINT",
                    "WEIGHT_PAINT": "WEIGHT_PAINT",
                    "TEXTURE_PAINT": "TEXTURE_PAINT",
                }.get(previous_mode)
                if mode is not None:
                    with contextlib.suppress(Exception):
                        bpy.ops.object.mode_set(mode=mode)


def _apply_operation(
    mesh: Any, obj: Any, resources: MeshResourceBook, operation: dict[str, Any]
) -> dict[str, Any]:
    operation_type = operation.get("type")
    if operation_type == "layer_create":
        name = operation.get("layer_name")
        if not isinstance(name, str) or not name or mesh.uv_layers.get(name) is not None:
            raise MeshUVOperationError(
                "MESH_UV_LAYER_NAME_CONFLICT", f"UV layer name is invalid or already used: {name}"
            )
        if len(mesh.uv_layers) >= MAX_UV_LAYERS:
            raise MeshUVOperationError(
                "MESH_UV_LAYER_LIMIT", f"A Mesh may have at most {MAX_UV_LAYERS} UV layers"
            )
        source_mode = operation.get("source", "EMPTY")
        source = None
        if source_mode == "ACTIVE":
            source = mesh.uv_layers.active
        elif source_mode == "LAYER":
            source = _layer_ref(mesh, operation.get("source_layer"))
        elif source_mode != "EMPTY":
            raise MeshUVOperationError(
                "MESH_UV_OPERATION_INVALID", "source must be EMPTY, ACTIVE, or LAYER"
            )
        layer = mesh.uv_layers.new(name=name, do_init=False)
        if source is not None:
            for index in range(len(layer.data)):
                layer.data[index].uv = source.data[index].uv
                if hasattr(layer.data[index], "pin_uv"):
                    layer.data[index].pin_uv = bool(getattr(source.data[index], "pin_uv", False))
        else:
            for item in layer.data:
                item.uv = (0.0, 0.0)
        return {"layer": _layer_summary(mesh, layer, list(mesh.uv_layers).index(layer))}
    if operation_type == "seam_set":
        record = _selection(resources, operation.get("selection_id"), obj, mesh, "EDGE")
        seam = operation.get("seam")
        if type(seam) is not bool:
            raise MeshUVOperationError("MESH_UV_OPERATION_INVALID", "seam must be a boolean")
        for index in record.indices:
            mesh.edges[index].use_seam = seam
        return {"affected_edges": len(record.indices)}
    layer = _layer_ref(mesh, operation.get("layer"))
    if operation_type == "layer_delete":
        mesh.uv_layers.remove(layer)
        return {"deleted_layer": operation["layer"]["layer_name"]}
    if operation_type == "layer_roles":
        _apply_roles(mesh, layer, operation)
        return {"layer": _layer_summary(mesh, layer, list(mesh.uv_layers).index(layer))}
    if operation_type == "coordinate_set":
        mode = operation.get("mode", "ABSOLUTE")
        if mode not in {"ABSOLUTE", "OFFSET"}:
            raise MeshUVOperationError(
                "MESH_UV_OPERATION_INVALID", "mode must be ABSOLUTE or OFFSET"
            )
        corners = operation.get("corners")
        if not isinstance(corners, list) or not 1 <= len(corners) <= MAX_UV_CORNERS:
            raise MeshUVOperationError(
                "MESH_UV_OPERATION_INVALID", "corners must contain 1-4096 entries"
            )
        seen: set[int] = set()
        for raw in corners:
            loop_index = _loop_evidence(mesh, raw)
            if loop_index in seen:
                raise MeshUVOperationError(
                    "MESH_UV_OPERATION_INVALID", "corner loop indices must be unique"
                )
            seen.add(loop_index)
            value = Vector(_finite_uv(raw.get("uv"), "corner.uv"))
            if mode == "OFFSET":
                value += layer.data[loop_index].uv
            if any(abs(float(component)) > UV_LIMIT for component in value):
                raise MeshUVOperationError(
                    "MESH_UV_OPERATION_INVALID", "resulting UV is outside the allowed range"
                )
            layer.data[loop_index].uv = value
        return {"affected_corners": len(seen)}
    if operation_type == "pin_set":
        corners = operation.get("corners")
        pinned = operation.get("pinned")
        if (
            not isinstance(corners, list)
            or not 1 <= len(corners) <= MAX_UV_CORNERS
            or type(pinned) is not bool
        ):
            raise MeshUVOperationError(
                "MESH_UV_OPERATION_INVALID", "pin_set requires exact corners and pinned"
            )
        indices = {_loop_evidence(mesh, raw) for raw in corners}
        if len(indices) != len(corners):
            raise MeshUVOperationError(
                "MESH_UV_OPERATION_INVALID", "corner loop indices must be unique"
            )
        for index in indices:
            layer.data[index].pin_uv = pinned
        return {"affected_corners": len(indices)}
    if operation_type == "transform":
        record = _selection(resources, operation.get("selection_id"), obj, mesh, "FACE")
        scope = operation.get("scope", "ISLANDS")
        if scope not in {"FACES", "ISLANDS"}:
            raise MeshUVOperationError(
                "MESH_UV_OPERATION_INVALID", "scope must be FACES or ISLANDS"
            )
        faces = _selected_faces(mesh, record, layer, scope)
        indices = sorted(
            {
                loop
                for face_index in faces
                for loop in range(
                    int(mesh.polygons[face_index].loop_start),
                    int(
                        mesh.polygons[face_index].loop_start + mesh.polygons[face_index].loop_total
                    ),
                )
            }
        )
        translation = Vector(_finite_uv(operation.get("translation", [0.0, 0.0]), "translation"))
        scale = Vector(_finite_uv(operation.get("scale", [1.0, 1.0]), "scale"))
        rotation = operation.get("rotation_degrees", 0.0)
        if (
            isinstance(rotation, bool)
            or not isinstance(rotation, (int, float))
            or not math.isfinite(float(rotation))
        ):
            raise MeshUVOperationError(
                "MESH_UV_OPERATION_INVALID", "rotation_degrees must be finite"
            )
        pivot_raw = operation.get("pivot", "MEDIAN")
        if pivot_raw == "MEDIAN":
            pivot = sum((layer.data[index].uv for index in indices), Vector((0.0, 0.0))) / max(
                1, len(indices)
            )
        else:
            pivot = Vector(_finite_uv(pivot_raw, "pivot"))
        angle = math.radians(float(rotation))
        cosine, sine = math.cos(angle), math.sin(angle)
        for index in indices:
            value = layer.data[index].uv - pivot
            value = Vector((value.x * scale.x, value.y * scale.y))
            value = (
                Vector((value.x * cosine - value.y * sine, value.x * sine + value.y * cosine))
                + pivot
                + translation
            )
            if any(abs(float(component)) > UV_LIMIT for component in value):
                raise MeshUVOperationError(
                    "MESH_UV_OPERATION_INVALID", "resulting UV is outside the allowed range"
                )
            layer.data[index].uv = value
        return {"affected_faces": len(faces), "affected_corners": len(indices)}
    if operation_type in {"unwrap", "pack"}:
        record = _selection(resources, operation.get("selection_id"), obj, mesh, "FACE")
        faces = tuple(record.indices)
        pins = sum(
            bool(getattr(layer.data[index], "pin_uv", False))
            for face_index in faces
            for index in range(
                int(mesh.polygons[face_index].loop_start),
                int(mesh.polygons[face_index].loop_start + mesh.polygons[face_index].loop_total),
            )
        )
        policy = operation.get("pin_policy" if operation_type == "unwrap" else "pinned_policy")
        if policy in {"ERROR_IF_PRESENT"} and pins:
            raise MeshUVOperationError(
                "MESH_UV_PIN_CONFLICT", "Selected UV faces contain pinned corners"
            )
        _run_uv_operator(mesh, layer.name, faces, operation)
        return {"affected_faces": len(faces), "pinned_corners": pins, "isolated_operator": True}
    raise MeshUVOperationError(
        "MESH_UV_OPERATION_INVALID", f"Unsupported UV operation: {operation_type}"
    )


def edit_uv(
    transaction: Transaction, resources: MeshResourceBook, params: dict[str, Any]
) -> dict[str, Any]:
    obj, initial_mesh, data_scope, _refs = validate_mesh_attribute_target(params)
    expected_uv = params.get("expected_uv_fingerprint")
    actual_uv = uv_fingerprint(initial_mesh)
    if expected_uv != actual_uv:
        raise MeshUVOperationError(
            "MESH_UV_FINGERPRINT_MISMATCH",
            "UV evidence changed",
            kind="conflict",
            details={"expected": expected_uv, "actual": actual_uv},
        )
    operation = params.get("operation")
    if not isinstance(operation, dict) or not isinstance(operation.get("type"), str):
        raise MeshUVOperationError("MESH_UV_OPERATION_INVALID", "operation must be a typed object")
    # Resolve every revision-bound input before OBJECT scope can create a private Mesh copy.
    operation_type = str(operation["type"])
    if operation_type == "seam_set":
        _selection(resources, operation.get("selection_id"), obj, initial_mesh, "EDGE")
    elif operation_type in {"transform", "unwrap", "pack"}:
        _selection(resources, operation.get("selection_id"), obj, initial_mesh, "FACE")
    if operation_type not in {"layer_create", "seam_set"}:
        _layer_ref(initial_mesh, operation.get("layer"))
    transaction.ensure_capacity()
    guard = transaction.mesh_snapshot_guard(
        initial_mesh.name, session_identity("mesh", initial_mesh)
    )
    new_guard = guard is None
    if guard is None:
        guard = _create_guard(transaction, obj, initial_mesh, data_scope)
    else:
        _validate_guard(guard)
        if guard.data_scope != data_scope:
            raise MeshUVOperationError(
                "MESH_UV_OPERATION_INVALID", "data_scope must remain stable within a transaction"
            )
    mesh = bpy.data.meshes.get(guard.mesh_name)
    if mesh is None:
        raise MeshUVOperationError(
            "MESH_DATA_CONFLICT", "Guarded Mesh no longer exists", kind="conflict"
        )
    working_operation = dict(operation)
    if new_guard and mesh is not initial_mesh:
        for field in ("layer", "source_layer"):
            ref = working_operation.get(field)
            if isinstance(ref, dict):
                copied = mesh.uv_layers.get(ref.get("layer_name"))
                if copied is not None:
                    working_operation[field] = {
                        "layer_name": copied.name,
                        "expected_layer_identity": session_identity("uv_layer", copied),
                    }
    before_mesh_fingerprint = mesh_fingerprint(mesh)
    before_uv_fingerprint = uv_fingerprint(mesh)
    before_revision = mesh_revision_id(mesh)
    before_topology = topology_fingerprint(mesh)
    call_snapshot = mesh.copy()
    call_snapshot.name = f"{mesh.name}.MCP-UV-Call-Snapshot"
    try:
        evidence = _apply_operation(mesh, obj, resources, working_operation)
        mesh.update()
        if topology_fingerprint(mesh) != before_topology:
            raise MeshUVOperationError(
                "MESH_UV_TOPOLOGY_DRIFT", "UV edit changed Mesh topology", kind="blender_api"
            )
        if any(
            not math.isfinite(float(value)) or abs(float(value)) > UV_LIMIT
            for layer in mesh.uv_layers
            for item in layer.data
            for value in item.uv
        ):
            raise MeshUVOperationError(
                "MESH_UV_OPERATION_INVALID",
                "UV edit produced invalid coordinates",
                kind="blender_api",
            )
    except (MeshOperationError, MeshResourceError) as exc:
        _restore_failed_edit(mesh, call_snapshot, before_mesh_fingerprint, exc)
        if new_guard:
            _remove_new_guard(transaction, guard)
        raise
    except Exception as exc:
        _restore_failed_edit(mesh, call_snapshot, before_mesh_fingerprint, exc)
        if new_guard:
            _remove_new_guard(transaction, guard)
        raise MeshUVOperationError(
            "MESH_UV_EDIT_FAILED",
            f"UV edit failed: {type(exc).__name__}",
            kind="blender_api",
            details={"message": str(exc)},
        ) from exc
    finally:
        _remove_temporary_mesh(call_snapshot)
    after_mesh_fingerprint = mesh_fingerprint(mesh)
    after_uv_fingerprint = uv_fingerprint(mesh)
    changed = after_mesh_fingerprint != before_mesh_fingerprint
    if not changed:
        if new_guard:
            _remove_new_guard(transaction, guard)
    else:
        guard.expected_fingerprint = after_mesh_fingerprint
        guard.expected_users = int(mesh.users)
        guard.expected_user_objects = mesh_user_refs(mesh)
        transaction.record(
            MeshEditDelta(
                object_name=obj.name,
                object_identity=session_identity("object", obj),
                mesh_name=mesh.name,
                mesh_identity=session_identity("mesh", mesh),
                operation=f"uv.{operation_type}",
                before_fingerprint=before_mesh_fingerprint,
                after_fingerprint=after_mesh_fingerprint,
                data_scope=data_scope,
            )
        )
        refresh_structure_guard_if_present(transaction, "object", obj)
        refresh_structure_guard_if_present(transaction, "mesh", mesh)
    return {
        "transaction_id": transaction.transaction_id,
        "changed": changed,
        "operation": operation_type,
        "data_scope": data_scope,
        "object": {"name": obj.name, "session_identity": session_identity("object", obj)},
        "mesh": {
            "name": mesh.name,
            "session_identity": session_identity("mesh", mesh),
            "users": int(mesh.users),
            "counts": mesh_counts(mesh),
        },
        "before_mesh_fingerprint": before_mesh_fingerprint,
        "after_mesh_fingerprint": after_mesh_fingerprint,
        "before_uv_fingerprint": before_uv_fingerprint,
        "after_uv_fingerprint": after_uv_fingerprint,
        "before_mesh_revision_id": before_revision,
        "after_mesh_revision_id": mesh_revision_id(mesh),
        "evidence": evidence,
        "delta": {"type": "mesh_uv", "recorded": changed, "snapshot_reused": not new_guard},
        "warnings": [],
    }
