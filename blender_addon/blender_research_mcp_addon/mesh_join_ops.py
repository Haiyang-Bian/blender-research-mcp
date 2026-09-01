"""Exact cross-object base-Mesh composition without Blender operator context."""

from __future__ import annotations

import contextlib
import math
import uuid
from typing import Any

import bmesh
import bpy
from mathutils import Matrix, Vector

from .authoring_ops import object_summary, unlink_object
from .lookdev_ops import session_identity
from .mesh_component_map import remap_selection
from .mesh_component_map_model import DOMAINS, ComponentRelation, make_component_map
from .mesh_ops import (
    MAX_EDGES,
    MAX_FACES,
    MAX_LOOPS,
    MAX_VERTICES,
    MeshOperationError,
    mesh_counts,
    mesh_fingerprint,
    mesh_revision_id,
    mesh_user_refs,
    shape_key_state_fingerprint,
    topology_fingerprint,
)
from .mesh_query_ops import validate_selection
from .mesh_resource_model import MeshResourceBook, MeshResourceError
from .mesh_uv_ops import uv_fingerprint
from .mesh_weight_ops import group_schema_fingerprint, weights_fingerprint
from .modifier_ops import modifier_stack_fingerprint
from .scene_organization_ops import collection_summary
from .structural_ops import (
    make_structure_guard,
    restore_structural_delta,
    structure_fingerprint,
)
from .transaction_model import MeshEditDelta, StructuralDelta, Transaction

_BUILTIN_ATTRIBUTES = {"position", "material_index", "sharp_edge", "sharp_face"}


class MeshJoinError(MeshOperationError):
    pass


def _error(code: str, message: str, *, kind: str = "validation", **details: Any) -> None:
    raise MeshJoinError(code, message, kind=kind, details=details)


def _source_attributes(mesh: Any) -> tuple[set[str], set[str]]:
    uv_names = {str(layer.name) for layer in mesh.uv_layers}
    color_names = {str(layer.name) for layer in mesh.color_attributes}
    generic = {
        str(attribute.name)
        for attribute in mesh.attributes
        if not str(attribute.name).startswith(".")
        and str(attribute.name) not in _BUILTIN_ATTRIBUTES
        and str(attribute.name) not in uv_names
        and str(attribute.name) not in color_names
    }
    return color_names, generic


def _require_source(raw: dict[str, Any], book: MeshResourceBook) -> dict[str, Any]:
    name = raw.get("object_name")
    obj = bpy.data.objects.get(str(name))
    if obj is None or obj.type != "MESH" or obj.data is None:
        _error(
            "MESH_JOIN_SOURCE_INVALID",
            f"Join source is not a live Mesh object: {name}",
            kind="not_found",
        )
    mesh = obj.data
    if obj.library is not None or mesh.library is not None:
        _error("MESH_JOIN_SOURCE_INVALID", f"Join source is library linked: {name}")
    if str(getattr(obj, "mode", "OBJECT")) != "OBJECT":
        _error("MESH_JOIN_SOURCE_INVALID", f"Join source is not in Object Mode: {name}")
    exact = {
        "expected_object_identity": session_identity("object", obj),
        "expected_object_structure_fingerprint": structure_fingerprint("object", obj),
        "mesh_name": str(mesh.name),
        "expected_mesh_identity": session_identity("mesh", mesh),
        "expected_mesh_users": int(mesh.users),
        "expected_mesh_user_objects": [
            {"object_name": item_name, "expected_object_identity": identity}
            for item_name, identity in mesh_user_refs(mesh)
        ],
        "expected_mesh_fingerprint": mesh_fingerprint(mesh),
        "expected_mesh_revision_id": mesh_revision_id(mesh),
        "expected_uv_fingerprint": uv_fingerprint(mesh),
        "expected_group_schema_fingerprint": group_schema_fingerprint(obj),
        "expected_weights_fingerprint": weights_fingerprint(mesh),
        "expected_shape_key_state_fingerprint": shape_key_state_fingerprint(obj),
        "expected_modifier_stack_fingerprint": modifier_stack_fingerprint(obj),
    }
    mismatches = {
        key: {"expected": raw.get(key), "actual": value}
        for key, value in exact.items()
        if raw.get(key) != value
    }
    if mismatches:
        _error(
            "MESH_JOIN_DATA_CONFLICT",
            f"Join source evidence changed: {name}",
            kind="conflict",
            object_name=name,
            mismatches=mismatches,
        )
    selections = []
    for selection_id in raw.get("selection_ids", []):
        selection = book.selection(str(selection_id))
        selection_obj, selection_mesh = validate_selection(selection)
        if selection_obj is not obj or selection_mesh is not mesh:
            _error(
                "MESH_JOIN_SOURCE_INVALID",
                f"SelectionSet does not belong to source: {selection_id}",
            )
        selections.append(selection)
    colors, generic = _source_attributes(mesh)
    return {
        "raw": raw,
        "object": obj,
        "mesh": mesh,
        "selections": tuple(selections),
        "colors": colors,
        "generic": generic,
    }


def _require_output(params: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    raw = params.get("output")
    if not isinstance(raw, dict):
        _error("MESH_JOIN_SOURCE_INVALID", "output must be an object")
    object_name = str(raw.get("new_object_name", ""))
    mesh_name = str(raw.get("new_mesh_name", ""))
    if bpy.data.objects.get(object_name) is not None or bpy.data.meshes.get(mesh_name) is not None:
        _error(
            "MESH_JOIN_NAME_CONFLICT",
            "The exact output object or Mesh name already exists",
            kind="conflict",
            object_name=object_name,
            mesh_name=mesh_name,
        )
    collection_name = str(raw.get("collection_name", ""))
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        _error(
            "MESH_JOIN_SOURCE_INVALID",
            f"Output Collection does not exist: {collection_name}",
            kind="not_found",
        )
    collection_mismatches = {}
    identity = session_identity("collection", collection)
    fingerprint = structure_fingerprint("collection", collection)
    if raw.get("expected_collection_identity") != identity:
        collection_mismatches["identity"] = identity
    if raw.get("expected_collection_structure_fingerprint") != fingerprint:
        collection_mismatches["structure_fingerprint"] = fingerprint
    if collection.library is not None or collection_mismatches:
        _error(
            "MESH_JOIN_DATA_CONFLICT",
            "Output Collection evidence changed or is read-only",
            kind="conflict",
            collection_name=collection_name,
            actual=collection_mismatches,
        )
    coordinate = raw.get("coordinate_frame")
    if not isinstance(coordinate, dict):
        _error("MESH_JOIN_COORDINATE_FRAME_INVALID", "coordinate_frame must be an object")
    coordinate_type = coordinate.get("type")
    if coordinate_type == "WORLD":
        matrix_world = Matrix.Identity(4)
        frame_source = None
    elif coordinate_type == "SOURCE_OBJECT":
        frame_source = next(
            (
                item["object"]
                for item in sources
                if item["object"].name == coordinate.get("source_object_name")
                and session_identity("object", item["object"])
                == coordinate.get("expected_source_object_identity")
            ),
            None,
        )
        if frame_source is None:
            _error(
                "MESH_JOIN_COORDINATE_FRAME_INVALID",
                "SOURCE_OBJECT coordinate frame no longer matches one exact source",
                kind="conflict",
            )
        matrix_world = frame_source.matrix_world.copy()
    else:
        _error(
            "MESH_JOIN_COORDINATE_FRAME_INVALID",
            f"Unsupported coordinate frame: {coordinate_type}",
        )
    try:
        inverse = matrix_world.inverted()
    except Exception as exc:
        _error(
            "MESH_JOIN_COORDINATE_FRAME_INVALID",
            "Output coordinate frame is not invertible",
            cause=type(exc).__name__,
        )
    return {
        "raw": raw,
        "collection": collection,
        "matrix_world": matrix_world,
        "inverse_matrix_world": inverse,
        "frame_source": frame_source,
    }


def _material_schema(sources: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    schemas = [tuple(item for item in source["mesh"].materials) for source in sources]
    if policy == "DROP":
        materials: list[Any] = []
    elif policy == "ERROR_IF_DIFFERENT":
        identities = [
            tuple(session_identity("material", item) if item is not None else None for item in row)
            for row in schemas
        ]
        if any(row != identities[0] for row in identities[1:]):
            _error(
                "MESH_JOIN_ATTRIBUTE_SCHEMA_CONFLICT",
                "Source material slot schemas differ",
                domain="materials",
            )
        materials = list(schemas[0])
    else:
        materials = []
        seen: set[str | None] = set()
        for row in schemas:
            for material in row:
                identity = session_identity("material", material) if material is not None else None
                if identity not in seen:
                    seen.add(identity)
                    materials.append(material)
    indices = {
        session_identity("material", material) if material is not None else None: index
        for index, material in enumerate(materials)
    }
    return {
        "items": materials,
        "indices": indices,
        "summary": [
            {
                "slot_index": index,
                "name": material.name if material is not None else None,
                "identity": (
                    session_identity("material", material) if material is not None else None
                ),
            }
            for index, material in enumerate(materials)
        ],
    }


def _uv_schema(sources: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    schemas = [
        tuple(
            (
                str(layer.name),
                bool(getattr(layer, "active_render", False)),
                bool(getattr(layer, "active_clone", False)),
            )
            for layer in source["mesh"].uv_layers
        )
        for source in sources
    ]
    if policy == "DROP":
        names: list[str] = []
    elif policy == "ERROR_IF_SCHEMA_DIFF":
        if any(row != schemas[0] for row in schemas[1:]):
            _error(
                "MESH_JOIN_ATTRIBUTE_SCHEMA_CONFLICT",
                "Source UV Layer schemas differ",
                domain="uv",
            )
        names = [item[0] for item in schemas[0]]
    else:
        names = list(dict.fromkeys(item[0] for row in schemas for item in row))
    roles = {}
    for name in names:
        roles[name] = next(
            (
                {"active_render": render, "active_clone": clone}
                for row in schemas
                for item, render, clone in row
                if item == name
            ),
            {"active_render": False, "active_clone": False},
        )
    return {"names": names, "roles": roles}


def _group_schema(sources: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    schemas = [
        tuple(
            (str(group.name), bool(group.lock_weight))
            for group in source["object"].vertex_groups
        )
        for source in sources
    ]
    if policy == "DROP":
        groups: list[tuple[str, bool]] = []
    elif policy == "ERROR_IF_SCHEMA_DIFF":
        if any(row != schemas[0] for row in schemas[1:]):
            _error(
                "MESH_JOIN_ATTRIBUTE_SCHEMA_CONFLICT",
                "Source Vertex Group schemas differ",
                domain="weights",
            )
        groups = list(schemas[0])
    else:
        by_name: dict[str, bool] = {}
        for row in schemas:
            for name, locked in row:
                by_name.setdefault(name, locked)
        groups = list(by_name.items())
    return {"items": groups, "indices": {name: index for index, (name, _lock) in enumerate(groups)}}


def _color_schema(sources: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    schemas = [
        tuple(
            (str(layer.name), str(layer.data_type), str(layer.domain))
            for layer in source["mesh"].color_attributes
        )
        for source in sources
    ]
    if policy == "ERROR_IF_PRESENT" and any(schemas):
        _error(
            "MESH_JOIN_ATTRIBUTE_SCHEMA_CONFLICT",
            "Join policy rejects existing color attributes",
            domain="colors",
        )
    if policy != "MERGE_BY_NAME":
        return {"items": []}
    items: dict[str, tuple[str, str]] = {}
    for row in schemas:
        for name, data_type, domain in row:
            previous = items.get(name)
            if previous is not None and previous != (data_type, domain):
                _error(
                    "MESH_JOIN_ATTRIBUTE_SCHEMA_CONFLICT",
                    f"Color attribute schema differs for {name}",
                    domain="colors",
                )
            items.setdefault(name, (data_type, domain))
    return {"items": [(name, *schema) for name, schema in items.items()]}


def preflight_join(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    raw_sources = params.get("sources")
    if not isinstance(raw_sources, list) or not 2 <= len(raw_sources) <= 32:
        _error("MESH_JOIN_SOURCE_INVALID", "sources must contain 2 to 32 records")
    sources = [_require_source(raw, book) for raw in raw_sources]
    object_refs = [session_identity("object", source["object"]) for source in sources]
    if len(set(object_refs)) != len(object_refs):
        _error("MESH_JOIN_SOURCE_DUPLICATE", "Join sources must be unique objects")
    output = _require_output(params, sources)
    attributes = params.get("attributes")
    dependencies = params.get("dependencies")
    if not isinstance(attributes, dict) or not isinstance(dependencies, dict):
        _error("MESH_JOIN_SOURCE_INVALID", "attributes and dependencies must be objects")
    if dependencies.get("shape_keys") == "ERROR_IF_PRESENT" and any(
        source["mesh"].shape_keys is not None for source in sources
    ):
        _error(
            "MESH_JOIN_DEPENDENCY_UNSUPPORTED",
            "Join policy rejects source Shape Keys",
            dependency="shape_keys",
        )
    if dependencies.get("modifiers") == "ERROR_IF_PRESENT" and any(
        len(source["object"].modifiers) for source in sources
    ):
        _error(
            "MESH_JOIN_DEPENDENCY_UNSUPPORTED",
            "Join policy rejects source Modifiers",
            dependency="modifiers",
        )
    if attributes.get("generic") == "ERROR_IF_PRESENT" and any(
        source["generic"] for source in sources
    ):
        _error(
            "MESH_JOIN_ATTRIBUTE_SCHEMA_CONFLICT",
            "Join policy rejects generic attributes",
            domain="generic",
            attributes=sorted({name for source in sources for name in source["generic"]}),
        )
    if attributes.get("custom_normals") == "ERROR_IF_PRESENT" and any(
        bool(getattr(source["mesh"], "has_custom_normals", False)) for source in sources
    ):
        _error(
            "MESH_JOIN_ATTRIBUTE_SCHEMA_CONFLICT",
            "Join policy rejects custom split normals",
            domain="custom_normals",
        )
    material_schema = _material_schema(sources, str(attributes.get("materials")))
    uv_schema = _uv_schema(sources, str(attributes.get("uv")))
    group_schema = _group_schema(sources, str(attributes.get("weights")))
    color_schema = _color_schema(sources, str(attributes.get("colors")))
    counts = {
        "vertices": sum(len(source["mesh"].vertices) for source in sources),
        "edges": sum(len(source["mesh"].edges) for source in sources),
        "faces": sum(len(source["mesh"].polygons) for source in sources),
        "loops": sum(len(source["mesh"].loops) for source in sources),
    }
    if (
        counts["vertices"] > MAX_VERTICES
        or counts["edges"] > MAX_EDGES
        or counts["faces"] > MAX_FACES
        or counts["loops"] > MAX_LOOPS
    ):
        _error(
            "MESH_JOIN_BUDGET_EXCEEDED",
            "Joined Mesh exceeds the bounded geometry budget",
            counts=counts,
        )
    source_offsets = []
    vertex_offset = edge_offset = face_offset = loop_offset = 0
    for source in sources:
        mesh = source["mesh"]
        source_offsets.append(
            {
                "object_name": source["object"].name,
                "object_identity": session_identity("object", source["object"]),
                "vertex_offset": vertex_offset,
                "edge_offset": edge_offset,
                "face_offset": face_offset,
                "loop_offset": loop_offset,
                "counts": mesh_counts(mesh),
            }
        )
        vertex_offset += len(mesh.vertices)
        edge_offset += len(mesh.edges)
        face_offset += len(mesh.polygons)
        loop_offset += len(mesh.loops)
    delta_capacity = 1 + (
        len(sources) if output["raw"].get("source_disposition") == "DELETE_ON_COMMIT" else 0
    )
    return {
        "sources": sources,
        "output": output,
        "public": {
            "status": "ready",
            "source_count": len(sources),
            "counts": counts,
            "source_offsets": source_offsets,
            "coordinate_frame": output["raw"]["coordinate_frame"],
            "source_disposition": output["raw"].get("source_disposition", "KEEP"),
            "attribute_schemas": {
                "materials": material_schema["summary"],
                "uv_layers": uv_schema["names"],
                "vertex_groups": [name for name, _locked in group_schema["items"]],
                "color_attributes": [name for name, _type, _domain in color_schema["items"]],
            },
            "dependencies": {
                "shape_keys": dependencies.get("shape_keys"),
                "modifiers": dependencies.get("modifiers"),
            },
            "transaction_delta_capacity": delta_capacity,
            "warnings": [],
            "collection": collection_summary(output["collection"]),
        },
        "schemas": {
            "materials": material_schema,
            "uv": uv_schema,
            "weights": group_schema,
            "colors": color_schema,
        },
    }


def _source_boundary_vertices(mesh: Any) -> tuple[int, ...]:
    edge_faces = [0 for _edge in mesh.edges]
    for polygon in mesh.polygons:
        for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
            edge_faces[int(mesh.loops[loop_index].edge_index)] += 1
    return tuple(
        sorted(
            {
                int(vertex)
                for edge, face_count in zip(mesh.edges, edge_faces, strict=True)
                if face_count == 1
                for vertex in edge.vertices
            }
        )
    )


def _boundary_summary(mesh: Any) -> dict[str, int]:
    edge_faces = [0 for _edge in mesh.edges]
    for polygon in mesh.polygons:
        for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
            edge_faces[int(mesh.loops[loop_index].edge_index)] += 1
    boundary_edges = [
        edge for edge, face_count in zip(mesh.edges, edge_faces, strict=True) if face_count == 1
    ]
    return {
        "vertices": len(
            {
                int(vertex)
                for edge in boundary_edges
                for vertex in edge.vertices
            }
        ),
        "edges": len(boundary_edges),
    }


def _copy_color_values(source: Any, target: Any, domain_offset: int) -> None:
    for index, item in enumerate(source.data):
        target_item = target.data[domain_offset + index]
        if hasattr(item, "color") and hasattr(target_item, "color"):
            target_item.color = tuple(float(value) for value in item.color)


def _build_output(preflight: dict[str, Any]) -> tuple[Any, Any, list[dict[str, int]]]:
    sources = preflight["sources"]
    output_evidence = preflight["output"]
    schemas = preflight["schemas"]
    vertices: list[tuple[float, float, float]] = []
    edges: list[tuple[int, int]] = []
    faces: list[tuple[int, ...]] = []
    offsets: list[dict[str, int]] = []
    vertex_offset = edge_offset = face_offset = loop_offset = 0
    inverse = output_evidence["inverse_matrix_world"]
    for source in sources:
        obj = source["object"]
        mesh = source["mesh"]
        transform = inverse @ obj.matrix_world
        offsets.append(
            {
                "vertex": vertex_offset,
                "edge": edge_offset,
                "face": face_offset,
                "loop": loop_offset,
            }
        )
        vertices.extend(
            tuple(float(value) for value in (transform @ vertex.co))
            for vertex in mesh.vertices
        )
        edges.extend(
            (int(edge.vertices[0]) + vertex_offset, int(edge.vertices[1]) + vertex_offset)
            for edge in mesh.edges
        )
        faces.extend(
            tuple(int(vertex) + vertex_offset for vertex in polygon.vertices)
            for polygon in mesh.polygons
        )
        vertex_offset += len(mesh.vertices)
        edge_offset += len(mesh.edges)
        face_offset += len(mesh.polygons)
        loop_offset += len(mesh.loops)

    raw_output = output_evidence["raw"]
    mesh = bpy.data.meshes.new(str(raw_output["new_mesh_name"]))
    obj = bpy.data.objects.new(str(raw_output["new_object_name"]), mesh)
    try:
        mesh.from_pydata(vertices, edges, faces)
        mesh.update(calc_edges=True, calc_edges_loose=True)
        obj.matrix_world = output_evidence["matrix_world"].copy()
        obj.parent = None
        output_evidence["collection"].objects.link(obj)
        obj.select_set(False)
        for material in schemas["materials"]["items"]:
            mesh.materials.append(material)
        for source, offset in zip(sources, offsets, strict=True):
            source_mesh = source["mesh"]
            for index, source_edge in enumerate(source_mesh.edges):
                target_edge = mesh.edges[offset["edge"] + index]
                target_edge.use_seam = bool(source_edge.use_seam)
                target_edge.use_edge_sharp = bool(source_edge.use_edge_sharp)
            for index, source_polygon in enumerate(source_mesh.polygons):
                target_polygon = mesh.polygons[offset["face"] + index]
                target_polygon.use_smooth = bool(source_polygon.use_smooth)
                if schemas["materials"]["items"]:
                    source_material = (
                        source_mesh.materials[int(source_polygon.material_index)]
                        if int(source_polygon.material_index) < len(source_mesh.materials)
                        else None
                    )
                    identity = (
                        session_identity("material", source_material)
                        if source_material is not None
                        else None
                    )
                    target_polygon.material_index = schemas["materials"]["indices"][identity]

        for name in schemas["uv"]["names"]:
            target_layer = mesh.uv_layers.new(name=name, do_init=False)
            role = schemas["uv"]["roles"][name]
            target_layer.active_render = bool(role["active_render"])
            target_layer.active_clone = bool(role["active_clone"])
            for source, offset in zip(sources, offsets, strict=True):
                source_layer = source["mesh"].uv_layers.get(name)
                if source_layer is None:
                    continue
                for index, item in enumerate(source_layer.data):
                    target_item = target_layer.data[offset["loop"] + index]
                    target_item.uv = tuple(float(value) for value in item.uv)
                    if hasattr(target_item, "pin_uv"):
                        target_item.pin_uv = bool(getattr(item, "pin_uv", False))

        for name, data_type, domain in schemas["colors"]["items"]:
            target_color = mesh.color_attributes.new(name=name, type=data_type, domain=domain)
            domain_key = {
                "POINT": "vertex",
                "EDGE": "edge",
                "FACE": "face",
                "CORNER": "loop",
            }.get(domain)
            if domain_key is None:
                _error(
                    "MESH_JOIN_ATTRIBUTE_SCHEMA_CONFLICT",
                    f"Unsupported color attribute domain: {domain}",
                    domain="colors",
                )
            for source, offset in zip(sources, offsets, strict=True):
                source_color = source["mesh"].color_attributes.get(name)
                if source_color is not None:
                    _copy_color_values(source_color, target_color, offset[domain_key])

        for name, locked in schemas["weights"]["items"]:
            group = obj.vertex_groups.new(name=name)
            group.lock_weight = bool(locked)
        for source, offset in zip(sources, offsets, strict=True):
            source_obj = source["object"]
            source_mesh = source["mesh"]
            source_groups = {
                int(group.index): str(group.name) for group in source_obj.vertex_groups
            }
            for vertex in source_mesh.vertices:
                target_index = int(vertex.index) + offset["vertex"]
                for membership in vertex.groups:
                    group_name = source_groups.get(int(membership.group))
                    if group_name in schemas["weights"]["indices"]:
                        obj.vertex_groups[group_name].add(
                            [target_index], float(membership.weight), "REPLACE"
                        )
        mesh.update(calc_edges=True, calc_edges_loose=True)
        return obj, mesh, offsets
    except Exception:
        if bpy.data.objects.get(obj.name) is obj:
            bpy.data.objects.remove(obj)
        if bpy.data.meshes.get(mesh.name) is mesh and int(mesh.users) == 0:
            bpy.data.meshes.remove(mesh)
        raise


def _map_evidence(obj: Any, mesh: Any) -> dict[str, Any]:
    return {
        "object_name": str(obj.name),
        "object_identity": session_identity("object", obj),
        "mesh_name": str(mesh.name),
        "mesh_identity": session_identity("mesh", mesh),
        "mesh_revision_id": mesh_revision_id(mesh),
        "mesh_fingerprint": mesh_fingerprint(mesh),
    }


def join_meshes(
    transaction: Transaction,
    book: MeshResourceBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    preflight = preflight_join(book, params)
    sources = preflight["sources"]
    transaction.ensure_capacity(preflight["public"]["transaction_delta_capacity"])
    obj = mesh = None
    maps = []
    selection_ids: list[str] = []
    delete_deltas: list[StructuralDelta] = []
    output_delta = None
    phase = "create_output"
    try:
        obj, mesh, offsets = _build_output(preflight)
        output_delta = StructuralDelta(
            kind="mesh_join",
            action="create_resource",
            before=(),
            after=(make_structure_guard("object", obj), make_structure_guard("mesh", mesh)),
            payload={
                "resource": obj,
                "resource_kind": "object",
                "resource_name": obj.name,
                "owned_resources": (("mesh", mesh),),
            },
        )
        phase = "component_maps"
        join_id = str(uuid.uuid4())
        branch_results = []
        for source, offset in zip(sources, offsets, strict=True):
            source_obj = source["object"]
            source_mesh = source["mesh"]
            relations = {
                "VERTEX": tuple(
                    ComponentRelation(index, (index + offset["vertex"],), "SURVIVED")
                    for index in range(len(source_mesh.vertices))
                ),
                "EDGE": tuple(
                    ComponentRelation(index, (index + offset["edge"],), "SURVIVED")
                    for index in range(len(source_mesh.edges))
                ),
                "FACE": tuple(
                    ComponentRelation(index, (index + offset["face"],), "SURVIVED")
                    for index in range(len(source_mesh.polygons))
                ),
            }
            empty = {domain: () for domain in DOMAINS}
            record = make_component_map(
                transaction_id=transaction.transaction_id,
                operation="join",
                before=_map_evidence(source_obj, source_mesh),
                after=_map_evidence(obj, mesh),
                after_users=int(mesh.users),
                after_user_objects=mesh_user_refs(mesh),
                relations=relations,
                created=empty,
                deleted=empty,
                map_kind="JOIN_BRANCH",
                join_id=join_id,
                branch_role=str(source_obj.name),
            )
            book.add_component_map(record)
            maps.append(record)
            per_domain = {}
            for domain, count, domain_offset in (
                ("VERTEX", len(source_mesh.vertices), offset["vertex"]),
                ("EDGE", len(source_mesh.edges), offset["edge"]),
                ("FACE", len(source_mesh.polygons), offset["face"]),
            ):
                selection = book.add_selection(
                    object_name=obj.name,
                    object_identity=session_identity("object", obj),
                    mesh_name=mesh.name,
                    mesh_identity=session_identity("mesh", mesh),
                    mesh_revision_id=mesh_revision_id(mesh),
                    mesh_fingerprint=mesh_fingerprint(mesh),
                    expected_users=int(mesh.users),
                    expected_user_objects=mesh_user_refs(mesh),
                    domain=domain,
                    indices=tuple(range(domain_offset, domain_offset + count)),
                    weights=None,
                    source_query={
                        "type": "join_branch",
                        "join_id": join_id,
                        "source": source["object"].name,
                    },
                )
                selection_ids.append(selection.selection_id)
                per_domain[domain] = selection.summary()
            boundary_indices = tuple(
                index + offset["vertex"] for index in _source_boundary_vertices(source_mesh)
            )
            boundary = book.add_selection(
                object_name=obj.name,
                object_identity=session_identity("object", obj),
                mesh_name=mesh.name,
                mesh_identity=session_identity("mesh", mesh),
                mesh_revision_id=mesh_revision_id(mesh),
                mesh_fingerprint=mesh_fingerprint(mesh),
                expected_users=int(mesh.users),
                expected_user_objects=mesh_user_refs(mesh),
                domain="VERTEX",
                indices=boundary_indices,
                weights=None,
                source_query={
                    "type": "join_boundary",
                    "join_id": join_id,
                    "source": source_obj.name,
                },
            )
            selection_ids.append(boundary.selection_id)
            rebound = []
            for original in source["selections"]:
                result = remap_selection(
                    book,
                    {
                        "selection_id": original.selection_id,
                        "component_map_id": record.component_map_id,
                        "mode": "ALL_MAPPED",
                        "weight_merge": "MAX",
                    },
                )
                selection_ids.append(str(result["selection"]["selection_id"]))
                rebound.append(result["selection"])
            branch_results.append(
                {
                    "source_object": object_summary(source_obj),
                    "component_map": record.summary(),
                    "selections": per_domain,
                    "boundary_selection": boundary.summary(),
                    "rebound_selections": rebound,
                }
            )

        phase = "deferred_source_delete"
        if preflight["output"]["raw"].get("source_disposition") == "DELETE_ON_COMMIT":
            for source in sources:
                _source_obj, delta = unlink_object(
                    transaction,
                    object_name=source["object"].name,
                    expected_object_identity=session_identity("object", source["object"]),
                )
                delete_deltas.append(delta)
        transaction.record(output_delta)
        for delta in delete_deltas:
            transaction.record(delta)
    except (MeshJoinError, MeshResourceError):
        for delta in reversed(delete_deltas):
            with contextlib.suppress(Exception):
                restore_structural_delta(delta)
        for selection_id in selection_ids:
            book.release_selection(selection_id)
        for record in maps:
            book.release_component_map(record.component_map_id)
        if obj is not None and bpy.data.objects.get(obj.name) is obj:
            bpy.data.objects.remove(obj)
        if mesh is not None and bpy.data.meshes.get(mesh.name) is mesh and int(mesh.users) == 0:
            bpy.data.meshes.remove(mesh)
        raise
    except Exception as exc:
        for delta in reversed(delete_deltas):
            with contextlib.suppress(Exception):
                restore_structural_delta(delta)
        for selection_id in selection_ids:
            book.release_selection(selection_id)
        for record in maps:
            book.release_component_map(record.component_map_id)
        if obj is not None and bpy.data.objects.get(obj.name) is obj:
            bpy.data.objects.remove(obj)
        if mesh is not None and bpy.data.meshes.get(mesh.name) is mesh and int(mesh.users) == 0:
            bpy.data.meshes.remove(mesh)
        raise MeshJoinError(
            "MESH_JOIN_FAILED",
            f"Mesh join failed during {phase}: {type(exc).__name__}",
            kind="blender_api",
            details={"phase": phase, "error_type": type(exc).__name__, "message": str(exc)},
        ) from exc

    assert obj is not None and mesh is not None
    return {
        "transaction_id": transaction.transaction_id,
        "changed": True,
        "join_id": join_id,
        "output_object": object_summary(obj),
        "output_mesh": {
            "name": mesh.name,
            "session_identity": session_identity("mesh", mesh),
            "mesh_revision_id": mesh_revision_id(mesh),
            "mesh_fingerprint": mesh_fingerprint(mesh),
            "counts": mesh_counts(mesh),
        },
        "branches": branch_results,
        "attribute_schemas": preflight["public"]["attribute_schemas"],
        "source_disposition": preflight["public"]["source_disposition"],
        "delta": {
            "types": ["mesh_join", *("object_delete" for _item in delete_deltas)],
            "recorded": True,
        },
    }


def _weld_operation(raw: Any) -> dict[str, Any]:
    required = {"type", "selection_ids", "maximum_distance"}
    optional = {"mode", "destination", "weight_merge", "attribute_policy"}
    if not isinstance(raw, dict) or set(raw) - required - optional or required - set(raw):
        _error("MESH_WELD_SELECTION_INVALID", "weld_vertices has invalid fields")
    selection_ids = raw.get("selection_ids")
    if (
        not isinstance(selection_ids, list)
        or not 1 <= len(selection_ids) <= 8
        or any(not isinstance(item, str) or not item for item in selection_ids)
        or len(set(selection_ids)) != len(selection_ids)
    ):
        _error(
            "MESH_WELD_SELECTION_INVALID",
            "selection_ids must contain 1 to 8 unique SelectionSet IDs",
        )
    mode = raw.get("mode", "CROSS_SELECTIONS")
    if mode not in {"ALL_SELECTED", "CROSS_SELECTIONS"}:
        _error("MESH_WELD_SELECTION_INVALID", f"Unsupported weld mode: {mode}")
    if mode == "CROSS_SELECTIONS" and len(selection_ids) < 2:
        _error(
            "MESH_WELD_SELECTION_INVALID",
            "CROSS_SELECTIONS requires at least two SelectionSets",
        )
    distance = raw.get("maximum_distance")
    if (
        isinstance(distance, bool)
        or not isinstance(distance, (int, float))
        or not math.isfinite(float(distance))
        or not 0 < float(distance) <= 1_000_000
    ):
        _error(
            "MESH_WELD_SELECTION_INVALID",
            "maximum_distance must be finite, positive, and at most 1000000",
        )
    if raw.get("destination", "LOWEST_INDEX") not in {"LOWEST_INDEX", "CENTER"}:
        _error("MESH_WELD_SELECTION_INVALID", "Invalid weld destination")
    if raw.get("weight_merge", "MAX") not in {"MAX", "AVERAGE", "SUM_NORMALIZE"}:
        _error("MESH_WELD_SELECTION_INVALID", "Invalid weight_merge")
    return raw


def _weld_selections(
    book: MeshResourceBook,
    raw: dict[str, Any],
    obj: Any,
    mesh: Any,
) -> tuple[list[Any], dict[int, int], tuple[int, ...]]:
    selections = []
    memberships: dict[int, int] = {}
    union: set[int] = set()
    for set_index, selection_id in enumerate(raw["selection_ids"]):
        selection = book.selection(str(selection_id))
        selection_obj, selection_mesh = validate_selection(selection)
        if selection.domain != "VERTEX" or selection_obj is not obj or selection_mesh is not mesh:
            _error(
                "MESH_WELD_SELECTION_INVALID",
                f"SelectionSet must target live vertices on {obj.name}: {selection_id}",
            )
        if raw.get("mode", "CROSS_SELECTIONS") == "CROSS_SELECTIONS":
            overlap = union.intersection(selection.indices)
            if overlap:
                _error(
                    "MESH_WELD_SELECTION_INVALID",
                    "CROSS_SELECTIONS requires disjoint SelectionSets",
                    overlap_sample=sorted(overlap)[:64],
                )
        for index in selection.indices:
            union.add(index)
            memberships[index] = set_index
        selections.append(selection)
    if len(union) > 65_536:
        _error(
            "MESH_WELD_PAIR_BUDGET_EXCEEDED",
            "weld_vertices accepts at most 65536 selected vertices",
            selected_vertices=len(union),
        )
    return selections, memberships, tuple(sorted(union))


def _candidate_groups(
    mesh: Any,
    indices: tuple[int, ...],
    memberships: dict[int, int],
    *,
    mode: str,
    distance: float,
) -> tuple[list[tuple[int, ...]], int]:
    parent = {index: index for index in indices}

    def find(value: int) -> int:
        root = value
        while parent[root] != root:
            root = parent[root]
        while parent[value] != value:
            next_value = parent[value]
            parent[value] = root
            value = next_value
        return root

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            low, high = sorted((left_root, right_root))
            parent[high] = low

    buckets: dict[tuple[int, int, int], list[int]] = {}
    accepted = 0
    squared = distance * distance
    for index in indices:
        point = mesh.vertices[index].co
        cell = tuple(math.floor(float(point[axis]) / distance) for axis in range(3))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other in buckets.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()):
                        if mode == "CROSS_SELECTIONS" and memberships[other] == memberships[index]:
                            continue
                        delta = mesh.vertices[other].co - point
                        if float(delta.length_squared) <= squared:
                            accepted += 1
                            if accepted > 65_536:
                                _error(
                                    "MESH_WELD_PAIR_BUDGET_EXCEEDED",
                                    "weld_vertices accepted-pair budget exceeded",
                                    accepted_pairs=accepted,
                                )
                            union(other, index)
        buckets.setdefault(cell, []).append(index)
    grouped: dict[int, list[int]] = {}
    for index in indices:
        grouped.setdefault(find(index), []).append(index)
    groups = [tuple(sorted(values)) for values in grouped.values() if len(values) > 1]
    groups.sort(key=lambda values: values[0])
    return groups, accepted


def _merge_weight_values(values: list[dict[int, float]], mode: str) -> dict[int, float]:
    keys = sorted({key for item in values for key in item})
    merged = {}
    for key in keys:
        samples = [item.get(key, 0.0) for item in values]
        if mode == "MAX":
            value = max(samples)
        elif mode == "AVERAGE":
            value = sum(samples) / len(samples)
        else:
            value = sum(samples)
        if value > 0:
            merged[key] = float(value)
    if mode == "SUM_NORMALIZE":
        total = sum(merged.values())
        if total > 0:
            merged = {key: value / total for key, value in merged.items()}
    return merged


def weld_mesh_vertices(
    transaction: Transaction,
    book: MeshResourceBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    from .mesh_ops import (
        _create_guard,
        _mesh_reference,
        _remove_new_guard,
        _remove_temporary_mesh,
        _restore_failed_edit,
        _validate_guard,
        _validate_mesh_target,
        finish_topology_attributes,
        prepare_topology_attributes,
    )
    from .mesh_topology_ops import _finish_lineage, _map_evidence, _start_lineage
    from .structural_ops import refresh_structure_guard_if_present

    operation = _weld_operation(params.get("operation"))
    obj, initial_mesh, data_scope, _refs = _validate_mesh_target(params)
    selections, memberships, indices = _weld_selections(book, operation, obj, initial_mesh)
    groups, accepted_pairs = _candidate_groups(
        initial_mesh,
        indices,
        memberships,
        mode=str(operation.get("mode", "CROSS_SELECTIONS")),
        distance=float(operation["maximum_distance"]),
    )
    before_fingerprint = mesh_fingerprint(initial_mesh)
    before_topology = topology_fingerprint(initial_mesh)
    before_counts = mesh_counts(initial_mesh)
    before_boundary = _boundary_summary(initial_mesh)
    if not groups:
        return {
            "transaction_id": transaction.transaction_id,
            "changed": False,
            "operation": "weld_vertices",
            "object": object_summary(obj),
            "before_mesh_fingerprint": before_fingerprint,
            "after_mesh_fingerprint": before_fingerprint,
            "before_topology_fingerprint": before_topology,
            "after_topology_fingerprint": before_topology,
            "before_counts": before_counts,
            "after_counts": before_counts,
            "accepted_pairs": 0,
            "groups": [],
            "merged_vertex_reduction": 0,
            "boundary_changes": {
                "before": before_boundary,
                "after": before_boundary,
                "vertex_delta": 0,
                "edge_delta": 0,
            },
            "component_map": None,
            "rebound_selections": [],
            "delta": {"type": "mesh_edit", "recorded": False},
        }

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
            _error(
                "MESH_WELD_SELECTION_INVALID",
                "data_scope must remain stable in one transaction",
            )
    mesh = bpy.data.meshes.get(guard.mesh_name)
    if mesh is None:
        _error("MESH_JOIN_DATA_CONFLICT", "Guarded weld Mesh no longer exists", kind="conflict")
    attribute_evidence = prepare_topology_attributes(obj, mesh, operation)
    weight_guard = None
    new_weight_guard = False
    weight_call_state = None
    if attribute_evidence["weight_present"]:
        from .mesh_weight_ops import (
            _capture_weights,
            _create_weight_guard,
            _group_schema,
            _validate_weight_guard,
        )

        weight_guard = transaction.weight_snapshot_guard(mesh.name, session_identity("mesh", mesh))
        new_weight_guard = weight_guard is None
        if weight_guard is None:
            weight_guard = _create_weight_guard(transaction, obj, mesh, data_scope)
        else:
            _validate_weight_guard(weight_guard)
        weight_objects = tuple(bpy.data.objects[name] for name in weight_guard.object_identities)
        weight_call_state = (
            {item.name: session_identity("object", item) for item in weight_objects},
            {item.name: _group_schema(item, identities=False) for item in weight_objects},
            _capture_weights(mesh),
        )
    before_map = _map_evidence(obj, mesh)
    call_snapshot = mesh.copy()
    call_snapshot.name = f"{mesh.name}.MCP-Weld-Call"
    bm = bmesh.new()
    component_map = None
    rebound_ids: list[str] = []
    lineage = None
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        lineage = _start_lineage(bm)
        deform = bm.verts.layers.deform.verify()
        merged_target_sources: dict[int, tuple[int, ...]] = {}
        target_map = {}
        for group in groups:
            target_source = group[0]
            verts = [bm.verts[index] for index in group]
            values = [dict(vert[deform]) for vert in verts]
            target = bm.verts[target_source]
            if operation.get("destination", "LOWEST_INDEX") == "CENTER":
                center = Vector((0.0, 0.0, 0.0))
                for vert in verts:
                    center += vert.co
                target.co = center / len(verts)
            target_weights = _merge_weight_values(
                values, str(operation.get("weight_merge", "MAX"))
            )
            target_deform = target[deform]
            for key in list(target_deform.keys()):
                del target_deform[key]
            for key, value in target_weights.items():
                target_deform[key] = value
            target_map.update({vert: target for vert in verts if vert is not target})
            merged_target_sources[target_source] = group
        bmesh.ops.weld_verts(bm, targetmap=target_map)
        bm.normal_update()
        relations, created, deleted = _finish_lineage(bm, lineage, "weld_vertices")
        lineage = None
        vertex_relations = {
            item.source_index: item for item in relations["VERTEX"]
        }
        merged_sources = {source for group in groups for source in group}
        replacement = [
            item for item in relations["VERTEX"] if item.source_index not in merged_sources
        ]
        for target_source, group in merged_target_sources.items():
            target_relation = vertex_relations.get(target_source)
            if target_relation is None or len(target_relation.target_indices) != 1:
                _error(
                    "MESH_WELD_ATTRIBUTE_CONFLICT",
                    "Could not prove the exact merged target vertex",
                )
            target_index = target_relation.target_indices[0]
            replacement.extend(
                ComponentRelation(source, (target_index,), "MERGED") for source in group
            )
        relations["VERTEX"] = tuple(sorted(replacement, key=lambda item: item.source_index))
        deleted["VERTEX"] = tuple(
            index for index in deleted["VERTEX"] if index not in merged_sources
        )
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True, calc_edges_loose=True)
        attribute_effects = finish_topology_attributes(obj, mesh, attribute_evidence)
        component_map = make_component_map(
            transaction_id=transaction.transaction_id,
            operation="weld_vertices",
            before=before_map,
            after=_map_evidence(obj, mesh),
            after_users=int(mesh.users),
            after_user_objects=mesh_user_refs(mesh),
            relations=relations,
            created=created,
            deleted=deleted,
        )
        book.add_component_map(component_map)
        rebound = []
        for selection in selections:
            result = remap_selection(
                book,
                {
                    "selection_id": selection.selection_id,
                    "component_map_id": component_map.component_map_id,
                    "mode": "ALL_MAPPED",
                    "weight_merge": "MAX",
                },
            )
            rebound_ids.append(str(result["selection"]["selection_id"]))
            rebound.append(result["selection"])
    except (MeshJoinError, MeshResourceError, MeshOperationError) as exc:
        if component_map is not None:
            book.release_component_map(component_map.component_map_id)
        for selection_id in rebound_ids:
            book.release_selection(selection_id)
        _restore_failed_edit(mesh, call_snapshot, before_fingerprint, exc)
        if weight_call_state is not None:
            from .mesh_weight_ops import _restore_call_state

            _restore_call_state(mesh, *weight_call_state, exc)
        if new_guard:
            _remove_new_guard(transaction, guard)
        if new_weight_guard and weight_guard is not None:
            transaction.remove_weight_snapshot_guard(weight_guard)
        raise
    except Exception as exc:
        if component_map is not None:
            book.release_component_map(component_map.component_map_id)
        for selection_id in rebound_ids:
            book.release_selection(selection_id)
        _restore_failed_edit(mesh, call_snapshot, before_fingerprint, exc)
        if weight_call_state is not None:
            from .mesh_weight_ops import _restore_call_state

            _restore_call_state(mesh, *weight_call_state, exc)
        if new_guard:
            _remove_new_guard(transaction, guard)
        if new_weight_guard and weight_guard is not None:
            transaction.remove_weight_snapshot_guard(weight_guard)
        raise MeshJoinError(
            "MESH_JOIN_FAILED",
            f"Vertex weld failed: {type(exc).__name__}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc
    finally:
        if lineage is not None:
            for state in lineage.values():
                with contextlib.suppress(Exception):
                    state.sequence.layers.int.remove(state.layer)
        bm.free()
        _remove_temporary_mesh(call_snapshot)

    after_fingerprint = mesh_fingerprint(mesh)
    after_counts = mesh_counts(mesh)
    after_boundary = _boundary_summary(mesh)
    guard.expected_fingerprint = after_fingerprint
    guard.expected_users = int(mesh.users)
    guard.expected_user_objects = mesh_user_refs(mesh)
    if weight_guard is not None:
        from .mesh_weight_ops import _schema_fingerprints, weights_fingerprint

        weight_objects = tuple(bpy.data.objects[name] for name in weight_guard.object_identities)
        weight_guard.expected_schema_fingerprints = _schema_fingerprints(weight_objects)
        weight_guard.expected_weights_fingerprint = weights_fingerprint(mesh)
    transaction.record(
        MeshEditDelta(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            mesh_name=mesh.name,
            mesh_identity=session_identity("mesh", mesh),
            operation="weld_vertices",
            before_fingerprint=before_fingerprint,
            after_fingerprint=after_fingerprint,
            data_scope=data_scope,
        )
    )
    refresh_structure_guard_if_present(transaction, "object", obj)
    refresh_structure_guard_if_present(transaction, "mesh", mesh)
    return {
        "transaction_id": transaction.transaction_id,
        "changed": True,
        "operation": "weld_vertices",
        "data_scope": data_scope,
        "object": object_summary(obj),
        "before_mesh": _mesh_reference(initial_mesh),
        "after_mesh": _mesh_reference(mesh),
        "before_mesh_fingerprint": before_fingerprint,
        "after_mesh_fingerprint": after_fingerprint,
        "before_topology_fingerprint": before_topology,
        "after_topology_fingerprint": topology_fingerprint(mesh),
        "before_counts": before_counts,
        "after_counts": after_counts,
        "accepted_pairs": accepted_pairs,
        "groups": [list(group) for group in groups],
        "merged_vertex_reduction": before_counts["vertices"] - after_counts["vertices"],
        "boundary_changes": {
            "before": before_boundary,
            "after": after_boundary,
            "vertex_delta": after_boundary["vertices"] - before_boundary["vertices"],
            "edge_delta": after_boundary["edges"] - before_boundary["edges"],
        },
        "component_map": component_map.summary() if component_map is not None else None,
        "rebound_selections": rebound,
        "attribute_effects": attribute_effects,
        "delta": {"type": "mesh_edit", "recorded": True, "snapshot_reused": not new_guard},
    }


__all__ = ["MeshJoinError", "join_meshes", "preflight_join", "weld_mesh_vertices"]
