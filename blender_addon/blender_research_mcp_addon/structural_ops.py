"""Structural transaction guards, rollback, and commit finalization."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import bpy

from .lookdev_ops import session_identity
from .transaction_model import StructuralDelta, StructureGuard, Transaction, TransactionModelError

_DATA_COLLECTIONS = {
    "camera": "cameras",
    "collection": "collections",
    "image": "images",
    "light": "lights",
    "material": "materials",
    "mesh": "meshes",
    "object": "objects",
    "world": "worlds",
}


def _rounded(values: Any) -> list[float]:
    return [round(float(value), 9) for value in values]


def _node_tree_summary(node_tree: Any) -> dict[str, Any] | None:
    if node_tree is None:
        return None
    nodes = [
        {
            "name": node.name,
            "type": node.bl_idname,
            "identity": session_identity("node", node),
        }
        for node in node_tree.nodes
    ]
    links = [
        {
            "from_node": session_identity("node", link.from_node),
            "from_socket": session_identity("socket", link.from_socket),
            "to_node": session_identity("node", link.to_node),
            "to_socket": session_identity("socket", link.to_socket),
        }
        for link in node_tree.links
    ]
    return {
        "nodes": sorted(nodes, key=lambda item: (item["name"], item["identity"])),
        "links": sorted(
            links,
            key=lambda item: (
                item["from_node"],
                item["from_socket"],
                item["to_node"],
                item["to_socket"],
            ),
        ),
    }


def structure_summary(kind: str, resource: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "kind": kind,
        "name": resource.name,
        "identity": session_identity(kind, resource),
        "users": int(resource.users),
    }
    if kind == "object":
        summary.update(
            {
                "type": resource.type,
                "data": (
                    session_identity(resource.data.__class__.__name__.lower(), resource.data)
                    if resource.data is not None
                    else None
                ),
                "collections": sorted(
                    session_identity("collection", collection)
                    for collection in resource.users_collection
                ),
                "location": _rounded(resource.location),
                "rotation_euler": _rounded(resource.rotation_euler),
                "scale": _rounded(resource.scale),
                "materials": [
                    session_identity("material", material) if material is not None else None
                    for material in getattr(resource.data, "materials", ())
                ],
            }
        )
    elif kind == "mesh":
        summary.update(
            {
                "vertices": len(resource.vertices),
                "edges": len(resource.edges),
                "polygons": len(resource.polygons),
                "materials": [
                    session_identity("material", material) if material is not None else None
                    for material in resource.materials
                ],
            }
        )
    elif kind in {"material", "world"}:
        summary["use_nodes"] = bool(resource.use_nodes)
        summary["node_tree"] = _node_tree_summary(resource.node_tree)
    elif kind == "image":
        summary.update(
            {
                "filepath": bpy.path.abspath(resource.filepath),
                "size": [int(value) for value in resource.size],
                "channels": int(resource.channels),
                "colorspace": resource.colorspace_settings.name,
                "packed": resource.packed_file is not None,
            }
        )
    elif kind == "collection":
        summary.update(
            {
                "objects": sorted(session_identity("object", obj) for obj in resource.objects),
                "children": sorted(
                    session_identity("collection", child) for child in resource.children
                ),
            }
        )
    elif kind == "camera":
        summary.update({"type": resource.type, "lens": round(float(resource.lens), 9)})
    elif kind == "light":
        summary.update(
            {
                "type": resource.type,
                "energy": round(float(resource.energy), 9),
                "color": _rounded(resource.color),
            }
        )
    return summary


def structure_fingerprint(kind: str, resource: Any) -> str:
    encoded = json.dumps(
        structure_summary(kind, resource),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def make_structure_guard(kind: str, resource: Any) -> StructureGuard:
    return StructureGuard(
        kind=kind,
        name=str(resource.name),
        identity=session_identity(kind, resource),
        fingerprint=structure_fingerprint(kind, resource),
        users=int(resource.users),
    )


def resolve_structure_guard(guard: StructureGuard) -> Any:
    collection_name = _DATA_COLLECTIONS.get(guard.kind)
    if collection_name is None:
        raise TransactionModelError(
            "STRUCTURE_GUARD_INVALID",
            f"Unsupported structural resource kind: {guard.kind}",
        )
    resource = getattr(bpy.data, collection_name).get(guard.name)
    if resource is None:
        raise TransactionModelError(
            "STRUCTURE_CONFLICT",
            f"Structural resource no longer exists: {guard.kind} {guard.name}",
        )
    if session_identity(guard.kind, resource) != guard.identity:
        raise TransactionModelError(
            "STRUCTURE_CONFLICT",
            f"Structural resource identity changed: {guard.kind} {guard.name}",
        )
    return resource


def validate_structure_guard(guard: StructureGuard) -> Any:
    resource = resolve_structure_guard(guard)
    if guard.users is not None and int(resource.users) != guard.users:
        raise TransactionModelError(
            "STRUCTURE_CONFLICT",
            f"Structural resource users changed: {guard.kind} {guard.name}",
        )
    if structure_fingerprint(guard.kind, resource) != guard.fingerprint:
        raise TransactionModelError(
            "STRUCTURE_CONFLICT",
            f"Structural resource changed outside the transaction: {guard.kind} {guard.name}",
        )
    return resource


def validate_structural_transaction(transaction: Transaction) -> None:
    for guard in transaction.expected_structures().values():
        validate_structure_guard(guard)


def refresh_structure_guard(transaction: Transaction, kind: str, resource: Any) -> None:
    transaction.refresh_structure_guard(make_structure_guard(kind, resource))


def _remove_data_block(kind: str, resource: Any) -> None:
    collection_name = _DATA_COLLECTIONS[kind]
    getattr(bpy.data, collection_name).remove(resource)


def restore_structural_delta(delta: StructuralDelta) -> dict[str, Any]:
    """Undo one validated structural delta.  Callers iterate in reverse order."""

    if delta.action == "create_resource":
        resource = delta.payload["resource"]
        kind = str(delta.payload["resource_kind"])
        owned = list(delta.payload.get("owned_resources", ()))
        _remove_data_block(kind, resource)
        removed = [f"{kind}:{delta.payload.get('resource_name', '')}"]
        for owned_kind, owned_resource in reversed(owned):
            if int(owned_resource.users) == 0:
                owned_name = str(owned_resource.name)
                _remove_data_block(str(owned_kind), owned_resource)
                removed.append(f"{owned_kind}:{owned_name}")
        return {"kind": delta.kind, "action": delta.action, "removed": removed}
    if delta.action == "unlink_object":
        obj = delta.payload["object"]
        linked = []
        for collection in delta.payload["collections"]:
            if obj.name not in collection.objects:
                collection.objects.link(obj)
            linked.append(collection.name)
        return {
            "kind": delta.kind,
            "action": delta.action,
            "object_name": obj.name,
            "collections": linked,
        }
    if delta.action == "material_slots":
        data = delta.payload["data"]
        data.materials.clear()
        for material in delta.payload["before"]:
            data.materials.append(material)
        return {"kind": delta.kind, "action": delta.action, "restored": True}
    if delta.action == "scene_camera":
        scene = delta.payload["scene"]
        scene.camera = delta.payload["before"]
        return {"kind": delta.kind, "action": delta.action, "restored": True}
    if delta.action == "world_assignment":
        scene = delta.payload["scene"]
        scene.world = delta.payload["before"]
        return {"kind": delta.kind, "action": delta.action, "restored": True}
    raise TransactionModelError(
        "STRUCTURE_DELTA_INVALID",
        f"Unsupported structural rollback action: {delta.action}",
    )


def finalize_structural_delta(delta: StructuralDelta) -> dict[str, Any] | None:
    """Complete deferred destructive work after every transaction guard is valid."""

    if delta.action != "unlink_object":
        return None
    obj = delta.payload["object"]
    name = str(obj.name)
    bpy.data.objects.remove(obj)
    return {"kind": delta.kind, "action": "delete_object", "object_name": name}
