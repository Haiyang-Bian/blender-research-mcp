"""Semantic Principled material and local image authoring operations."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import bpy

from .authoring_ops import AuthoringOperationError, object_summary
from .lookdev_ops import session_identity
from .structural_ops import make_structure_guard
from .transaction_model import StructuralDelta, Transaction


def _linear_channel(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def color_value(spec: dict[str, Any]) -> tuple[float, float, float, float]:
    if spec.get("type") == "hex_srgb":
        text = str(spec.get("value", ""))
        if len(text) != 7 or not text.startswith("#"):
            raise AuthoringOperationError(
                "MATERIAL_COLOR_INVALID",
                "Hex colors must use #RRGGBB",
                kind="validation",
            )
        try:
            rgb = [int(text[index : index + 2], 16) / 255.0 for index in (1, 3, 5)]
        except ValueError as exc:
            raise AuthoringOperationError(
                "MATERIAL_COLOR_INVALID",
                "Hex colors must use #RRGGBB",
                kind="validation",
            ) from exc
        return (*(_linear_channel(channel) for channel in rgb), 1.0)
    values = spec.get("value")
    if (
        not isinstance(values, (list, tuple))
        or len(values) != 4
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
            for value in values
        )
    ):
        raise AuthoringOperationError(
            "MATERIAL_COLOR_INVALID",
            "RGBA colors require four finite components between 0 and 1",
            kind="validation",
        )
    return tuple(float(value) for value in values)  # type: ignore[return-value]


def _input(node: Any, *names: str) -> Any:
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    raise AuthoringOperationError(
        "PRINCIPLED_SOCKET_UNAVAILABLE",
        f"Principled input is unavailable: {'/'.join(names)}",
    )


def _material_result(material: Any) -> dict[str, Any]:
    tree = material.node_tree
    principled = next(
        (node for node in tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"),
        None,
    )
    output = next(
        (node for node in tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial"),
        None,
    )
    return {
        "name": material.name,
        "session_identity": session_identity("material", material),
        "users": int(material.users),
        "use_nodes": bool(material.use_nodes),
        "principled": (
            {
                "name": principled.name,
                "session_identity": session_identity("node", principled),
            }
            if principled is not None
            else None
        ),
        "output": (
            {"name": output.name, "session_identity": session_identity("node", output)}
            if output is not None
            else None
        ),
    }


def create_material(
    transaction: Transaction,
    definition: dict[str, Any],
) -> tuple[Any, StructuralDelta]:
    transaction.ensure_capacity()
    name = str(definition.get("name", ""))
    if not name:
        raise AuthoringOperationError(
            "MATERIAL_NAME_INVALID",
            "Material name must be non-empty",
            kind="validation",
        )
    if bpy.data.materials.get(name) is not None:
        raise AuthoringOperationError(
            "MATERIAL_NAME_CONFLICT",
            f"A material already uses the exact name: {name}",
            kind="conflict",
        )
    material = bpy.data.materials.new(name)
    try:
        material.use_nodes = True
        tree = material.node_tree
        tree.nodes.clear()
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.name = "Material Output"
        output.location = (320.0, 0.0)
        principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
        principled.name = "Principled BSDF"
        principled.location = (0.0, 0.0)
        tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
        _input(principled, "Base Color").default_value = color_value(definition["base_color"])
        _input(principled, "Metallic").default_value = float(definition["metallic"])
        _input(principled, "Roughness").default_value = float(definition["roughness"])
        _input(principled, "IOR").default_value = float(definition["ior"])
        _input(principled, "Transmission Weight", "Transmission").default_value = float(
            definition["transmission"]
        )
        _input(principled, "Emission Color", "Emission").default_value = color_value(
            definition["emission_color"]
        )
        _input(principled, "Emission Strength").default_value = float(
            definition["emission_strength"]
        )
        _input(principled, "Alpha").default_value = float(definition["alpha"])
        delta = StructuralDelta(
            kind="material_create",
            action="create_resource",
            before=(),
            after=(make_structure_guard("material", material),),
            payload={
                "resource": material,
                "resource_kind": "material",
                "resource_name": name,
                "owned_resources": (),
            },
        )
        return material, delta
    except Exception:
        bpy.data.materials.remove(material)
        raise


def material_result(material: Any) -> dict[str, Any]:
    return _material_result(material)


def _require_object_data(
    object_name: str,
    expected_object_identity: str,
    expected_data_identity: str,
    expected_data_users: int,
    allow_shared_data: bool,
) -> tuple[Any, Any, str]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise AuthoringOperationError(
            "OBJECT_NOT_FOUND",
            f"Object does not exist: {object_name}",
            kind="not_found",
        )
    if session_identity("object", obj) != expected_object_identity:
        raise AuthoringOperationError(
            "OBJECT_IDENTITY_MISMATCH",
            f"Object identity changed: {object_name}",
            kind="conflict",
        )
    data = obj.data
    if data is None or not hasattr(data, "materials"):
        raise AuthoringOperationError(
            "OBJECT_DATA_UNSUPPORTED",
            f"Object data has no material slots: {object_name}",
        )
    data_kind = data.__class__.__name__.lower()
    if session_identity(data_kind, data) != expected_data_identity:
        raise AuthoringOperationError(
            "OBJECT_DATA_IDENTITY_MISMATCH",
            f"Object data identity changed: {object_name}",
            kind="conflict",
        )
    actual_users = int(data.users)
    if actual_users != expected_data_users:
        raise AuthoringOperationError(
            "SHARED_DATA_USERS_MISMATCH",
            "Object data user count changed after inspection",
            kind="conflict",
            details={"expected": expected_data_users, "actual": actual_users},
        )
    if actual_users > 1 and not allow_shared_data:
        raise AuthoringOperationError(
            "SHARED_DATA_CONFIRMATION_REQUIRED",
            "Material slot changes affect every object sharing this data-block",
            details={"users": actual_users},
        )
    return obj, data, data_kind


def _require_material(name: str, identity: str, users: int | None = None) -> Any:
    material = bpy.data.materials.get(name)
    if material is None:
        raise AuthoringOperationError(
            "MATERIAL_NOT_FOUND",
            f"Material does not exist: {name}",
            kind="not_found",
        )
    if session_identity("material", material) != identity:
        raise AuthoringOperationError(
            "MATERIAL_IDENTITY_MISMATCH",
            f"Material identity changed: {name}",
            kind="conflict",
        )
    if users is not None and int(material.users) != users:
        raise AuthoringOperationError(
            "MATERIAL_USERS_MISMATCH",
            f"Material user count changed: {name}",
            kind="conflict",
            details={"expected": users, "actual": int(material.users)},
        )
    return material


def assign_material(
    transaction: Transaction,
    params: dict[str, Any],
) -> tuple[Any, StructuralDelta, int]:
    transaction.ensure_capacity()
    obj, data, data_kind = _require_object_data(
        str(params["object_name"]),
        str(params["expected_object_identity"]),
        str(params["expected_data_identity"]),
        int(params["expected_data_users"]),
        params.get("allow_shared_data") is True,
    )
    mode = str(params["mode"])
    before = tuple(data.materials)
    slot_index = params.get("slot_index")
    material = None
    if mode in {"append", "replace"}:
        material = _require_material(
            str(params.get("material_name", "")),
            str(params.get("expected_material_identity", "")),
            int(params["expected_material_users"]),
        )
    if mode == "append":
        if slot_index is not None:
            raise AuthoringOperationError(
                "MATERIAL_SLOT_INDEX_INVALID",
                "append does not accept slot_index",
                kind="validation",
            )
        data.materials.append(material)
        changed_index = len(data.materials) - 1
    elif mode in {"replace", "clear"}:
        if isinstance(slot_index, bool) or not isinstance(slot_index, int):
            raise AuthoringOperationError(
                "MATERIAL_SLOT_INDEX_INVALID",
                "replace and clear require an integer slot_index",
                kind="validation",
            )
        if not 0 <= slot_index < len(data.materials):
            raise AuthoringOperationError(
                "MATERIAL_SLOT_NOT_FOUND",
                f"Material slot does not exist: {slot_index}",
                kind="not_found",
            )
        current = data.materials[slot_index]
        expected_current = params.get("expected_slot_material_identity")
        actual_current = (
            session_identity("material", current) if current is not None else None
        )
        if actual_current != expected_current:
            raise AuthoringOperationError(
                "MATERIAL_SLOT_CONFLICT",
                f"Material slot changed before {mode}",
                kind="conflict",
                details={"expected": expected_current, "actual": actual_current},
            )
        data.materials[slot_index] = material if mode == "replace" else None
        changed_index = slot_index
    else:
        raise AuthoringOperationError(
            "MATERIAL_ASSIGN_MODE_INVALID",
            f"Unsupported material assignment mode: {mode}",
            kind="validation",
        )
    guarded_materials = {candidate for candidate in before if candidate is not None}
    if material is not None:
        guarded_materials.add(material)
    delta = StructuralDelta(
        kind="material_assign",
        action="material_slots",
        before=(),
        after=(
            make_structure_guard("object", obj),
            make_structure_guard(data_kind, data),
            *(make_structure_guard("material", item) for item in guarded_materials),
        ),
        payload={"data": data, "before": before},
    )
    return obj, delta, changed_index


def _normalized_image_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise AuthoringOperationError(
            "IMAGE_PATH_INVALID",
            "Image path must be a non-empty absolute path",
            kind="validation",
        )
    path = Path(value)
    if not path.is_absolute():
        raise AuthoringOperationError(
            "IMAGE_PATH_INVALID",
            "Image path must be absolute",
            kind="validation",
        )
    if not path.is_file():
        raise AuthoringOperationError(
            "IMAGE_NOT_FOUND",
            f"Image file does not exist: {path}",
            kind="not_found",
        )
    return path.resolve()


def image_summary(image: Any) -> dict[str, Any]:
    return {
        "name": image.name,
        "session_identity": session_identity("image", image),
        "filepath": bpy.path.abspath(image.filepath),
        "size": [int(value) for value in image.size],
        "channels": int(image.channels),
        "colorspace": image.colorspace_settings.name,
        "users": int(image.users),
        "packed": image.packed_file is not None,
        "source": image.source,
    }


def inspect_image(image_name: str) -> dict[str, Any]:
    image = bpy.data.images.get(image_name)
    if image is None:
        raise AuthoringOperationError(
            "IMAGE_NOT_FOUND",
            f"Image does not exist: {image_name}",
            kind="not_found",
        )
    return image_summary(image)


def load_image(
    transaction: Transaction,
    path_value: Any,
    colorspace: str,
) -> tuple[Any, StructuralDelta | None, bool]:
    transaction.ensure_capacity()
    path = _normalized_image_path(path_value)
    normalized = os.path.normcase(str(path))
    existing = next(
        (
            image
            for image in bpy.data.images
            if os.path.normcase(str(Path(bpy.path.abspath(image.filepath)).resolve()))
            == normalized
        ),
        None,
    )
    image = (
        existing
        if existing is not None
        else bpy.data.images.load(str(path), check_existing=True)
    )
    reused = existing is not None
    requested = {"SRGB": "sRGB", "NON_COLOR": "Non-Color"}.get(colorspace)
    before_colorspace = image.colorspace_settings.name
    if requested is not None:
        try:
            image.colorspace_settings.name = requested
        except TypeError as exc:
            if not reused:
                bpy.data.images.remove(image)
            raise AuthoringOperationError(
                "IMAGE_COLORSPACE_INVALID",
                f"Blender does not support requested color space: {requested}",
                kind="validation",
            ) from exc
    if reused and image.colorspace_settings.name == before_colorspace:
        return image, None, True
    if reused:
        delta = StructuralDelta(
            kind="image_colorspace",
            action="image_colorspace",
            before=(),
            after=(make_structure_guard("image", image),),
            payload={"image": image, "before": before_colorspace},
        )
    else:
        delta = StructuralDelta(
            kind="image_load",
            action="create_resource",
            before=(),
            after=(make_structure_guard("image", image),),
            payload={
                "resource": image,
                "resource_kind": "image",
                "resource_name": image.name,
                "owned_resources": (),
            },
        )
    return image, delta, reused


_CHANNEL_INPUTS = {
    "base_color": ("Base Color",),
    "roughness": ("Roughness",),
    "metallic": ("Metallic",),
    "normal": ("Normal",),
    "bump": ("Normal",),
    "emission": ("Emission Color", "Emission"),
    "alpha": ("Alpha",),
}


def link_identity(link: Any) -> str:
    return session_identity("link", link)


def _require_principled(
    material: Any,
    node_name: str,
    expected_node_identity: str,
) -> Any:
    tree = material.node_tree if material.use_nodes else None
    node = tree.nodes.get(node_name) if tree is not None else None
    if node is None or node.bl_idname != "ShaderNodeBsdfPrincipled":
        raise AuthoringOperationError(
            "PRINCIPLED_NODE_NOT_FOUND",
            f"Principled node does not exist: {node_name}",
            kind="not_found",
        )
    if session_identity("node", node) != expected_node_identity:
        raise AuthoringOperationError(
            "NODE_IDENTITY_MISMATCH",
            f"Node identity changed: {node_name}",
            kind="conflict",
        )
    return node


def _require_image(name: str, identity: str, users: int) -> Any:
    image = bpy.data.images.get(name)
    if image is None:
        raise AuthoringOperationError(
            "IMAGE_NOT_FOUND",
            f"Image does not exist: {name}",
            kind="not_found",
        )
    if session_identity("image", image) != identity or int(image.users) != users:
        raise AuthoringOperationError(
            "IMAGE_IDENTITY_CONFLICT",
            f"Image identity or user count changed: {name}",
            kind="conflict",
        )
    return image


def _stored_link(link: Any) -> tuple[Any, Any]:
    return (link.from_socket, link.to_socket)


def _set_mapping(mapping_node: Any, mapping: dict[str, Any]) -> None:
    axes = ("x", "y", "z")
    mapping_node.inputs["Location"].default_value = tuple(
        float(mapping["location"][axis]) for axis in axes
    )
    mapping_node.inputs["Rotation"].default_value = tuple(
        math.radians(float(mapping["rotation_euler_degrees"][axis])) for axis in axes
    )
    mapping_node.inputs["Scale"].default_value = tuple(
        float(mapping["scale"][axis]) for axis in axes
    )


def bind_texture(
    transaction: Transaction,
    params: dict[str, Any],
) -> tuple[Any, StructuralDelta, dict[str, Any]]:
    transaction.ensure_capacity()
    material = _require_material(
        str(params["material_name"]),
        str(params["expected_material_identity"]),
        int(params["expected_material_users"]),
    )
    if int(material.users) > 1 and params.get("allow_shared") is not True:
        raise AuthoringOperationError(
            "SHARED_MATERIAL_CONFIRMATION_REQUIRED",
            "Texture binding affects every material user",
            details={"users": int(material.users)},
        )
    image = _require_image(
        str(params["image_name"]),
        str(params["expected_image_identity"]),
        int(params["expected_image_users"]),
    )
    node = _require_principled(
        material,
        str(params["node_name"]),
        str(params["expected_node_identity"]),
    )
    channel = str(params["channel"])
    names = _CHANNEL_INPUTS.get(channel)
    if names is None:
        raise AuthoringOperationError(
            "MATERIAL_CHANNEL_INVALID",
            f"Unsupported semantic material channel: {channel}",
            kind="validation",
        )
    destination = _input(node, *names)
    tree = material.node_tree
    existing = list(destination.links)
    actual_link_ids = sorted(link_identity(link) for link in existing)
    expected_link_ids = sorted(params.get("expected_link_identities") or [])
    replace_existing = params.get("replace_existing") is True
    if existing and not replace_existing:
        raise AuthoringOperationError(
            "MATERIAL_LINK_CONFLICT",
            f"Semantic channel already has an incoming link: {channel}",
            kind="conflict",
            details={"link_identities": actual_link_ids},
        )
    if expected_link_ids != actual_link_ids:
        raise AuthoringOperationError(
            "MATERIAL_LINK_IDENTITY_MISMATCH",
            f"Semantic channel links changed: {channel}",
            kind="conflict",
            details={"expected": expected_link_ids, "actual": actual_link_ids},
        )
    replaced = [_stored_link(link) for link in existing]
    for link in existing:
        tree.links.remove(link)
    created_nodes = []
    try:
        coordinate = tree.nodes.new("ShaderNodeTexCoord")
        mapping = tree.nodes.new("ShaderNodeMapping")
        texture = tree.nodes.new("ShaderNodeTexImage")
        created_nodes.extend([coordinate, mapping, texture])
        binding_id = f"{transaction.transaction_id}:{len(transaction.deltas)}:{channel}"
        for created in created_nodes:
            created["blender_research_mcp_binding"] = binding_id
            created["blender_research_mcp_channel"] = channel
        texture.image = image
        _set_mapping(mapping, params["mapping"])
        coordinate_output = "UV" if params["coordinates"] == "UV" else "Generated"
        tree.links.new(coordinate.outputs[coordinate_output], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
        source = texture.outputs["Color"]
        if channel == "normal":
            helper = tree.nodes.new("ShaderNodeNormalMap")
            helper["blender_research_mcp_binding"] = binding_id
            helper["blender_research_mcp_channel"] = channel
            created_nodes.append(helper)
            tree.links.new(source, helper.inputs["Color"])
            source = helper.outputs["Normal"]
        elif channel == "bump":
            helper = tree.nodes.new("ShaderNodeBump")
            helper["blender_research_mcp_binding"] = binding_id
            helper["blender_research_mcp_channel"] = channel
            created_nodes.append(helper)
            tree.links.new(source, helper.inputs["Height"])
            source = helper.outputs["Normal"]
        destination_link = tree.links.new(source, destination)
    except Exception:
        for created in reversed(created_nodes):
            tree.nodes.remove(created)
        for from_socket, to_socket in replaced:
            tree.links.new(from_socket, to_socket)
        raise
    delta = StructuralDelta(
        kind="material_texture_bind",
        action="node_graph",
        before=(),
        after=(
            make_structure_guard("material", material),
            make_structure_guard("image", image),
        ),
        payload={
            "material": material,
            "created_nodes": tuple(created_nodes),
            "replaced_links": tuple(replaced),
        },
    )
    return material, delta, {
        "channel": channel,
        "destination_socket": {
            "name": destination.name,
            "session_identity": session_identity("socket", destination),
        },
        "link_identity": link_identity(destination_link),
        "created_nodes": [
            {"name": item.name, "session_identity": session_identity("node", item)}
            for item in created_nodes
        ],
        "replaced_link_identities": actual_link_ids,
    }


def clear_texture(
    transaction: Transaction,
    params: dict[str, Any],
) -> tuple[Any, StructuralDelta, list[str]]:
    transaction.ensure_capacity()
    material = _require_material(
        str(params["material_name"]),
        str(params["expected_material_identity"]),
        int(params["expected_material_users"]),
    )
    if int(material.users) > 1 and params.get("allow_shared") is not True:
        raise AuthoringOperationError(
            "SHARED_MATERIAL_CONFIRMATION_REQUIRED",
            "Texture clearing affects every material user",
            details={"users": int(material.users)},
        )
    node = _require_principled(
        material,
        str(params["node_name"]),
        str(params["expected_node_identity"]),
    )
    channel = str(params["channel"])
    names = _CHANNEL_INPUTS.get(channel)
    if names is None:
        raise AuthoringOperationError(
            "MATERIAL_CHANNEL_INVALID",
            f"Unsupported semantic material channel: {channel}",
            kind="validation",
        )
    destination = _input(node, *names)
    links = list(destination.links)
    actual = sorted(link_identity(link) for link in links)
    expected = sorted(params.get("expected_link_identities") or [])
    if not links or actual != expected:
        raise AuthoringOperationError(
            "MATERIAL_LINK_IDENTITY_MISMATCH",
            f"Semantic channel links changed: {channel}",
            kind="conflict",
            details={"expected": expected, "actual": actual},
        )
    tree = material.node_tree
    removed = tuple(_stored_link(link) for link in links)
    tagged_nodes = []
    for link in links:
        source = link.from_node
        binding_id = source.get("blender_research_mcp_binding")
        if binding_id:
            for candidate in tree.nodes:
                if (
                    candidate.get("blender_research_mcp_binding") == binding_id
                    and candidate not in tagged_nodes
                ):
                    tagged_nodes.append(candidate)
        tree.links.remove(link)
    delta = StructuralDelta(
        kind="material_texture_clear",
        action="node_graph_clear",
        before=(),
        after=(make_structure_guard("material", material),),
        payload={
            "material": material,
            "removed_links": removed,
            "tagged_nodes": tuple(tagged_nodes),
        },
    )
    return material, delta, actual


def assignment_result(obj: Any, slot_index: int) -> dict[str, Any]:
    result = object_summary(obj)
    result["slot_index"] = slot_index
    result["material_slots"] = [
        (
            {
                "name": material.name,
                "session_identity": session_identity("material", material),
            }
            if material is not None
            else None
        )
        for material in obj.data.materials
    ]
    return result
