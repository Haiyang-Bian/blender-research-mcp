"""Context-safe Blender observation operations."""

from __future__ import annotations

import base64
import hashlib
import struct
from dataclasses import dataclass
from typing import Any

import bpy
import gpu
from mathutils import Vector

from .capture_codec import (
    bounded_dimensions,
    encode_rgba_png,
    flatten_rgba_buffer,
    is_blank_rgba,
)


class ContextOperationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: str = "precondition",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.kind = kind
        self.retryable = retryable
        self.details = details or {}


@dataclass
class ViewportContext:
    window: Any
    area: Any
    region: Any
    space: Any

    @property
    def viewport_id(self) -> str:
        return f"{self.window.as_pointer():x}:{self.area.as_pointer():x}"


def list_viewports() -> list[ViewportContext]:
    viewports: list[ViewportContext] = []
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is None:
                continue
            viewports.append(ViewportContext(window, area, region, area.spaces.active))
    return viewports


def resolve_viewport(viewport_id: str | None = None) -> ViewportContext:
    viewports = list_viewports()
    if not viewports:
        raise ContextOperationError("VIEWPORT_NOT_FOUND", "No VIEW_3D area is available")
    if viewport_id is not None:
        for viewport in viewports:
            if viewport.viewport_id == viewport_id:
                return viewport
        raise ContextOperationError(
            "VIEWPORT_STALE",
            "The requested viewport no longer exists in the current layout",
        )
    current_window = bpy.context.window
    candidates = [item for item in viewports if item.window == current_window] or viewports
    return max(candidates, key=lambda item: item.area.width * item.area.height)


def capture_context(viewport_id: str | None = None) -> dict[str, Any]:
    viewport = resolve_viewport(viewport_id)
    region_3d = viewport.space.region_3d
    scene = viewport.window.scene
    view_layer = viewport.window.view_layer
    active = view_layer.objects.active
    return {
        "scene": scene.name,
        "view_layer": view_layer.name,
        "workspace": viewport.window.workspace.name,
        "window_id": viewport.window.as_pointer(),
        "area_id": viewport.area.as_pointer(),
        "region_id": viewport.region.as_pointer(),
        "viewport_id": viewport.viewport_id,
        "mode": bpy.context.mode,
        "active_object": active.name if active else None,
        "selected_objects": sorted(obj.name for obj in bpy.context.selected_objects),
        "frame_current": scene.frame_current,
        "active_camera": scene.camera.name if scene.camera else None,
        "view": {
            "location": list(region_3d.view_location),
            "rotation": list(region_3d.view_rotation),
            "distance": region_3d.view_distance,
            "perspective": region_3d.view_perspective,
            "lens": viewport.space.lens,
            "shading": viewport.space.shading.type,
            "show_overlays": viewport.space.overlay.show_overlays,
        },
    }


def _find_viewport(snapshot: dict[str, Any]) -> ViewportContext:
    for viewport in list_viewports():
        if (
            viewport.window.as_pointer() == snapshot["window_id"]
            and viewport.area.as_pointer() == snapshot["area_id"]
            and viewport.region.as_pointer() == snapshot["region_id"]
        ):
            return viewport
    raise ContextOperationError(
        "VIEWPORT_STALE",
        "The snapshot viewport no longer exists in the current layout",
    )


def _ensure_object_mode(viewport: ViewportContext) -> None:
    if bpy.context.mode == "OBJECT":
        return
    with bpy.context.temp_override(
        window=viewport.window,
        area=viewport.area,
        region=viewport.region,
    ):
        result = bpy.ops.object.mode_set(mode="OBJECT")
    if "FINISHED" not in result:
        raise ContextOperationError("MODE_CHANGE_FAILED", "Could not enter Object mode")


def _restore_mode(viewport: ViewportContext, mode: str) -> None:
    operator_mode = {
        "EDIT_MESH": "EDIT",
        "EDIT_CURVE": "EDIT",
        "EDIT_ARMATURE": "EDIT",
        "POSE": "POSE",
        "SCULPT": "SCULPT",
        "PAINT_WEIGHT": "WEIGHT_PAINT",
        "PAINT_VERTEX": "VERTEX_PAINT",
        "PAINT_TEXTURE": "TEXTURE_PAINT",
    }.get(mode)
    if operator_mode is None:
        return
    with bpy.context.temp_override(
        window=viewport.window,
        area=viewport.area,
        region=viewport.region,
    ):
        result = bpy.ops.object.mode_set(mode=operator_mode)
    if "FINISHED" not in result:
        raise ContextOperationError("MODE_RESTORE_FAILED", f"Could not restore mode {mode}")


def restore_context(snapshot: dict[str, Any]) -> None:
    validate_context_snapshot(snapshot)
    viewport = _find_viewport(snapshot)
    scene = bpy.data.scenes.get(snapshot["scene"])
    if scene is None:
        raise ContextOperationError("SCENE_NOT_FOUND", "The snapshot scene no longer exists")
    view_layer = scene.view_layers.get(snapshot["view_layer"])
    if view_layer is None:
        raise ContextOperationError(
            "VIEW_LAYER_NOT_FOUND",
            "The snapshot view layer no longer exists",
        )
    _ensure_object_mode(viewport)
    with bpy.context.temp_override(
        window=viewport.window,
        area=viewport.area,
        region=viewport.region,
        scene=scene,
        view_layer=view_layer,
    ):
        for obj in view_layer.objects:
            obj.select_set(False)
        for name in snapshot["selected_objects"]:
            obj = bpy.data.objects.get(name)
            if obj is None:
                raise ContextOperationError(
                    "OBJECT_NOT_FOUND",
                    f"Snapshot object no longer exists: {name}",
                )
            obj.select_set(True)
        active_name = snapshot["active_object"]
        view_layer.objects.active = bpy.data.objects.get(active_name) if active_name else None
        if scene.frame_current != snapshot["frame_current"]:
            scene.frame_set(snapshot["frame_current"])
        camera_name = snapshot["active_camera"]
        camera = bpy.data.objects.get(camera_name) if camera_name else None
        if scene.camera != camera:
            scene.camera = camera
        view = snapshot["view"]
        region_3d = viewport.space.region_3d
        region_3d.view_location = view["location"]
        region_3d.view_rotation = view["rotation"]
        region_3d.view_distance = view["distance"]
        region_3d.view_perspective = view["perspective"]
        viewport.space.lens = view["lens"]
        viewport.space.shading.type = view["shading"]
        viewport.space.overlay.show_overlays = view["show_overlays"]
        region_3d.update()
        _restore_mode(viewport, snapshot["mode"])


def validate_context_snapshot(snapshot: dict[str, Any]) -> None:
    _find_viewport(snapshot)
    scene = bpy.data.scenes.get(snapshot["scene"])
    if scene is None:
        raise ContextOperationError("SCENE_NOT_FOUND", "The snapshot scene no longer exists")
    if scene.view_layers.get(snapshot["view_layer"]) is None:
        raise ContextOperationError(
            "VIEW_LAYER_NOT_FOUND",
            "The snapshot view layer no longer exists",
        )
    names = list(snapshot["selected_objects"])
    if snapshot["active_object"]:
        names.append(snapshot["active_object"])
    if snapshot["active_camera"]:
        names.append(snapshot["active_camera"])
    missing = sorted({name for name in names if bpy.data.objects.get(name) is None})
    if missing:
        raise ContextOperationError(
            "OBJECT_NOT_FOUND",
            f"Snapshot objects no longer exist: {', '.join(missing)}",
        )


def context_summary() -> dict[str, Any]:
    primary = resolve_viewport()
    snapshot = capture_context(primary.viewport_id)
    snapshot["viewports"] = [
        {
            "viewport_id": viewport.viewport_id,
            "width": viewport.area.width,
            "height": viewport.area.height,
            "primary": viewport.viewport_id == primary.viewport_id,
        }
        for viewport in list_viewports()
    ]
    snapshot["blend_file"] = bpy.data.filepath
    snapshot["blend_file_saved"] = bool(bpy.data.filepath)
    return snapshot


def inspect_object(object_name: str) -> dict[str, Any]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise ContextOperationError(
            "OBJECT_NOT_FOUND",
            f"Object does not exist: {object_name}",
            kind="not_found",
        )
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    world_bounds = [list(evaluated.matrix_world @ Vector(corner)) for corner in evaluated.bound_box]
    return {
        "name": obj.name,
        "name_full": obj.name_full,
        "type": obj.type,
        "session_identity": f"object:{obj.as_pointer():x}",
        "library": obj.library.filepath if obj.library else None,
        "location": list(obj.location),
        "rotation_mode": obj.rotation_mode,
        "rotation_euler": list(obj.rotation_euler),
        "rotation_quaternion": list(obj.rotation_quaternion),
        "scale": list(obj.scale),
        "dimensions": list(obj.dimensions),
        "visible": obj.visible_get(),
        "hide_viewport": obj.hide_viewport,
        "hide_render": obj.hide_render,
        "selected": obj.select_get(),
        "world_bounds": world_bounds,
    }


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ContextOperationError("CAPTURE_INVALID", "Viewport capture did not produce a PNG")
    return struct.unpack(">II", data[16:24])


def capture_viewport(
    object_name: str,
    view: str,
    max_size: int,
    viewport_id: str | None,
) -> dict[str, Any]:
    if view not in {"FRONT", "RIGHT", "TOP", "BACK", "LEFT", "BOTTOM", "CURRENT"}:
        raise ContextOperationError("VIEW_INVALID", f"Unsupported view: {view}", kind="validation")
    if not 256 <= max_size <= 1600:
        raise ContextOperationError(
            "MAX_SIZE_INVALID",
            "max_size must be between 256 and 1600",
            kind="validation",
        )
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise ContextOperationError(
            "OBJECT_NOT_FOUND",
            f"Object does not exist: {object_name}",
            kind="not_found",
        )
    if obj.hide_viewport or not obj.visible_get():
        raise ContextOperationError("OBJECT_HIDDEN", f"Object is not visible: {object_name}")
    snapshot = capture_context(viewport_id)
    viewport = _find_viewport(snapshot)
    preferences_view = bpy.context.preferences.view
    smooth_view = preferences_view.smooth_view
    result: dict[str, Any] | None = None
    offscreen: Any | None = None
    try:
        preferences_view.smooth_view = 0
        _ensure_object_mode(viewport)
        with bpy.context.temp_override(
            window=viewport.window,
            area=viewport.area,
            region=viewport.region,
        ):
            for candidate in bpy.context.view_layer.objects:
                candidate.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            if view != "CURRENT" and "FINISHED" not in bpy.ops.view3d.view_axis(
                type=view,
                align_active=False,
            ):
                raise ContextOperationError("VIEW_AXIS_FAILED", f"Could not set view {view}")
            if "FINISHED" not in bpy.ops.view3d.view_selected(use_all_regions=False):
                raise ContextOperationError("VIEW_FRAME_FAILED", f"Could not frame {object_name}")
            viewport.space.region_3d.update()
            width, height = bounded_dimensions(
                viewport.region.width,
                viewport.region.height,
                max_size,
            )
            view_matrix = viewport.space.region_3d.view_matrix.copy()
            projection_matrix = viewport.space.region_3d.window_matrix.copy()
            try:
                offscreen = gpu.types.GPUOffScreen(width, height, format="RGBA8")
                offscreen.draw_view3d(
                    viewport.window.scene,
                    viewport.window.view_layer,
                    viewport.space,
                    viewport.region,
                    view_matrix,
                    projection_matrix,
                    do_color_management=True,
                    draw_background=True,
                )
                with offscreen.bind():
                    framebuffer = gpu.state.active_framebuffer_get()
                    pixels = framebuffer.read_color(
                        0,
                        0,
                        width,
                        height,
                        4,
                        0,
                        "UBYTE",
                    )
                    rgba = flatten_rgba_buffer(pixels, width, height)
            except Exception as exc:
                raise ContextOperationError(
                    "CAPTURE_GPU_UNAVAILABLE",
                    f"Off-screen viewport rendering failed: {type(exc).__name__}",
                    kind="blender_api",
                    retryable=True,
                ) from exc
        if is_blank_rgba(rgba):
            raise ContextOperationError(
                "CAPTURE_BLANK",
                "Off-screen viewport rendering returned an all-black image",
                kind="blender_api",
                retryable=True,
            )
        data = encode_rgba_png(width, height, rgba, bottom_up=True)
        png_width, png_height = _png_size(data)
        result = {
            "object_name": object_name,
            "view": view,
            "viewport_id": viewport.viewport_id,
            "native_width": png_width,
            "native_height": png_height,
            "max_size": max_size,
            "mime_type": "image/png",
            "backend": "gpu_offscreen",
            "focus_requirement": "none_when_window_exists",
            "native_sha256": hashlib.sha256(data).hexdigest(),
            "png_base64": base64.b64encode(data).decode("ascii"),
        }
    finally:
        try:
            if offscreen is not None:
                offscreen.free()
        finally:
            try:
                restore_context(snapshot)
            finally:
                preferences_view.smooth_view = smooth_view
    restored = capture_context(snapshot["viewport_id"])
    if restored != snapshot:
        changed_fields = {
            key: {"before": snapshot.get(key), "after": restored.get(key)}
            for key in sorted(snapshot.keys() | restored.keys())
            if snapshot.get(key) != restored.get(key)
        }
        raise ContextOperationError(
            "OBSERVATION_CONTEXT_DRIFT",
            "Viewport capture did not restore the original user context",
            kind="conflict",
            retryable=True,
            details={"changed_fields": changed_fields},
        )
    assert result is not None
    result["context_unchanged"] = True
    return result
