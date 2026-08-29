"""Context-safe Blender observation operations."""

from __future__ import annotations

import base64
import hashlib
import math
import struct
from dataclasses import dataclass
from typing import Any

import bpy
import gpu
from mathutils import Matrix, Vector

from .capture_codec import (
    bounded_dimensions,
    encode_rgba_png,
    flatten_rgba_buffer,
    is_blank_rgba,
)
from .capture_model import CaptureEvidence, MatrixRows
from .geometry_model import DETAIL_POLYGON_LIMIT, summarize_polygon_diagnostics


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


def _stable_view_float(value: Any) -> float:
    """Canonicalize Blender float32 view state for exact context evidence."""

    return round(float(value), 6)


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
            "location": [_stable_view_float(value) for value in region_3d.view_location],
            "rotation": [_stable_view_float(value) for value in region_3d.view_rotation],
            "distance": _stable_view_float(region_3d.view_distance),
            "perspective": region_3d.view_perspective,
            "lens": _stable_view_float(viewport.space.lens),
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
    snapshot["blend_file_dirty"] = bool(getattr(bpy.data, "is_dirty", False))
    return snapshot


def inspect_object(object_name: str) -> dict[str, Any]:
    from .object_settings_ops import object_data_summary

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
        "rotation_euler_degrees": [math.degrees(float(value)) for value in obj.rotation_euler],
        "rotation_quaternion": list(obj.rotation_quaternion),
        "scale": list(obj.scale),
        "dimensions": list(obj.dimensions),
        "visible": obj.visible_get(),
        "hide_viewport": obj.hide_viewport,
        "hide_render": obj.hide_render,
        "visibility": {
            "hide_viewport": bool(obj.hide_viewport),
            "hide_render": bool(obj.hide_render),
        },
        "data": object_data_summary(obj),
        "selected": obj.select_get(),
        "world_bounds": world_bounds,
    }


def inspect_geometry(object_name: str) -> dict[str, Any]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise ContextOperationError(
            "OBJECT_NOT_FOUND",
            f"Object does not exist: {object_name}",
            kind="not_found",
        )
    if obj.type != "MESH":
        raise ContextOperationError(
            "OBJECT_GEOMETRY_UNSUPPORTED",
            f"Evaluated geometry inspection only supports MESH objects: {object_name}",
        )
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.data
        mesh.calc_loop_triangles()
        polygon_count = len(mesh.polygons)
        material_slots = [
            {
                "index": index,
                "name": slot.material.name if slot.material else None,
                "polygon_count": None,
            }
            for index, slot in enumerate(obj.material_slots)
        ]
        warnings: list[dict[str, Any]] = []
        surface_area_local: float | None = None
        edge_topology: dict[str, int] | None = None
        unassigned_polygon_count: int | None = None
        if polygon_count <= DETAIL_POLYGON_LIMIT:
            diagnostics = summarize_polygon_diagnostics(
                edge_count=len(mesh.edges),
                material_slot_count=len(material_slots),
                polygons=(
                    (
                        (mesh.loops[index].edge_index for index in polygon.loop_indices),
                        int(polygon.material_index),
                        float(polygon.area),
                    )
                    for polygon in mesh.polygons
                ),
            )
            surface_area_local = float(diagnostics["surface_area_local"])
            edge_topology = dict(diagnostics["edge_topology"])
            unassigned_polygon_count = int(diagnostics["unassigned_polygon_count"])
            for slot, count in zip(
                material_slots,
                diagnostics["material_polygon_counts"],
                strict=True,
            ):
                slot["polygon_count"] = int(count)
        else:
            warnings.append(
                {
                    "code": "GEOMETRY_DIAGNOSTICS_TRUNCATED",
                    "polygon_limit": DETAIL_POLYGON_LIMIT,
                    "polygon_count": polygon_count,
                }
            )
        local_bounds = [list(corner) for corner in evaluated.bound_box]
        world_bounds = [
            list(evaluated.matrix_world @ Vector(corner)) for corner in evaluated.bound_box
        ]
        return {
            "name": obj.name,
            "type": obj.type,
            "session_identity": f"object:{obj.as_pointer():x}",
            "library": obj.library.filepath if obj.library else None,
            "counts": {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": polygon_count,
                "loop_triangles": len(mesh.loop_triangles),
            },
            "dimensions": list(evaluated.dimensions),
            "local_bounds": local_bounds,
            "world_bounds": world_bounds,
            "surface_area_local": surface_area_local,
            "edge_topology": edge_topology,
            "material_slots": material_slots,
            "unassigned_polygon_count": unassigned_polygon_count,
            "modifiers": [
                {
                    "name": modifier.name,
                    "type": modifier.type,
                    "show_viewport": bool(modifier.show_viewport),
                    "show_render": bool(modifier.show_render),
                }
                for modifier in obj.modifiers
            ],
            "warnings": warnings,
        }
    except ContextOperationError:
        raise
    except Exception as exc:
        raise ContextOperationError(
            "GEOMETRY_EVALUATION_FAILED",
            f"Could not inspect evaluated geometry for {object_name}: {type(exc).__name__}",
            kind="blender_api",
            retryable=True,
        ) from exc


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ContextOperationError("CAPTURE_INVALID", "Viewport capture did not produce a PNG")
    return struct.unpack(">II", data[16:24])


def _matrix_rows(matrix: Any) -> MatrixRows:
    rows = tuple(tuple(float(value) for value in row) for row in matrix)
    if len(rows) != 4 or any(len(row) != 4 for row in rows):
        raise ContextOperationError(
            "RAYCAST_MATRIX_INVALID",
            "Viewport capture did not produce a 4x4 matrix",
            kind="blender_api",
        )
    return rows  # type: ignore[return-value]


def _validate_capture_options(
    view: str,
    display_mode: str,
    overlays: str,
    orbit: dict[str, Any] | None,
) -> tuple[float, float] | None:
    if display_mode not in {"CURRENT", "WIREFRAME", "SOLID", "MATERIAL", "RENDERED"}:
        raise ContextOperationError(
            "DISPLAY_MODE_INVALID",
            f"Unsupported display mode: {display_mode}",
            kind="validation",
        )
    if overlays not in {"CURRENT", "ON", "OFF"}:
        raise ContextOperationError(
            "OVERLAYS_INVALID",
            f"Unsupported overlays mode: {overlays}",
            kind="validation",
        )
    if orbit is None:
        return None
    if view == "CURRENT":
        raise ContextOperationError(
            "ORBIT_VIEW_INVALID",
            "orbit requires a semantic base view rather than CURRENT",
            kind="validation",
        )
    if not isinstance(orbit, dict) or set(orbit) - {"yaw_degrees", "pitch_degrees"}:
        raise ContextOperationError(
            "ORBIT_INVALID",
            "orbit must contain only yaw_degrees and pitch_degrees",
            kind="validation",
        )
    yaw = orbit.get("yaw_degrees", 0.0)
    pitch = orbit.get("pitch_degrees", 0.0)
    if (
        isinstance(yaw, bool)
        or not isinstance(yaw, (int, float))
        or isinstance(pitch, bool)
        or not isinstance(pitch, (int, float))
        or not math.isfinite(float(yaw))
        or not math.isfinite(float(pitch))
        or not -180.0 <= float(yaw) <= 180.0
        or not -89.0 <= float(pitch) <= 89.0
    ):
        raise ContextOperationError(
            "ORBIT_INVALID",
            "orbit yaw must be -180..180 and pitch must be -89..89 degrees",
            kind="validation",
        )
    return float(yaw), float(pitch)


def _apply_orbit(yaw: float, pitch: float) -> None:
    operations = (
        (yaw, "ORBITLEFT" if yaw >= 0 else "ORBITRIGHT"),
        (pitch, "ORBITUP" if pitch >= 0 else "ORBITDOWN"),
    )
    for angle, direction in operations:
        if abs(angle) <= 1e-9:
            continue
        if "FINISHED" not in bpy.ops.view3d.view_orbit(
            angle=math.radians(abs(angle)),
            type=direction,
        ):
            raise ContextOperationError("VIEW_ORBIT_FAILED", "Could not orbit the viewport")


def capture_viewport(
    object_name: str,
    view: str,
    max_size: int,
    viewport_id: str | None,
    display_mode: str = "CURRENT",
    overlays: str = "CURRENT",
    orbit: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if view not in {"FRONT", "RIGHT", "TOP", "BACK", "LEFT", "BOTTOM", "CURRENT"}:
        raise ContextOperationError("VIEW_INVALID", f"Unsupported view: {view}", kind="validation")
    if not 256 <= max_size <= 1600:
        raise ContextOperationError(
            "MAX_SIZE_INVALID",
            "max_size must be between 256 and 1600",
            kind="validation",
        )
    orbit_values = _validate_capture_options(view, display_mode, overlays, orbit)
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
    evidence: dict[str, Any] | None = None
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
            if orbit_values is not None:
                _apply_orbit(*orbit_values)
            if display_mode != "CURRENT":
                viewport.space.shading.type = display_mode
            if overlays != "CURRENT":
                viewport.space.overlay.show_overlays = overlays == "ON"
            viewport.space.region_3d.update()
            width, height = bounded_dimensions(
                viewport.region.width,
                viewport.region.height,
                max_size,
            )
            region_3d = viewport.space.region_3d
            view_matrix = region_3d.view_matrix.copy()
            projection_matrix = region_3d.window_matrix.copy()
            perspective_matrix = region_3d.perspective_matrix.copy()
            view_rows = _matrix_rows(view_matrix)
            projection_rows = _matrix_rows(projection_matrix)
            perspective_rows = _matrix_rows(perspective_matrix)
            projection_kind = "PERSP" if region_3d.is_perspective else "ORTHO"
            actual_display_mode = str(viewport.space.shading.type)
            actual_overlays = "ON" if viewport.space.overlay.show_overlays else "OFF"
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
            "display_mode": actual_display_mode,
            "overlays": actual_overlays,
            "orbit": (
                {"yaw_degrees": orbit_values[0], "pitch_degrees": orbit_values[1]}
                if orbit_values is not None
                else None
            ),
            "viewport_id": viewport.viewport_id,
            "native_width": png_width,
            "native_height": png_height,
            "max_size": max_size,
            "mime_type": "image/png",
            "backend": "gpu_offscreen",
            "focus_requirement": "none_when_window_exists",
            "native_sha256": hashlib.sha256(data).hexdigest(),
            "projection_kind": projection_kind,
            "coordinate_space": "normalized_top_left",
            "view_matrix": view_rows,
            "projection_matrix": projection_rows,
            "perspective_matrix": perspective_rows,
            "png_base64": base64.b64encode(data).decode("ascii"),
        }
        evidence = {
            "scene": viewport.window.scene.name,
            "view_layer": viewport.window.view_layer.name,
            "window_id": viewport.window.as_pointer(),
            "target_name": object_name,
            "target_identity": f"object:{obj.as_pointer():x}",
            "viewport_id": viewport.viewport_id,
            "view": view,
            "display_mode": actual_display_mode,
            "overlays": actual_overlays,
            "width": png_width,
            "height": png_height,
            "native_sha256": result["native_sha256"],
            "projection_kind": projection_kind,
            "clip_start": float(viewport.space.clip_start),
            "clip_end": float(viewport.space.clip_end),
            "view_matrix": view_rows,
            "projection_matrix": projection_rows,
            "perspective_matrix": perspective_rows,
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
    assert evidence is not None
    result["context_unchanged"] = True
    return result, evidence


def _window_for_capture(evidence: CaptureEvidence) -> Any:
    for window in bpy.context.window_manager.windows:
        if window.as_pointer() == evidence.window_id:
            if (
                window.scene.name != evidence.scene
                or window.view_layer.name != evidence.view_layer
            ):
                break
            return window
    raise ContextOperationError(
        "CAPTURE_CONTEXT_STALE",
        "The capture scene or view layer is no longer active in its Blender window",
        kind="conflict",
        retryable=True,
        details={"scene": evidence.scene, "view_layer": evidence.view_layer},
    )


def _unproject_ray(evidence: CaptureEvidence, x: float, y: float) -> tuple[Vector, Vector, float]:
    try:
        inverse = Matrix(evidence.perspective_matrix).inverted()
        near_h = inverse @ Vector((2.0 * x - 1.0, 1.0 - 2.0 * y, -1.0, 1.0))
        far_h = inverse @ Vector((2.0 * x - 1.0, 1.0 - 2.0 * y, 1.0, 1.0))
        if abs(float(near_h.w)) < 1e-12 or abs(float(far_h.w)) < 1e-12:
            raise ValueError("homogeneous ray endpoint has zero w")
        origin = Vector((near_h.x / near_h.w, near_h.y / near_h.w, near_h.z / near_h.w))
        far = Vector((far_h.x / far_h.w, far_h.y / far_h.w, far_h.z / far_h.w))
        segment = far - origin
        distance = float(segment.length)
        if distance <= 1e-12:
            raise ValueError("ray endpoints are identical")
        return origin, segment.normalized(), distance
    except Exception as exc:
        raise ContextOperationError(
            "RAYCAST_MATRIX_INVALID",
            "The capture projection matrix could not be inverted into a finite ray",
            kind="blender_api",
            details={"capture_id": evidence.capture_id},
        ) from exc


def raycast_capture(evidence: CaptureEvidence, x: float, y: float) -> dict[str, Any]:
    """Cast against the same evaluated scene represented by a capture."""
    window = _window_for_capture(evidence)
    origin, direction, max_distance = _unproject_ray(evidence, x, y)
    with bpy.context.temp_override(
        window=window,
        scene=window.scene,
        view_layer=window.view_layer,
    ):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        hit, location, normal, face_index, obj, _matrix = window.scene.ray_cast(
            depsgraph,
            origin,
            direction,
            distance=max_distance,
        )
    result: dict[str, Any] = {
        "capture_id": evidence.capture_id,
        "capture_scene_generation": evidence.scene_generation,
        "coordinate": {"x": x, "y": y, "space": "normalized_top_left"},
        "ray": {
            "origin": list(origin),
            "direction": list(direction),
            "max_distance": max_distance,
        },
        "hit": bool(hit),
        "hit_object": None,
        "location": None,
        "normal": None,
        "face_index": None,
        "distance": None,
        "hit_target": False,
    }
    if hit and obj is not None:
        result.update(
            {
                "hit_object": {
                    "name": obj.name,
                    "type": obj.type,
                    "session_identity": f"object:{obj.as_pointer():x}",
                    "library": obj.library.filepath if obj.library else None,
                },
                "location": list(location),
                "normal": list(normal),
                "face_index": int(face_index),
                "distance": float((location - origin).length),
                "hit_target": obj.name == evidence.target_name,
            }
        )
    return result
