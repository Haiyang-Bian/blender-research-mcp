"""Semantic World, active-camera, and reviewed Eevee render operations."""

from __future__ import annotations

import base64
import hashlib
import math
import os
import tempfile
import time
from array import array
from pathlib import Path
from typing import Any

import bpy

from .authoring_ops import AuthoringOperationError, object_summary
from .lookdev_ops import session_identity
from .material_authoring_ops import color_value, image_summary
from .structural_ops import make_structure_guard
from .transaction_model import StructuralDelta, Transaction


def _require_camera(name: str, expected_identity: str) -> Any:
    camera = bpy.data.objects.get(name)
    if camera is None:
        raise AuthoringOperationError(
            "CAMERA_NOT_FOUND",
            f"Camera object does not exist: {name}",
            kind="not_found",
        )
    if camera.type != "CAMERA":
        raise AuthoringOperationError(
            "CAMERA_INVALID",
            f"Object is not a Camera: {name}",
            kind="validation",
        )
    if session_identity("object", camera) != expected_identity:
        raise AuthoringOperationError(
            "CAMERA_IDENTITY_MISMATCH",
            f"Camera identity changed: {name}",
            kind="conflict",
        )
    return camera


def set_scene_camera(
    transaction: Transaction,
    camera_name: str,
    expected_camera_identity: str,
) -> tuple[Any, StructuralDelta]:
    transaction.ensure_capacity()
    camera = _require_camera(camera_name, expected_camera_identity)
    scene = bpy.context.scene
    before = scene.camera
    scene.camera = camera
    delta = StructuralDelta(
        kind="scene_camera",
        action="scene_camera",
        before=(),
        after=(
            make_structure_guard("scene", scene),
            make_structure_guard("object", camera),
        ),
        payload={"scene": scene, "before": before},
    )
    return camera, delta


def _require_world(
    expected_identity: str | None,
    expected_users: int | None,
    allow_shared: bool,
) -> tuple[Any, bool]:
    scene = bpy.context.scene
    world = scene.world
    if world is None:
        if expected_identity is not None or expected_users is not None:
            raise AuthoringOperationError(
                "WORLD_IDENTITY_MISMATCH",
                "The scene no longer has the inspected World",
                kind="conflict",
            )
        world = bpy.data.worlds.new("World")
        scene.world = world
        return world, True
    if not expected_identity or expected_users is None:
        raise AuthoringOperationError(
            "WORLD_IDENTITY_REQUIRED",
            "Existing World changes require exact identity and user count",
            kind="validation",
        )
    if session_identity("world", world) != expected_identity:
        raise AuthoringOperationError(
            "WORLD_IDENTITY_MISMATCH",
            "The active World identity changed",
            kind="conflict",
        )
    actual_users = int(world.users)
    if actual_users != expected_users:
        raise AuthoringOperationError(
            "WORLD_USERS_MISMATCH",
            "The active World user count changed",
            kind="conflict",
            details={"expected": expected_users, "actual": actual_users},
        )
    if actual_users > 1 and not allow_shared:
        raise AuthoringOperationError(
            "SHARED_WORLD_CONFIRMATION_REQUIRED",
            "World changes affect every scene sharing this World",
            details={"users": actual_users},
        )
    return world, False


def _world_nodes(
    world: Any,
    created_nodes: list[Any],
    before_nodes: tuple[Any, ...],
) -> tuple[Any, Any, tuple[tuple[Any, Any], ...]]:
    world.use_nodes = True
    tree = world.node_tree
    for node in tree.nodes:
        if node not in before_nodes and node not in created_nodes:
            created_nodes.append(node)
    output = next(
        (node for node in tree.nodes if node.bl_idname == "ShaderNodeOutputWorld"),
        None,
    )
    if output is None:
        output = tree.nodes.new("ShaderNodeOutputWorld")
        created_nodes.append(output)
    background = next(
        (node for node in tree.nodes if node.bl_idname == "ShaderNodeBackground"),
        None,
    )
    if background is None:
        background = tree.nodes.new("ShaderNodeBackground")
        created_nodes.append(background)
    surface_links = list(output.inputs["Surface"].links)
    before_surface_links = tuple(
        (link.from_socket, link.to_socket) for link in surface_links
    )
    background_identity = session_identity("node", background)
    if surface_links and any(
        session_identity("node", link.from_node) != background_identity
        for link in surface_links
    ):
        raise AuthoringOperationError(
            "WORLD_LINK_CONFLICT",
            "World Output Surface is controlled by an unsupported node graph",
            kind="conflict",
            details={
                "link_identities": [session_identity("link", link) for link in surface_links]
            },
        )
    if not surface_links:
        tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    return background, output, before_surface_links


def _world_image(params: dict[str, Any]) -> Any | None:
    name = params.get("environment_image_name")
    if name is None:
        return None
    image = bpy.data.images.get(str(name))
    if image is None:
        raise AuthoringOperationError(
            "IMAGE_NOT_FOUND",
            f"Environment image does not exist: {name}",
            kind="not_found",
        )
    if (
        session_identity("image", image) != params.get("expected_environment_image_identity")
        or int(image.users) != params.get("expected_environment_image_users")
    ):
        raise AuthoringOperationError(
            "IMAGE_IDENTITY_CONFLICT",
            f"Environment image identity or user count changed: {name}",
            kind="conflict",
        )
    return image


def set_world(
    transaction: Transaction,
    params: dict[str, Any],
) -> tuple[Any, StructuralDelta, dict[str, Any]]:
    transaction.ensure_capacity()
    if all(
        params.get(name) is None
        for name in ("color", "strength", "environment_image_name", "rotation_z_degrees")
    ):
        raise AuthoringOperationError(
            "WORLD_PATCH_INVALID",
            "World color, strength, environment image, and/or rotation is required",
            kind="validation",
        )
    if (
        params.get("rotation_z_degrees") is not None
        and params.get("environment_image_name") is None
    ):
        raise AuthoringOperationError(
            "WORLD_ROTATION_INVALID",
            "rotation_z_degrees requires environment_image_name",
            kind="validation",
        )
    scene = bpy.context.scene
    before_world = scene.world
    world, created_world = _require_world(
        params.get("expected_world_identity"),
        params.get("expected_world_users"),
        params.get("allow_shared") is True,
    )
    created_nodes: list[Any] = []
    replaced_links: list[tuple[Any, Any]] = []
    background = None
    output = None
    before_surface_links: tuple[tuple[Any, Any], ...] = ()
    before_color = None
    before_strength = None
    image = None
    before_use_nodes = bool(world.use_nodes)
    before_nodes = tuple(world.node_tree.nodes) if world.node_tree is not None else ()
    try:
        background, output, before_surface_links = _world_nodes(
            world,
            created_nodes,
            before_nodes,
        )
        before_color = tuple(float(value) for value in background.inputs["Color"].default_value)
        before_strength = float(background.inputs["Strength"].default_value)
        if params.get("color") is not None:
            background.inputs["Color"].default_value = color_value(params["color"])
        if params.get("strength") is not None:
            strength = params["strength"]
            if (
                isinstance(strength, bool)
                or not isinstance(strength, (int, float))
                or not math.isfinite(float(strength))
                or not 0 <= float(strength) <= 1_000_000
            ):
                raise AuthoringOperationError(
                    "WORLD_STRENGTH_INVALID",
                    "World strength must be finite and between 0 and 1000000",
                    kind="validation",
                )
            background.inputs["Strength"].default_value = float(strength)
        image = _world_image(params)
        if image is not None:
            tree = world.node_tree
            existing = list(background.inputs["Color"].links)
            if existing and any(
                not link.from_node.get("blender_research_mcp_world_environment")
                for link in existing
            ):
                raise AuthoringOperationError(
                    "WORLD_LINK_CONFLICT",
                    "World Background Color is controlled by an unsupported node graph",
                    kind="conflict",
                    details={
                        "link_identities": [
                            session_identity("link", link) for link in existing
                        ]
                    },
                )
            replaced_links.extend((link.from_socket, link.to_socket) for link in existing)
            for link in existing:
                tree.links.remove(link)
            coordinate = tree.nodes.new("ShaderNodeTexCoord")
            mapping = tree.nodes.new("ShaderNodeMapping")
            environment = tree.nodes.new("ShaderNodeTexEnvironment")
            created_nodes.extend([coordinate, mapping, environment])
            for node in (coordinate, mapping, environment):
                node["blender_research_mcp_world_environment"] = True
            environment.image = image
            rotation = float(params.get("rotation_z_degrees") or 0.0)
            mapping.inputs["Rotation"].default_value[2] = math.radians(rotation)
            tree.links.new(coordinate.outputs["Generated"], mapping.inputs["Vector"])
            tree.links.new(mapping.outputs["Vector"], environment.inputs["Vector"])
            tree.links.new(environment.outputs["Color"], background.inputs["Color"])
        guards = [make_structure_guard("scene", scene), make_structure_guard("world", world)]
        if image is not None:
            guards.append(make_structure_guard("image", image))
        delta = StructuralDelta(
            kind="world_set",
            action="world_state",
            before=(),
            after=tuple(guards),
            payload={
                "scene": scene,
                "world": world,
                "before_world": before_world,
                "before_use_nodes": before_use_nodes,
                "created_world": created_world,
                "created_nodes": tuple(created_nodes),
                "replaced_links": tuple(replaced_links),
                "background": background,
                "output": output,
                "before_surface_links": before_surface_links,
                "before_color": before_color,
                "before_strength": before_strength,
            },
        )
        return world, delta, {
            "world_name": world.name,
            "world_identity": session_identity("world", world),
            "world_users": int(world.users),
            "created": created_world,
            "background_node_identity": session_identity("node", background),
            "environment_image": image_summary(image) if image is not None else None,
        }
    except Exception:
        tree = world.node_tree if world.use_nodes else None
        if background is not None and before_color is not None:
            background.inputs["Color"].default_value = before_color
            background.inputs["Strength"].default_value = before_strength
        if tree is not None:
            if output is not None:
                for link in list(output.inputs["Surface"].links):
                    tree.links.remove(link)
                for from_socket, to_socket in before_surface_links:
                    tree.links.new(from_socket, to_socket)
            for node in reversed(created_nodes):
                if tree.nodes.get(node.name) is node:
                    tree.nodes.remove(node)
            for from_socket, to_socket in replaced_links:
                tree.links.new(from_socket, to_socket)
        if created_world:
            scene.world = before_world
            if int(world.users) == 0:
                bpy.data.worlds.remove(world)
        else:
            world.use_nodes = before_use_nodes
        raise


def _render_snapshot(scene: Any) -> dict[str, Any]:
    render = scene.render
    return {
        "camera": scene.camera,
        "engine": render.engine,
        "resolution_x": int(render.resolution_x),
        "resolution_y": int(render.resolution_y),
        "resolution_percentage": int(render.resolution_percentage),
        "film_transparent": bool(render.film_transparent),
        "filepath": str(render.filepath),
        "file_format": render.image_settings.file_format,
        "color_mode": render.image_settings.color_mode,
        "color_depth": render.image_settings.color_depth,
        "samples": int(scene.eevee.taa_render_samples),
    }


def _restore_render_snapshot(scene: Any, snapshot: dict[str, Any]) -> None:
    render = scene.render
    scene.camera = snapshot["camera"]
    render.engine = snapshot["engine"]
    render.resolution_x = snapshot["resolution_x"]
    render.resolution_y = snapshot["resolution_y"]
    render.resolution_percentage = snapshot["resolution_percentage"]
    render.film_transparent = snapshot["film_transparent"]
    render.filepath = snapshot["filepath"]
    render.image_settings.file_format = snapshot["file_format"]
    render.image_settings.color_mode = snapshot["color_mode"]
    render.image_settings.color_depth = snapshot["color_depth"]
    scene.eevee.taa_render_samples = snapshot["samples"]


def _configure_render(
    scene: Any,
    camera: Any,
    width: int,
    height: int,
    samples: int,
    transparent: bool,
) -> None:
    scene.camera = camera
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = transparent
    scene.eevee.taa_render_samples = samples


def _validate_render_values(width: Any, height: Any, samples: Any, transparent: Any) -> None:
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
        or not 256 <= width <= 1000
        or not 256 <= height <= 1000
    ):
        raise AuthoringOperationError(
            "RENDER_SIZE_INVALID",
            "Render width and height must be integers between 256 and 1000",
            kind="validation",
        )
    if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 64:
        raise AuthoringOperationError(
            "RENDER_SAMPLES_INVALID",
            "Eevee samples must be an integer between 1 and 64",
            kind="validation",
        )
    if type(transparent) is not bool:
        raise AuthoringOperationError(
            "RENDER_TRANSPARENCY_INVALID",
            "transparent must be a boolean",
            kind="validation",
        )


def _image_is_blank(image: Any) -> bool:
    pixels = array("f", [0.0]) * (int(image.size[0]) * int(image.size[1]) * 4)
    image.pixels.foreach_get(pixels)
    minimum = 1.0
    maximum = 0.0
    for index in range(0, len(pixels), 4):
        gray = (float(pixels[index]) + float(pixels[index + 1]) + float(pixels[index + 2])) / 3
        minimum = min(minimum, gray)
        maximum = max(maximum, gray)
        if maximum - minimum > 1e-6:
            return False
    return True


def _perform_render(
    camera_name: str,
    expected_camera_identity: str,
    width: Any,
    height: Any,
    samples: Any,
    transparent: Any,
    output_path: Path,
    file_format: str,
) -> tuple[dict[str, Any], float]:
    _validate_render_values(width, height, samples, transparent)
    camera = _require_camera(camera_name, expected_camera_identity)
    scene = bpy.context.scene
    snapshot = _render_snapshot(scene)
    started = time.perf_counter()
    try:
        _configure_render(scene, camera, width, height, samples, transparent)
        scene.render.filepath = str(output_path)
        scene.render.image_settings.file_format = file_format
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "16" if file_format == "OPEN_EXR" else "8"
        outcome = bpy.ops.render.render(write_still=True)
        if "FINISHED" not in outcome:
            raise AuthoringOperationError(
                "RENDER_FAILED",
                f"Blender render operator returned: {sorted(outcome)}",
                kind="blender_api",
            )
        if not output_path.is_file():
            raise AuthoringOperationError(
                "RENDER_RESULT_INVALID",
                "Blender did not write the temporary render result",
                kind="blender_api",
            )
        image = None
        try:
            image = bpy.data.images.load(str(output_path), check_existing=False)
            actual_size = [int(image.size[0]), int(image.size[1])]
            if actual_size != [width, height]:
                raise AuthoringOperationError(
                    "RENDER_RESULT_INVALID",
                    f"Rendered image size {actual_size} does not match {[width, height]}",
                    kind="blender_api",
                    details={"expected_size": [width, height], "actual_size": actual_size},
                )
            if _image_is_blank(image):
                raise AuthoringOperationError(
                    "RENDER_BLANK",
                    "Rendered image has no grayscale variation",
                    kind="blender_api",
                )
        finally:
            if image is not None:
                bpy.data.images.remove(image)
        return snapshot, (time.perf_counter() - started) * 1000
    except Exception:
        _restore_render_snapshot(scene, snapshot)
        raise


def render_preview(params: dict[str, Any]) -> dict[str, Any]:
    scene = bpy.context.scene
    temporary_path: Path | None = None
    snapshot: dict[str, Any] | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporary:
            temporary_path = Path(temporary.name)
        snapshot, duration_ms = _perform_render(
            str(params["camera_name"]),
            str(params["expected_camera_identity"]),
            params["width"],
            params["height"],
            params["samples"],
            params["transparent"],
            temporary_path,
            "PNG",
        )
        data = temporary_path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AuthoringOperationError(
                "RENDER_RESULT_INVALID",
                "Render Result did not encode as a valid PNG",
                kind="blender_api",
            )
        return {
            "camera": object_summary(scene.camera),
            "width": int(params["width"]),
            "height": int(params["height"]),
            "samples": int(params["samples"]),
            "transparent": bool(params["transparent"]),
            "engine": "BLENDER_EEVEE_NEXT",
            "mime_type": "image/png",
            "sha256": hashlib.sha256(data).hexdigest(),
            "byte_count": len(data),
            "duration_ms": duration_ms,
            "png_base64": base64.b64encode(data).decode("ascii"),
            "settings_restored": True,
        }
    finally:
        if snapshot is not None:
            _restore_render_snapshot(scene, snapshot)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _render_output_path(value: Any) -> tuple[Path, str]:
    if not isinstance(value, str) or not value:
        raise AuthoringOperationError(
            "RENDER_OUTPUT_PATH_INVALID",
            "Render output path must be a non-empty absolute path",
            kind="validation",
        )
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() not in {".png", ".exr"}:
        raise AuthoringOperationError(
            "RENDER_OUTPUT_PATH_INVALID",
            "Render output path must be an absolute .png or .exr path",
            kind="validation",
        )
    if not path.parent.is_dir():
        raise AuthoringOperationError(
            "RENDER_OUTPUT_PARENT_NOT_FOUND",
            f"Render output parent does not exist: {path.parent}",
            kind="not_found",
        )
    return path.resolve(), "PNG" if path.suffix.lower() == ".png" else "OPEN_EXR"


def render_save(params: dict[str, Any]) -> dict[str, Any]:
    output_path, file_format = _render_output_path(params.get("path"))
    scene = bpy.context.scene
    temporary_path: Path | None = None
    snapshot: dict[str, Any] | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=output_path.suffix,
            dir=output_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        snapshot, duration_ms = _perform_render(
            str(params["camera_name"]),
            str(params["expected_camera_identity"]),
            params["width"],
            params["height"],
            params["samples"],
            params["transparent"],
            temporary_path,
            file_format,
        )
        data = temporary_path.read_bytes()
        try:
            os.replace(temporary_path, output_path)
        except OSError as exc:
            raise AuthoringOperationError(
                "RENDER_SAVE_FAILED",
                f"Could not replace render output: {output_path}",
                kind="filesystem",
            ) from exc
        temporary_path = None
        return {
            "camera_name": params["camera_name"],
            "path": str(output_path),
            "format": file_format,
            "width": int(params["width"]),
            "height": int(params["height"]),
            "samples": int(params["samples"]),
            "transparent": bool(params["transparent"]),
            "engine": "BLENDER_EEVEE_NEXT",
            "sha256": hashlib.sha256(data).hexdigest(),
            "byte_count": len(data),
            "duration_ms": duration_ms,
            "settings_restored": True,
        }
    finally:
        if snapshot is not None:
            _restore_render_snapshot(scene, snapshot)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
