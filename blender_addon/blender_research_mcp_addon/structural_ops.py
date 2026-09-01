"""Structural transaction guards, rollback, and commit finalization."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import bpy

from .lookdev_ops import session_identity
from .transaction_model import StructuralDelta, StructureGuard, Transaction, TransactionModelError

_DATA_COLLECTIONS = {
    "armature": "armatures",
    "camera": "cameras",
    "collection": "collections",
    "image": "images",
    "light": "lights",
    "material": "materials",
    "mesh": "meshes",
    "node_group": "node_groups",
    "object": "objects",
    "scene": "scenes",
    "world": "worlds",
}


def _rounded(values: Any) -> list[float]:
    return [round(float(value), 9) for value in values]


def _socket_value(socket: Any) -> Any:
    if not hasattr(socket, "default_value"):
        return None
    value = socket.default_value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 9)
    try:
        return _rounded(value)
    except (TypeError, ValueError):
        return None


def _node_tree_summary(node_tree: Any) -> dict[str, Any] | None:
    if node_tree is None:
        return None
    nodes = [
        {
            "name": node.name,
            "type": node.bl_idname,
            "identity": session_identity("node", node),
            "image": (
                session_identity("image", node.image)
                if hasattr(node, "image") and node.image is not None
                else None
            ),
            "inputs": [
                {
                    "identifier": socket.identifier,
                    "identity": session_identity("socket", socket),
                    "value": _socket_value(socket),
                }
                for socket in node.inputs
            ],
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


def _vertex_group_schema(resource: Any) -> list[dict[str, Any]]:
    return [
        {
            "name": str(group.name),
            "identity": session_identity("vertex_group", group),
            "index": int(group.index),
            "lock_weight": bool(group.lock_weight),
        }
        for group in getattr(resource, "vertex_groups", ())
    ]


def _parent_inverse(resource: Any) -> list[float]:
    matrix = getattr(resource, "matrix_parent_inverse", None)
    if matrix is None:
        return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    return [round(float(value), 9) for row in matrix for value in row]


def _exact_mesh_fingerprint(resource: Any) -> str | None:
    try:
        from .mesh_ops import mesh_fingerprint
    except ModuleNotFoundError as exc:
        if exc.name not in {"bmesh", "mathutils"}:
            raise
        return None
    return mesh_fingerprint(resource)


def structure_summary(kind: str, resource: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "kind": kind,
        "name": resource.name,
        "identity": session_identity(kind, resource),
        "users": int(resource.users),
    }
    if kind == "object":
        from .modifier_ops import modifier_stack_summary

        parent = getattr(resource, "parent", None)

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
                "modifiers": modifier_stack_summary(resource),
                "parent": (
                    session_identity("object", parent)
                    if parent is not None
                    else None
                ),
                "parent_type": str(getattr(resource, "parent_type", "OBJECT")),
                "parent_bone": str(getattr(resource, "parent_bone", "")),
                "matrix_parent_inverse": _parent_inverse(resource),
                "vertex_groups": _vertex_group_schema(resource),
            }
        )
        if str(getattr(resource, "type", "")) == "MESH" and getattr(
            resource, "data", None
        ) is not None:
            from .mesh_weight_ops import weights_fingerprint

            summary["weights_fingerprint"] = weights_fingerprint(resource.data)
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
                "mesh_fingerprint": _exact_mesh_fingerprint(resource),
            }
        )
    elif kind in {"material", "world"}:
        summary["use_nodes"] = bool(resource.use_nodes)
        summary["node_tree"] = _node_tree_summary(resource.node_tree)
    elif kind == "node_group":
        summary["node_tree"] = _node_tree_summary(resource)
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
        parent_collections = [
            parent
            for parent in bpy.data.collections
            if resource.name in parent.children
        ]
        parent_scenes = [
            scene
            for scene in bpy.data.scenes
            if scene.collection is resource or resource.name in scene.collection.children
        ]
        summary.update(
            {
                "objects": sorted(session_identity("object", obj) for obj in resource.objects),
                "children": sorted(
                    session_identity("collection", child) for child in resource.children
                ),
                "parent_collections": sorted(
                    session_identity("collection", parent) for parent in parent_collections
                ),
                "parent_scenes": sorted(
                    session_identity("scene", scene) for scene in parent_scenes
                ),
            }
        )
    elif kind == "camera":
        summary.update({"type": resource.type, "lens": round(float(resource.lens), 9)})
    elif kind == "scene":
        summary.update(
            {
                "camera": (
                    session_identity("object", resource.camera)
                    if resource.camera is not None
                    else None
                ),
                "world": (
                    session_identity("world", resource.world)
                    if resource.world is not None
                    else None
                ),
                "root_collection": session_identity("collection", resource.collection),
                "root_children": sorted(
                    session_identity("collection", child)
                    for child in resource.collection.children
                ),
            }
        )
    elif kind == "light":
        summary.update(
            {
                "type": resource.type,
                "energy": round(float(resource.energy), 9),
                "color": _rounded(resource.color),
            }
        )
    elif kind == "armature":
        summary.update(
            {
                "bones": [
                    {
                        "name": bone.name,
                        "identity": session_identity("bone", bone),
                        "parent": (
                            session_identity("bone", bone.parent)
                            if bone.parent is not None
                            else None
                        ),
                        "use_deform": bool(bone.use_deform),
                        "head": _rounded(bone.head_local),
                        "tail": _rounded(bone.tail_local),
                        "roll": round(float(bone.matrix_local.to_euler().z), 9),
                    }
                    for bone in resource.bones
                ]
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


def refresh_structure_guard_if_present(
    transaction: Transaction,
    kind: str,
    resource: Any,
) -> None:
    identity = session_identity(kind, resource)
    if (kind, str(resource.name), identity) in transaction.expected_structures():
        refresh_structure_guard(transaction, kind, resource)


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
    if delta.action == "library_append":
        from .library_ops import restore_library_append

        return restore_library_append(delta)
    if delta.action == "rig_binding":
        from .rig_ops import restore_rig_binding

        return restore_rig_binding(delta)
    if delta.action in {
        "collection_link_object",
        "collection_unlink_object",
        "object_parent",
    }:
        from .scene_organization_ops import restore_scene_organization_delta

        return restore_scene_organization_delta(delta)
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
    if delta.action == "image_colorspace":
        image = delta.payload["image"]
        image.colorspace_settings.name = delta.payload["before"]
        return {
            "kind": delta.kind,
            "action": delta.action,
            "colorspace": image.colorspace_settings.name,
        }
    if delta.action == "node_graph":
        material = delta.payload["material"]
        tree = material.node_tree
        for node in reversed(delta.payload["created_nodes"]):
            if tree.nodes.get(node.name) is node:
                tree.nodes.remove(node)
        for from_socket, to_socket in delta.payload["replaced_links"]:
            tree.links.new(from_socket, to_socket)
        return {"kind": delta.kind, "action": delta.action, "restored": True}
    if delta.action == "node_graph_clear":
        material = delta.payload["material"]
        tree = material.node_tree
        for from_socket, to_socket in delta.payload["removed_links"]:
            tree.links.new(from_socket, to_socket)
        return {"kind": delta.kind, "action": delta.action, "restored": True}
    if delta.action == "scene_camera":
        scene = delta.payload["scene"]
        scene.camera = delta.payload["before"]
        return {"kind": delta.kind, "action": delta.action, "restored": True}
    if delta.action == "world_assignment":
        scene = delta.payload["scene"]
        scene.world = delta.payload["before"]
        return {"kind": delta.kind, "action": delta.action, "restored": True}
    if delta.action == "world_state":
        scene = delta.payload["scene"]
        world = delta.payload["world"]
        if delta.payload["created_world"]:
            scene.world = delta.payload["before_world"]
            if int(world.users) == 0:
                bpy.data.worlds.remove(world)
            return {"kind": delta.kind, "action": delta.action, "restored": True}
        tree = world.node_tree if world.use_nodes else None
        background = delta.payload["background"]
        output = delta.payload["output"]
        background.inputs["Color"].default_value = delta.payload["before_color"]
        background.inputs["Strength"].default_value = delta.payload["before_strength"]
        if tree is not None:
            for link in list(output.inputs["Surface"].links):
                tree.links.remove(link)
            for from_socket, to_socket in delta.payload["before_surface_links"]:
                tree.links.new(from_socket, to_socket)
            for node in reversed(delta.payload["created_nodes"]):
                if tree.nodes.get(node.name) is node:
                    tree.nodes.remove(node)
            for from_socket, to_socket in delta.payload["replaced_links"]:
                tree.links.new(from_socket, to_socket)
        world.use_nodes = delta.payload["before_use_nodes"]
        return {"kind": delta.kind, "action": delta.action, "restored": True}
    raise TransactionModelError(
        "STRUCTURE_DELTA_INVALID",
        f"Unsupported structural rollback action: {delta.action}",
    )


def finalize_structural_delta(delta: StructuralDelta) -> dict[str, Any] | None:
    """Complete deferred destructive work after every transaction guard is valid."""

    if delta.action == "node_graph_clear":
        material = delta.payload["material"]
        tree = material.node_tree
        removed = []
        for node in reversed(delta.payload["tagged_nodes"]):
            if tree.nodes.get(node.name) is node:
                removed.append(node.name)
                tree.nodes.remove(node)
        return {"kind": delta.kind, "action": delta.action, "removed_nodes": removed}
    if delta.action != "unlink_object":
        return None
    obj = delta.payload["object"]
    name = str(obj.name)
    bpy.data.objects.remove(obj)
    removed_owned = []
    if delta.payload.get("created_in_transaction"):
        for owned_kind, owned_resource in reversed(delta.payload.get("owned_resources", ())):
            if int(owned_resource.users) == 0:
                owned_name = str(owned_resource.name)
                _remove_data_block(str(owned_kind), owned_resource)
                removed_owned.append(f"{owned_kind}:{owned_name}")
    return {
        "kind": delta.kind,
        "action": "delete_object",
        "object_name": name,
        "removed_owned": removed_owned,
    }


def adopt_structural_delta_for_native_save(
    delta: StructuralDelta,
) -> dict[str, Any] | None:
    """Finalize deferred work only while its last Agent-owned guard still matches."""

    if delta.action not in {"node_graph_clear", "unlink_object"}:
        return None
    try:
        for guard in delta.after:
            validate_structure_guard(guard)
    except TransactionModelError:
        return {
            "kind": delta.kind,
            "action": "preserved_user_state",
            "deferred_action": delta.action,
        }
    result = finalize_structural_delta(delta)
    if result is not None:
        result["native_save"] = True
    return result
