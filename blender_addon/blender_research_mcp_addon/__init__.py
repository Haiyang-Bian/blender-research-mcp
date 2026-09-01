"""Blender Research MCP add-on."""

import os
from typing import Any

import bpy
from bpy.app.handlers import persistent
from bpy.props import IntProperty

from .runtime import ADDON_VERSION
from .state import AddonState

bl_info = {
    "name": "Blender Research MCP",
    "author": "Blender Research MCP contributors",
    "version": (0, 15, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Research MCP",
    "description": "Local semantic, observable, and reversible MCP bridge",
    "category": "Development",
}

STATE: AddonState | None = None
PORT_ENV = "BLENDER_RESEARCH_MCP_PORT"
DEFAULT_PORT = 9877


def _runtime_port(preference_port: int) -> int:
    managed = os.environ.get(PORT_ENV)
    if managed is None:
        return preference_port
    try:
        port = int(managed)
    except ValueError:
        return preference_port
    return port if 1 <= port <= 65535 else preference_port


def _preference_port(context: bpy.types.Context) -> int:
    """Return the saved port, or the default for a session-only managed add-on."""
    addon = context.preferences.addons.get(__package__)
    if addon is None:
        return DEFAULT_PORT
    return int(addon.preferences.port)


class BRMCP_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    port: IntProperty(name="Loopback Port", default=9877, min=1024, max=65535)

    def draw(self, context: bpy.types.Context) -> None:
        del context
        layout = self.layout
        layout.prop(self, "port")
        layout.label(text="Changes take effect after Restart Bridge")


class BRMCP_OT_restart(bpy.types.Operator):
    bl_idname = "brmcp.restart_bridge"
    bl_label = "Restart Bridge"
    bl_description = "Rotate the session token and restart the loopback listener"

    def execute(self, context: bpy.types.Context) -> set[str]:
        global STATE
        if STATE is not None:
            STATE.stop()
        STATE = AddonState()
        STATE.runtime.port = _runtime_port(_preference_port(context))
        STATE.start()
        return {"FINISHED"}


def _draw_stopped(layout: Any) -> bool:
    if STATE is not None:
        return False
    layout.label(text="Stopped", icon="PAUSE")
    return True


def _draw_compact_status(layout: Any) -> None:
    if _draw_stopped(layout):
        return
    assert STATE is not None
    runtime = STATE.runtime
    icon = "CHECKMARK" if runtime.status in {"listening", "connected"} else "ERROR"
    layout.label(text=f"Status: {runtime.status}", icon=icon)
    layout.label(text=f"Capture: {STATE.last_capture_backend}")
    layout.label(text=f"Transaction: {STATE.transactions.last_status}")
    if runtime.last_error or STATE.last_error:
        box = layout.box()
        box.label(text=runtime.last_error or STATE.last_error, icon="ERROR")
    layout.operator(BRMCP_OT_restart.bl_idname, icon="FILE_REFRESH")


def _draw_full_status(layout: Any) -> None:
    if _draw_stopped(layout):
        return
    assert STATE is not None
    runtime = STATE.runtime
    icon = "CHECKMARK" if runtime.status in {"listening", "connected"} else "ERROR"
    layout.label(text=f"Add-on: {ADDON_VERSION}")
    layout.label(text=f"Status: {runtime.status}", icon=icon)
    layout.label(text=f"Endpoint: 127.0.0.1:{runtime.port}")
    layout.label(text=f"Instance: {runtime.instance_id[:8] or 'starting'}")
    layout.label(text=f"Connected: {'yes' if runtime.connected else 'no'}")
    layout.label(text=f"Heartbeat: {STATE.heartbeat}")
    layout.label(text=f"Scene generation: {STATE.scene_generation}")
    layout.label(text=f"Capture: {STATE.last_capture_backend}")
    project = STATE.project_summary()
    project_box = layout.box()
    project_box.label(text="Project lifecycle", icon="FILE_BLEND")
    project_box.label(text=f"Path: {project['filepath'] or 'Untitled'}")
    project_box.label(text=f"Dirty: {'yes' if project['is_dirty'] else 'no'}")
    operation = project["last_operation"]
    if operation is not None:
        project_box.label(
            text=f"Last: {operation['kind']} ({operation['status']})"
        )
    transaction = STATE.transactions.active
    if transaction is None:
        layout.label(text=f"Transaction: {STATE.transactions.last_status}")
    else:
        transaction_box = layout.box()
        transaction_box.label(text="Active transaction", icon="MODIFIER")
        transaction_box.label(text=f"ID: {transaction.transaction_id[:8]}")
        if transaction.label:
            transaction_box.label(text=f"Label: {transaction.label}")
        transaction_box.label(text=f"Deltas: {len(transaction.deltas)}")
        kinds = ", ".join(transaction.delta_kinds()) or "none"
        transaction_box.label(text=f"Kinds: {kinds}")
    authority_box = layout.box()
    authority_box.label(text="Semantic scene authoring", icon="LOCKVIEW_ON")
    authority_box.label(text="Objects, transforms, and material slots")
    authority_box.label(text="Principled materials and local images")
    authority_box.label(text="World, active Camera, and Eevee renders")
    authority_box.label(text="Object visibility")
    authority_box.label(text="Modifier viewport/render state")
    authority_box.label(text="Shape key value")
    authority_box.label(text="Material input default value")
    authority_box.label(text="Commit never saves the blend file")
    if STATE.active_command:
        layout.label(text=f"Running: {STATE.active_command}")
    layout.label(text=f"Last command: {STATE.last_command_ms:.3f} ms")
    if runtime.last_error or STATE.last_error:
        box = layout.box()
        box.label(text=runtime.last_error or STATE.last_error, icon="ERROR")
    layout.operator(BRMCP_OT_restart.bl_idname, icon="FILE_REFRESH")


class BRMCP_PT_status(bpy.types.Panel):
    bl_label = "Blender Research MCP"
    bl_idname = "BRMCP_PT_status"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Research MCP"

    def draw(self, context: bpy.types.Context) -> None:
        del context
        _draw_compact_status(self.layout)


class BRMCP_PT_scene_status(bpy.types.Panel):
    bl_label = "Blender Research MCP"
    bl_idname = "BRMCP_PT_scene_status"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    def draw(self, context: bpy.types.Context) -> None:
        del context
        _draw_full_status(self.layout)


CLASSES = (
    BRMCP_AddonPreferences,
    BRMCP_OT_restart,
    BRMCP_PT_status,
    BRMCP_PT_scene_status,
)


def _timer() -> float | None:
    if STATE is None:
        return None
    STATE.tick()
    return 0.1


@persistent
def _depsgraph_update(_scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph) -> None:
    if STATE is not None:
        STATE.on_depsgraph_update(depsgraph)


@persistent
def _load_post(_unused: object) -> None:
    if STATE is not None:
        STATE.on_file_loaded()


@persistent
def _save_pre(filepath: str) -> None:
    if STATE is not None:
        try:
            STATE.on_native_save_pre(filepath)
        except Exception as exc:  # noqa: BLE001 - never prevent the user's native save
            STATE.last_error = f"NATIVE_SAVE_PRE_FAILED: {type(exc).__name__}: {exc}"


@persistent
def _save_post(filepath: str) -> None:
    if STATE is not None:
        STATE.on_native_save_post(filepath)


@persistent
def _save_post_fail(filepath: str) -> None:
    if STATE is not None:
        STATE.on_native_save_failed(filepath)


def register() -> None:
    global STATE
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    STATE = AddonState()
    STATE.runtime.port = _runtime_port(_preference_port(bpy.context))
    STATE.start()
    bpy.app.handlers.depsgraph_update_post.append(_depsgraph_update)
    bpy.app.handlers.load_post.append(_load_post)
    bpy.app.handlers.save_pre.append(_save_pre)
    bpy.app.handlers.save_post.append(_save_post)
    bpy.app.handlers.save_post_fail.append(_save_post_fail)
    bpy.app.timers.register(_timer, first_interval=0.1, persistent=True)


def unregister() -> None:
    global STATE
    if STATE is not None:
        STATE.stop()
        STATE = None
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    if _depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_update)
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if _save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_save_pre)
    if _save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_save_post)
    if _save_post_fail in bpy.app.handlers.save_post_fail:
        bpy.app.handlers.save_post_fail.remove(_save_post_fail)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
