"""Read exact local Blender libraries and append one guarded static root."""

from __future__ import annotations

import hashlib
import os
from typing import Any

import bpy

from .authoring_ops import AuthoringOperationError, object_summary
from .lookdev_ops import session_identity
from .mesh_ops import MAX_EDGES, MAX_FACES, MAX_LOOPS, MAX_VERTICES, mesh_counts
from .scene_organization_ops import (
    _require_collection,
    _require_collection_parent,
    collection_summary,
)
from .structural_ops import make_structure_guard, structure_fingerprint, validate_structure_guard
from .transaction_model import StructuralDelta, Transaction

LIBRARY_KINDS = {"OBJECT": "objects", "COLLECTION": "collections", "MESH": "meshes"}
SUPPORTED_NEW_COLLECTIONS = {
    "armature": "armatures",
    "collection": "collections",
    "image": "images",
    "material": "materials",
    "mesh": "meshes",
    "node_group": "node_groups",
    "object": "objects",
}
REJECTED_NEW_COLLECTIONS = {
    "action": "actions",
    "camera": "cameras",
    "curve": "curves",
    "font": "fonts",
    "grease_pencil": "grease_pencils",
    "hair_curve": "hair_curves",
    "lattice": "lattices",
    "library": "libraries",
    "light": "lights",
    "metaball": "metaballs",
    "particle": "particles",
    "pointcloud": "pointclouds",
    "speaker": "speakers",
    "text": "texts",
    "texture": "textures",
    "volume": "volumes",
    "world": "worlds",
}
SUPPORTED_OBJECT_TYPES = {"MESH", "ARMATURE", "EMPTY"}
SUPPORTED_MODIFIER_TYPES = {"ARMATURE", "BEVEL", "BOOLEAN", "SOLIDIFY", "SUBSURF"}
MAX_CREATED_IDS = 4096
MAX_CREATED_OBJECTS = 512
MAX_CREATED_COLLECTIONS = 128


def library_entry_identity(file_sha256: str, kind: str, name: str) -> str:
    return hashlib.sha256(f"{file_sha256}:{kind}:{name}".encode()).hexdigest()


def validate_library_source(source: Any) -> tuple[str, os.stat_result]:
    if not isinstance(source, dict):
        raise AuthoringOperationError(
            "LIBRARY_PATH_INVALID", "source must be an object", kind="validation"
        )
    path = source.get("path")
    expected_size = source.get("expected_size_bytes")
    expected_modified = source.get("expected_modified_ns")
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        raise AuthoringOperationError(
            "LIBRARY_PATH_INVALID", "Library path must be absolute", kind="validation"
        )
    normalized = os.path.realpath(path)
    if not normalized.lower().endswith(".blend"):
        raise AuthoringOperationError(
            "LIBRARY_PATH_INVALID", "Library path must end with .blend", kind="validation"
        )
    try:
        stat = os.stat(normalized)
    except FileNotFoundError as exc:
        raise AuthoringOperationError(
            "LIBRARY_NOT_FOUND",
            f"Library file does not exist: {normalized}",
            kind="not_found",
        ) from exc
    except OSError as exc:
        raise AuthoringOperationError(
            "LIBRARY_INSPECTION_FAILED",
            f"Library file could not be inspected: {normalized}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        ) from exc
    if type(expected_size) is not int or expected_size != int(stat.st_size):
        raise AuthoringOperationError(
            "LIBRARY_FILE_CHANGED",
            "Library file size differs from inspected evidence",
            kind="conflict",
            details={"expected_size_bytes": expected_size, "actual_size_bytes": int(stat.st_size)},
        )
    if type(expected_modified) is not int or expected_modified != int(stat.st_mtime_ns):
        raise AuthoringOperationError(
            "LIBRARY_FILE_CHANGED",
            "Library file timestamp differs from inspected evidence",
            kind="conflict",
            details={
                "expected_modified_ns": expected_modified,
                "actual_modified_ns": int(stat.st_mtime_ns),
            },
        )
    return normalized, stat


def _assert_source_stable(path: str, before: os.stat_result) -> None:
    after = os.stat(path)
    if int(after.st_size) != int(before.st_size) or int(after.st_mtime_ns) != int(
        before.st_mtime_ns
    ):
        raise AuthoringOperationError(
            "LIBRARY_FILE_CHANGED",
            "Library file changed while Blender was reading it",
            kind="conflict",
        )


def inspect_library(params: dict[str, Any]) -> dict[str, Any]:
    source = params.get("source")
    path, stat = validate_library_source(source)
    file_sha256 = source.get("expected_file_sha256") if isinstance(source, dict) else None
    if not isinstance(file_sha256, str) or len(file_sha256) != 64:
        raise AuthoringOperationError(
            "LIBRARY_EVIDENCE_INVALID", "expected_file_sha256 is required"
        )
    kinds = params.get("kinds", ["OBJECT", "COLLECTION", "MESH"])
    if (
        not isinstance(kinds, list)
        or not kinds
        or len(kinds) > 3
        or len(set(kinds)) != len(kinds)
        or any(kind not in LIBRARY_KINDS for kind in kinds)
    ):
        raise AuthoringOperationError(
            "LIBRARY_KINDS_INVALID",
            "kinds must contain unique OBJECT, COLLECTION, and/or MESH values",
        )
    name_filter = params.get("name_filter")
    if name_filter is not None and (not isinstance(name_filter, str) or not name_filter):
        raise AuthoringOperationError(
            "LIBRARY_FILTER_INVALID", "name_filter must be a non-empty string or null"
        )
    offset = params.get("offset", 0)
    limit = params.get("limit", 256)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 256:
        raise AuthoringOperationError(
            "LIBRARY_PAGINATION_INVALID",
            "offset must be non-negative and limit must be between 1 and 256",
        )
    entries: list[dict[str, Any]] = []
    try:
        with bpy.data.libraries.load(path, link=False, relative=False) as (data_from, _data_to):
            for kind in kinds:
                for name in getattr(data_from, LIBRARY_KINDS[kind]):
                    if name_filter is not None and name_filter.casefold() not in name.casefold():
                        continue
                    entries.append(
                        {
                            "type": kind,
                            "name": str(name),
                            "entry_identity": library_entry_identity(
                                file_sha256, kind, str(name)
                            ),
                            "supported_root": True,
                        }
                    )
    except AuthoringOperationError:
        raise
    except Exception as exc:  # noqa: BLE001 - Blender library API boundary
        raise AuthoringOperationError(
            "LIBRARY_FORMAT_UNSUPPORTED",
            f"Blender could not read the Library catalog: {path}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        ) from exc
    _assert_source_stable(path, stat)
    entries.sort(key=lambda item: (str(item["type"]), str(item["name"])))
    stop = min(len(entries), offset + limit)
    return {
        "source": {
            "path": path,
            "file_sha256": file_sha256,
            "size_bytes": int(stat.st_size),
            "modified_ns": int(stat.st_mtime_ns),
            "blend_header": params.get("blend_header"),
        },
        "items": entries[offset:stop],
        "pagination": {
            "offset": offset,
            "limit": limit,
            "total": len(entries),
            "returned": max(0, stop - offset),
            "truncated": stop < len(entries),
            "next_offset": stop if stop < len(entries) else None,
        },
    }


def library_entry_names(source: Any, kind: str) -> tuple[str, ...]:
    if kind not in LIBRARY_KINDS:
        raise AuthoringOperationError(
            "LIBRARY_EVIDENCE_INVALID", f"Unsupported Library entry kind: {kind}"
        )
    path, stat = validate_library_source(source)
    try:
        with bpy.data.libraries.load(path, link=False, relative=False) as (data_from, _data_to):
            names = tuple(sorted(str(name) for name in getattr(data_from, LIBRARY_KINDS[kind])))
    except Exception as exc:  # noqa: BLE001 - Blender library API boundary
        raise AuthoringOperationError(
            "LIBRARY_FORMAT_UNSUPPORTED",
            f"Blender could not read the Library catalog: {path}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        ) from exc
    _assert_source_stable(path, stat)
    return names


def _pointer(resource: Any) -> int:
    return int(resource.as_pointer())


def _snapshot_ids() -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for kind, attribute in {**SUPPORTED_NEW_COLLECTIONS, **REJECTED_NEW_COLLECTIONS}.items():
        collection = getattr(bpy.data, attribute, None)
        if collection is not None:
            result[kind] = {_pointer(resource) for resource in collection}
    return result


def _new_ids(before: dict[str, set[int]]) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {}
    for kind, attribute in {**SUPPORTED_NEW_COLLECTIONS, **REJECTED_NEW_COLLECTIONS}.items():
        collection = getattr(bpy.data, attribute, None)
        if collection is None:
            continue
        known = before.get(kind, set())
        created = [resource for resource in collection if _pointer(resource) not in known]
        if created:
            result[kind] = created
    return result


def _all_resources(created: dict[str, list[Any]]) -> list[tuple[str, Any]]:
    return [
        (kind, resource)
        for kind in sorted(created)
        for resource in sorted(created[kind], key=lambda item: str(item.name))
    ]


def _cleanup_created(created: dict[str, list[Any]]) -> None:
    resources = [resource for _kind, resource in _all_resources(created)]
    if not resources:
        return
    if hasattr(bpy.data, "batch_remove"):
        bpy.data.batch_remove(ids=tuple(resources))
        return
    removal_order = (
        "object",
        "collection",
        "mesh",
        "armature",
        "material",
        "node_group",
        "image",
    )
    for kind in removal_order:
        collection_name = SUPPORTED_NEW_COLLECTIONS.get(kind)
        collection = getattr(bpy.data, collection_name, None) if collection_name else None
        if collection is None:
            continue
        for resource in reversed(created.get(kind, [])):
            try:
                collection.remove(resource, do_unlink=True)
            except TypeError:
                collection.remove(resource)


def _has_animation(resource: Any) -> bool:
    animation = getattr(resource, "animation_data", None)
    if animation is None:
        return False
    return bool(
        getattr(animation, "action", None)
        or len(getattr(animation, "drivers", ()))
        or len(getattr(animation, "nla_tracks", ()))
    )


def _validate_static_graph(created: dict[str, list[Any]]) -> None:
    unsupported = {
        kind: [str(resource.name) for resource in resources]
        for kind, resources in created.items()
        if kind in REJECTED_NEW_COLLECTIONS and resources
    }
    if unsupported:
        scripted = any(kind in unsupported for kind in {"action", "text"})
        raise AuthoringOperationError(
            "LIBRARY_SCRIPTED_DATA_UNSUPPORTED" if scripted else "LIBRARY_DEPENDENCY_UNSUPPORTED",
            "Library dependency closure contains unsupported data-block kinds",
            details={"unsupported": unsupported},
        )
    supported = _all_resources(created)
    if len(supported) > MAX_CREATED_IDS:
        raise AuthoringOperationError(
            "LIBRARY_BUDGET_EXCEEDED",
            f"Library append created more than {MAX_CREATED_IDS} data-blocks",
        )
    if len(created.get("object", ())) > MAX_CREATED_OBJECTS or len(
        created.get("collection", ())
    ) > MAX_CREATED_COLLECTIONS:
        raise AuthoringOperationError(
            "LIBRARY_BUDGET_EXCEEDED", "Library append exceeds object or Collection budgets"
        )
    totals = {"vertices": 0, "edges": 0, "faces": 0, "loops": 0}
    for mesh in created.get("mesh", ()):
        for name, count in mesh_counts(mesh).items():
            totals[name] += int(count)
        if getattr(mesh, "shape_keys", None) is not None:
            raise AuthoringOperationError(
                "LIBRARY_DEPENDENCY_UNSUPPORTED",
                f"Shape-Key template Mesh is not appendable in 0.16: {mesh.name}",
            )
    limits = {
        "vertices": MAX_VERTICES,
        "edges": MAX_EDGES,
        "faces": MAX_FACES,
        "loops": MAX_LOOPS,
    }
    if any(totals[name] > limits[name] for name in limits):
        raise AuthoringOperationError(
            "LIBRARY_BUDGET_EXCEEDED",
            "Library Mesh dependency closure exceeds the aggregate geometry budget",
            details={"counts": totals, "limits": limits},
        )
    for kind, resource in supported:
        if getattr(resource, "library", None) is not None or getattr(
            resource, "override_library", None
        ) is not None:
            raise AuthoringOperationError(
                "LIBRARY_DEPENDENCY_UNSUPPORTED",
                f"Nested linked or override data is not supported: {kind} {resource.name}",
            )
        if _has_animation(resource):
            raise AuthoringOperationError(
                "LIBRARY_SCRIPTED_DATA_UNSUPPORTED",
                f"Animated or driven data is not supported: {kind} {resource.name}",
            )
    for obj in created.get("object", ()):
        if str(obj.type) not in SUPPORTED_OBJECT_TYPES:
            raise AuthoringOperationError(
                "LIBRARY_DEPENDENCY_UNSUPPORTED",
                f"Unsupported appended object type {obj.type}: {obj.name}",
            )
        if len(obj.constraints):
            raise AuthoringOperationError(
                "LIBRARY_DEPENDENCY_UNSUPPORTED",
                f"Object constraints are not supported: {obj.name}",
            )
        unsupported_modifiers = [
            modifier.name
            for modifier in obj.modifiers
            if str(modifier.type) not in SUPPORTED_MODIFIER_TYPES
        ]
        if unsupported_modifiers:
            raise AuthoringOperationError(
                "LIBRARY_DEPENDENCY_UNSUPPORTED",
                f"Object has unsupported modifiers: {obj.name}",
                details={"modifiers": unsupported_modifiers},
            )
    for material in created.get("material", ()):
        tree = material.node_tree if bool(material.use_nodes) else None
        if tree is not None and _has_animation(tree):
            raise AuthoringOperationError(
                "LIBRARY_SCRIPTED_DATA_UNSUPPORTED",
                f"Material node tree is animated or driven: {material.name}",
            )


def _destination(output: dict[str, Any]) -> tuple[str, Any]:
    output_type = output.get("type")
    if output_type in {"OBJECT", "MESH"}:
        collection = output.get("collection")
        if not isinstance(collection, dict):
            raise AuthoringOperationError(
                "LIBRARY_OUTPUT_INVALID", "output.collection is required"
            )
        return (
            "collection",
            _require_collection(
                collection.get("collection_name"),
                collection.get("expected_collection_identity"),
                collection.get("expected_collection_structure_fingerprint"),
            ),
        )
    if output_type == "COLLECTION":
        return _require_collection_parent(output.get("parent"))
    raise AuthoringOperationError(
        "LIBRARY_OUTPUT_INVALID", "output.type must be OBJECT, COLLECTION, or MESH"
    )


def _preflight_names(entry_type: str, output: dict[str, Any]) -> None:
    if output.get("type") != entry_type:
        raise AuthoringOperationError(
            "LIBRARY_OUTPUT_INVALID", "entry.type and output.type must match"
        )
    names: list[tuple[str, Any, Any]] = []
    if entry_type == "OBJECT":
        names.append(("Object", bpy.data.objects, output.get("new_object_name")))
    elif entry_type == "COLLECTION":
        names.append(("Collection", bpy.data.collections, output.get("new_collection_name")))
    else:
        names.extend(
            (
                ("Mesh", bpy.data.meshes, output.get("new_mesh_name")),
                ("Object", bpy.data.objects, output.get("new_object_name")),
            )
        )
    for label, collection, name in names:
        if not isinstance(name, str) or not name:
            raise AuthoringOperationError(
                "LIBRARY_OUTPUT_INVALID", f"Exact output {label} name is required"
            )
        if collection.get(name) is not None:
            raise AuthoringOperationError(
                "LIBRARY_NAME_CONFLICT", f"{label} name already exists: {name}", kind="conflict"
            )


def _load_root(path: str, entry_type: str, entry_name: str) -> Any:
    attribute = LIBRARY_KINDS[entry_type]
    try:
        with bpy.data.libraries.load(path, link=False, relative=False) as (data_from, data_to):
            available = tuple(getattr(data_from, attribute))
            if entry_name not in available:
                raise AuthoringOperationError(
                    "LIBRARY_ENTRY_NOT_FOUND",
                    f"Library entry does not exist: {entry_type} {entry_name}",
                    kind="not_found",
                )
            setattr(data_to, attribute, [entry_name])
        loaded = tuple(getattr(data_to, attribute))
    except AuthoringOperationError:
        raise
    except Exception as exc:  # noqa: BLE001 - Blender library API boundary
        raise AuthoringOperationError(
            "LIBRARY_APPEND_FAILED",
            f"Blender failed to append {entry_type} {entry_name}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        ) from exc
    if len(loaded) != 1 or loaded[0] is None:
        raise AuthoringOperationError(
            "LIBRARY_APPEND_FAILED", f"Library entry did not produce a data-block: {entry_name}"
        )
    return loaded[0]


def _place_root(entry_type: str, root: Any, output: dict[str, Any], destination: Any) -> Any:
    if entry_type == "OBJECT":
        root.name = str(output["new_object_name"])
        destination.objects.link(root)
        return root
    if entry_type == "COLLECTION":
        root.name = str(output["new_collection_name"])
        if destination.__class__.__name__ == "Scene":
            destination.collection.children.link(root)
        else:
            destination.children.link(root)
        return root
    root.name = str(output["new_mesh_name"])
    obj = bpy.data.objects.new(str(output["new_object_name"]), root)
    destination.objects.link(obj)
    return obj


def _created_summary(kind: str, resource: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": str(resource.name),
        "session_identity": session_identity(kind, resource),
        "users": int(resource.users),
        "structure_fingerprint": structure_fingerprint(kind, resource),
    }


def append_library(
    transaction: Transaction,
    params: dict[str, Any],
) -> tuple[dict[str, Any], StructuralDelta]:
    transaction.ensure_capacity()
    source = params.get("source")
    path, stat = validate_library_source(source)
    if not isinstance(source, dict):
        raise AuthoringOperationError("LIBRARY_EVIDENCE_INVALID", "source is required")
    file_sha256 = source.get("expected_file_sha256")
    entry = params.get("entry")
    output = params.get("output")
    if not isinstance(entry, dict) or not isinstance(output, dict):
        raise AuthoringOperationError(
            "LIBRARY_EVIDENCE_INVALID", "entry and output must be objects"
        )
    entry_type = entry.get("type")
    entry_name = entry.get("name")
    if entry_type not in LIBRARY_KINDS or not isinstance(entry_name, str) or not entry_name:
        raise AuthoringOperationError(
            "LIBRARY_EVIDENCE_INVALID", "entry must identify an OBJECT, COLLECTION, or MESH"
        )
    expected_identity = entry.get("expected_entry_identity")
    actual_identity = library_entry_identity(str(file_sha256), str(entry_type), entry_name)
    if expected_identity != actual_identity:
        raise AuthoringOperationError(
            "LIBRARY_ENTRY_IDENTITY_MISMATCH",
            f"Library entry identity changed: {entry_type} {entry_name}",
            kind="conflict",
            details={"expected": expected_identity, "actual": actual_identity},
        )
    destination_kind, destination = _destination(output)
    destination_before = make_structure_guard(destination_kind, destination)
    _preflight_names(str(entry_type), output)
    before = _snapshot_ids()
    created: dict[str, list[Any]] = {}
    try:
        root = _load_root(path, str(entry_type), entry_name)
        output_root = _place_root(str(entry_type), root, output, destination)
        _assert_source_stable(path, stat)
        created = _new_ids(before)
        _validate_static_graph(created)
        supported_resources = _all_resources(
            {kind: values for kind, values in created.items() if kind in SUPPORTED_NEW_COLLECTIONS}
        )
        guards = tuple(
            make_structure_guard(kind, resource) for kind, resource in supported_resources
        )
        destination_after = make_structure_guard(destination_kind, destination)
    except Exception as exc:
        created = created or _new_ids(before)
        try:
            _cleanup_created(created)
            if (
                structure_fingerprint(destination_kind, destination)
                != destination_before.fingerprint
            ):
                raise RuntimeError("destination structure did not return to its baseline")
        except Exception as restore_error:
            raise AuthoringOperationError(
                "LIBRARY_APPEND_RESTORE_FAILED",
                "Library append failed and the created dependency closure could not be removed",
                kind="conflict",
                details={
                    "failure_type": type(exc).__name__,
                    "failure": str(exc),
                    "restore_error_type": type(restore_error).__name__,
                    "restore_error": str(restore_error),
                },
            ) from restore_error
        raise
    delta = StructuralDelta(
        kind="library_append",
        action="library_append",
        before=(destination_before,),
        after=(*guards, destination_after),
        payload={
            "resources": tuple(supported_resources),
            "source": {
                "path": path,
                "file_sha256": file_sha256,
                "size_bytes": int(stat.st_size),
            },
        },
    )
    if entry_type == "COLLECTION":
        root_result: dict[str, Any] = {
            "type": "COLLECTION",
            "collection": collection_summary(root),
        }
    elif entry_type == "MESH":
        root_result = {
            "type": "MESH",
            "object": object_summary(output_root),
            "mesh": _created_summary("mesh", root),
        }
    else:
        root_result = {"type": "OBJECT", "object": object_summary(output_root)}
    summaries = [_created_summary(kind, resource) for kind, resource in supported_resources]
    summaries.sort(key=lambda item: (str(item["kind"]), str(item["name"])))
    return (
        {
            "transaction_id": transaction.transaction_id,
            "source": delta.payload["source"],
            "entry": {
                "type": entry_type,
                "name": entry_name,
                "entry_identity": actual_identity,
            },
            "root": root_result,
            "created_ids": summaries,
            "created_count": len(summaries),
            "dependency_counts": {
                kind: len(values)
                for kind, values in sorted(created.items())
                if kind in SUPPORTED_NEW_COLLECTIONS
            },
            "changed": True,
            "delta": {"type": "library_append", "resource_count": len(summaries)},
        },
        delta,
    )


def restore_library_append(delta: StructuralDelta) -> dict[str, Any]:
    resources = tuple(delta.payload.get("resources", ()))
    created = {kind: [] for kind in SUPPORTED_NEW_COLLECTIONS}
    for kind, resource in resources:
        created.setdefault(str(kind), []).append(resource)
    _cleanup_created(created)
    for guard in delta.before:
        try:
            validate_structure_guard(guard)
        except Exception as exc:
            raise AuthoringOperationError(
                "LIBRARY_APPEND_RESTORE_FAILED",
                f"Library destination structure could not be restored: {guard.kind} {guard.name}",
                kind="conflict",
            ) from exc
    return {
        "kind": delta.kind,
        "action": delta.action,
        "removed": [f"{kind}:{resource.name}" for kind, resource in resources],
    }
