"""Exact Collection links and reversible object parenting."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import bpy

from .authoring_ops import AuthoringOperationError, object_summary
from .lookdev_ops import session_identity
from .structural_ops import (
    make_structure_guard,
    refresh_structure_guard_if_present,
    structure_fingerprint,
)
from .transaction_model import StructuralDelta, Transaction


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def object_collection_fingerprint(obj: Any) -> str:
    return _digest(
        sorted(
            (collection.name, session_identity("collection", collection))
            for collection in obj.users_collection
        )
    )


def _collection_parents(collection: Any) -> tuple[list[Any], list[Any]]:
    parents = [
        parent for parent in bpy.data.collections if collection.name in parent.children
    ]
    scenes = [
        scene
        for scene in bpy.data.scenes
        if scene.collection is collection or collection.name in scene.collection.children
    ]
    return parents, scenes


def collection_summary(collection: Any) -> dict[str, Any]:
    parents, scenes = _collection_parents(collection)
    return {
        "name": collection.name,
        "session_identity": session_identity("collection", collection),
        "users": int(collection.users),
        "linked": collection.library is not None,
        "structure_fingerprint": structure_fingerprint("collection", collection),
        "parents": [
            {
                "type": "COLLECTION",
                "name": parent.name,
                "session_identity": session_identity("collection", parent),
            }
            for parent in sorted(parents, key=lambda item: item.name)
        ]
        + [
            {
                "type": "SCENE_ROOT",
                "name": scene.name,
                "session_identity": session_identity("scene", scene),
            }
            for scene in sorted(scenes, key=lambda item: item.name)
        ],
        "children": [
            {
                "name": child.name,
                "session_identity": session_identity("collection", child),
            }
            for child in sorted(collection.children, key=lambda item: item.name)
        ],
        "direct_object_count": len(collection.objects),
        "direct_objects": [
            {
                "name": obj.name,
                "session_identity": session_identity("object", obj),
            }
            for obj in sorted(collection.objects, key=lambda item: item.name)
        ],
    }


def inspect_collection(name: str, offset: int, limit: int) -> dict[str, Any]:
    collection = bpy.data.collections.get(name)
    if collection is None:
        raise AuthoringOperationError(
            "COLLECTION_NOT_FOUND", f"Collection does not exist: {name}", kind="not_found"
        )
    if offset < 0 or not 1 <= limit <= 256:
        raise AuthoringOperationError(
            "COLLECTION_PAGINATION_INVALID",
            "offset must be non-negative and limit must be between 1 and 256",
        )
    result = collection_summary(collection)
    direct_objects = result.pop("direct_objects")
    stop = min(len(direct_objects), offset + limit)
    result["items"] = direct_objects[offset:stop]
    result["pagination"] = {
        "offset": offset,
        "limit": limit,
        "total": len(direct_objects),
        "returned": max(0, stop - offset),
        "truncated": stop < len(direct_objects),
        "next_offset": stop if stop < len(direct_objects) else None,
    }
    return result


def _require_writable(kind: str, resource: Any) -> None:
    if resource.library is not None and resource.override_library is None:
        raise AuthoringOperationError(
            f"{kind.upper()}_LINKED",
            f"Linked {kind} cannot be modified: {resource.name}",
        )


def _require_collection(
    name: Any,
    expected_identity: Any,
    expected_fingerprint: Any,
) -> Any:
    if not all(isinstance(value, str) and value for value in (name, expected_identity)):
        raise AuthoringOperationError(
            "COLLECTION_EVIDENCE_INVALID", "Collection name and identity are required"
        )
    collection = bpy.data.collections.get(name)
    if collection is None:
        raise AuthoringOperationError(
            "COLLECTION_NOT_FOUND", f"Collection does not exist: {name}", kind="not_found"
        )
    actual = {
        "identity": session_identity("collection", collection),
        "structure_fingerprint": structure_fingerprint("collection", collection),
    }
    expected = {
        "identity": expected_identity,
        "structure_fingerprint": expected_fingerprint,
    }
    if actual != expected:
        raise AuthoringOperationError(
            "COLLECTION_STRUCTURE_MISMATCH",
            f"Collection identity or structure changed: {name}",
            kind="conflict",
            details={"expected": expected, "actual": actual},
        )
    _require_writable("collection", collection)
    return collection


def _require_object(
    name: Any,
    expected_identity: Any,
    *,
    expected_collections_fingerprint: Any | None = None,
    expected_structure_fingerprint: Any | None = None,
) -> Any:
    if not all(isinstance(value, str) and value for value in (name, expected_identity)):
        raise AuthoringOperationError(
            "OBJECT_EVIDENCE_INVALID", "Object name and identity are required"
        )
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise AuthoringOperationError(
            "OBJECT_NOT_FOUND", f"Object does not exist: {name}", kind="not_found"
        )
    if session_identity("object", obj) != expected_identity:
        raise AuthoringOperationError(
            "OBJECT_IDENTITY_MISMATCH", f"Object identity changed: {name}", kind="conflict"
        )
    if (
        expected_collections_fingerprint is not None
        and object_collection_fingerprint(obj) != expected_collections_fingerprint
    ):
        raise AuthoringOperationError(
            "COLLECTION_OBJECT_LINK_MISMATCH",
            f"Object Collection links changed: {name}",
            kind="conflict",
        )
    if (
        expected_structure_fingerprint is not None
        and structure_fingerprint("object", obj) != expected_structure_fingerprint
    ):
        raise AuthoringOperationError(
            "OBJECT_PARENT_STATE_MISMATCH",
            f"Object parent or structure changed: {name}",
            kind="conflict",
        )
    _require_writable("object", obj)
    return obj


def _require_collection_parent(parent: Any) -> tuple[str, Any]:
    if not isinstance(parent, dict):
        raise AuthoringOperationError(
            "COLLECTION_PARENT_INVALID", "parent must be a typed object"
        )
    parent_type = parent.get("type")
    if parent_type == "COLLECTION":
        return (
            "collection",
            _require_collection(
                parent.get("collection_name"),
                parent.get("expected_collection_identity"),
                parent.get("expected_collection_structure_fingerprint"),
            ),
        )
    if parent_type == "SCENE_ROOT":
        name = parent.get("scene_name")
        scene = bpy.data.scenes.get(name) if isinstance(name, str) else None
        if scene is None:
            raise AuthoringOperationError(
                "SCENE_NOT_FOUND", f"Scene does not exist: {name}", kind="not_found"
            )
        actual = {
            "identity": session_identity("scene", scene),
            "structure_fingerprint": structure_fingerprint("scene", scene),
        }
        expected = {
            "identity": parent.get("expected_scene_identity"),
            "structure_fingerprint": parent.get("expected_scene_structure_fingerprint"),
        }
        if actual != expected:
            raise AuthoringOperationError(
                "COLLECTION_PARENT_MISMATCH",
                f"Scene root identity or structure changed: {name}",
                kind="conflict",
                details={"expected": expected, "actual": actual},
            )
        return "scene", scene
    raise AuthoringOperationError(
        "COLLECTION_PARENT_INVALID", "parent.type must be SCENE_ROOT or COLLECTION"
    )


def create_collection(
    transaction: Transaction, params: dict[str, Any]
) -> tuple[Any, StructuralDelta]:
    transaction.ensure_capacity()
    name = params.get("name")
    if not isinstance(name, str) or not name or len(name) > 255:
        raise AuthoringOperationError(
            "COLLECTION_NAME_INVALID", "name must contain between 1 and 255 characters"
        )
    if bpy.data.collections.get(name) is not None:
        raise AuthoringOperationError(
            "COLLECTION_NAME_CONFLICT",
            f"A Collection already uses the exact name: {name}",
            kind="conflict",
        )
    parent_kind, parent = _require_collection_parent(params.get("parent"))
    children = parent.collection.children if parent_kind == "scene" else parent.children
    collection = bpy.data.collections.new(name)
    try:
        children.link(collection)
        refresh_structure_guard_if_present(transaction, parent_kind, parent)
        delta = StructuralDelta(
            kind="collection_create",
            action="create_resource",
            before=(),
            after=(
                make_structure_guard("collection", collection),
                make_structure_guard(parent_kind, parent),
            ),
            payload={
                "resource": collection,
                "resource_kind": "collection",
                "resource_name": name,
                "owned_resources": (),
            },
        )
        return collection, delta
    except Exception:
        if bpy.data.collections.get(name) is collection:
            bpy.data.collections.remove(collection)
        raise


def _link_evidence(params: dict[str, Any]) -> tuple[Any, Any]:
    collection = _require_collection(
        params.get("collection_name"),
        params.get("expected_collection_identity"),
        params.get("expected_collection_structure_fingerprint"),
    )
    obj = _require_object(
        params.get("object_name"),
        params.get("expected_object_identity"),
        expected_collections_fingerprint=params.get(
            "expected_object_collections_fingerprint"
        ),
    )
    return collection, obj


def change_collection_link(
    transaction: Transaction,
    params: dict[str, Any],
    *,
    link: bool,
) -> tuple[bool, StructuralDelta | None, Any, Any]:
    transaction.ensure_capacity()
    collection, obj = _link_evidence(params)
    is_linked = obj.name in collection.objects
    if is_linked == link:
        return False, None, collection, obj
    if not link and len(obj.users_collection) <= 1:
        raise AuthoringOperationError(
            "COLLECTION_LAST_OBJECT_LINK",
            "Cannot unlink an object's final Collection link; use object.delete instead",
        )
    action = "collection_link_object" if link else "collection_unlink_object"
    try:
        if link:
            collection.objects.link(obj)
        else:
            collection.objects.unlink(obj)
        refresh_structure_guard_if_present(transaction, "collection", collection)
        refresh_structure_guard_if_present(transaction, "object", obj)
        delta = StructuralDelta(
            kind="collection_link",
            action=action,
            before=(),
            after=(
                make_structure_guard("collection", collection),
                make_structure_guard("object", obj),
            ),
            payload={"collection": collection, "object": obj},
        )
        return True, delta, collection, obj
    except Exception as exc:
        try:
            if link and obj.name in collection.objects:
                collection.objects.unlink(obj)
            elif not link and obj.name not in collection.objects:
                collection.objects.link(obj)
        except Exception as restore_error:
            raise AuthoringOperationError(
                "COLLECTION_LINK_RESTORE_FAILED",
                "Collection link update failed and could not be restored",
                kind="conflict",
                details={"failure": str(exc), "restore_error": str(restore_error)},
            ) from restore_error
        raise


def _parent_chain_contains(parent: Any, child: Any) -> bool:
    current = parent
    visited: set[int] = set()
    while current is not None:
        pointer = int(current.as_pointer())
        if pointer in visited:
            return True
        if current is child:
            return True
        visited.add(pointer)
        current = current.parent
    return False


def _parent_state(obj: Any) -> dict[str, Any]:
    return {
        "parent": obj.parent,
        "parent_type": str(obj.parent_type),
        "parent_bone": str(obj.parent_bone),
        "parent_inverse": obj.matrix_parent_inverse.copy(),
        "matrix_world": obj.matrix_world.copy(),
        "matrix_basis": obj.matrix_basis.copy(),
    }


def _apply_parent(obj: Any, parent: Any | None, transform_mode: str) -> None:
    if transform_mode == "KEEP_WORLD":
        world = obj.matrix_world.copy()
        obj.parent = parent
        obj.parent_type = "OBJECT"
        obj.parent_bone = ""
        if parent is None:
            obj.matrix_parent_inverse.identity()
        else:
            obj.matrix_parent_inverse = parent.matrix_world.inverted_safe()
        obj.matrix_world = world
        return
    if transform_mode == "KEEP_LOCAL":
        basis = obj.matrix_basis.copy()
        obj.parent = parent
        obj.parent_type = "OBJECT"
        obj.parent_bone = ""
        obj.matrix_parent_inverse.identity()
        obj.matrix_basis = basis
        return
    raise AuthoringOperationError(
        "OBJECT_PARENT_TRANSFORM_MODE_INVALID",
        "transform_mode must be KEEP_WORLD or KEEP_LOCAL",
    )


def _restore_parent(obj: Any, state: dict[str, Any]) -> None:
    obj.parent = state["parent"]
    obj.parent_type = state["parent_type"]
    obj.parent_bone = state["parent_bone"]
    obj.matrix_parent_inverse = state["parent_inverse"]
    obj.matrix_world = state["matrix_world"]
    if state["parent"] is not None:
        obj.matrix_basis = state["matrix_basis"]


def change_object_parent(
    transaction: Transaction,
    params: dict[str, Any],
    *,
    clear: bool,
) -> tuple[bool, StructuralDelta | None, Any]:
    transaction.ensure_capacity()
    child = _require_object(
        params.get("child_name"),
        params.get("expected_child_identity"),
        expected_structure_fingerprint=params.get("expected_child_structure_fingerprint"),
    )
    transform_mode = params.get("transform_mode")
    if not isinstance(transform_mode, str):
        raise AuthoringOperationError(
            "OBJECT_PARENT_TRANSFORM_MODE_INVALID", "transform_mode is required"
        )
    if clear:
        expected_parent_name = params.get("expected_parent_name")
        expected_parent_identity = params.get("expected_parent_identity")
        protected_parent = _require_object(
            expected_parent_name,
            expected_parent_identity,
            expected_structure_fingerprint=params.get(
                "expected_parent_structure_fingerprint"
            ),
        )
        if child.parent is None:
            raise AuthoringOperationError(
                "OBJECT_PARENT_STATE_MISMATCH", "Child object has no parent", kind="conflict"
            )
        if (
            child.parent.name != expected_parent_name
            or session_identity("object", child.parent) != expected_parent_identity
        ):
            raise AuthoringOperationError(
                "OBJECT_PARENT_STATE_MISMATCH",
                "Existing parent identity changed",
                kind="conflict",
            )
        parent = None
        if child.parent is not protected_parent:
            raise AuthoringOperationError(
                "OBJECT_PARENT_STATE_MISMATCH",
                "Existing parent reference changed",
                kind="conflict",
            )
    else:
        parent = _require_object(
            params.get("parent_name"),
            params.get("expected_parent_identity"),
            expected_structure_fingerprint=params.get("expected_parent_structure_fingerprint"),
        )
        if _parent_chain_contains(parent, child):
            raise AuthoringOperationError(
                "OBJECT_PARENT_CYCLE", "Object parenting would create a cycle"
            )
        if child.parent is parent and str(child.parent_type) == "OBJECT":
            return False, None, child
        protected_parent = parent
    before = _parent_state(child)
    try:
        _apply_parent(child, parent, transform_mode)
        refresh_structure_guard_if_present(transaction, "object", child)
        after_guards = [make_structure_guard("object", child)]
        if protected_parent is not child:
            after_guards.append(make_structure_guard("object", protected_parent))
        delta = StructuralDelta(
            kind="object_parent",
            action="object_parent",
            before=(),
            after=tuple(after_guards),
            payload={"object": child, "before": before},
        )
        return True, delta, child
    except Exception as exc:
        try:
            _restore_parent(child, before)
        except Exception as restore_error:
            raise AuthoringOperationError(
                "OBJECT_PARENT_RESTORE_FAILED",
                "Object parenting failed and could not be restored",
                kind="conflict",
                details={"failure": str(exc), "restore_error": str(restore_error)},
            ) from restore_error
        raise


def restore_scene_organization_delta(delta: StructuralDelta) -> dict[str, Any]:
    if delta.action in {"collection_link_object", "collection_unlink_object"}:
        collection = delta.payload["collection"]
        obj = delta.payload["object"]
        if delta.action == "collection_link_object":
            if obj.name in collection.objects:
                collection.objects.unlink(obj)
        elif obj.name not in collection.objects:
            collection.objects.link(obj)
        return {"kind": delta.kind, "action": delta.action, "restored": True}
    if delta.action == "object_parent":
        _restore_parent(delta.payload["object"], delta.payload["before"])
        return {"kind": delta.kind, "action": delta.action, "restored": True}
    raise AuthoringOperationError(
        "SCENE_ORGANIZATION_DELTA_INVALID",
        f"Unsupported scene organization delta: {delta.action}",
    )


def organization_result(
    transaction: Transaction,
    *,
    changed: bool,
    collection: Any | None = None,
    obj: Any | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "transaction_id": transaction.transaction_id,
        "changed": changed,
        "delta": {
            "types": ["scene_organization"] if changed else [],
            "recorded": changed,
        },
    }
    if collection is not None:
        result["collection"] = collection_summary(collection)
    if obj is not None:
        result["object"] = object_summary(obj)
        result["object"]["collections_fingerprint"] = object_collection_fingerprint(obj)
        result["object"]["structure_fingerprint"] = structure_fingerprint("object", obj)
    return result


__all__ = [
    "change_collection_link",
    "change_object_parent",
    "collection_summary",
    "create_collection",
    "inspect_collection",
    "object_collection_fingerprint",
    "organization_result",
    "restore_scene_organization_delta",
]
