"""Blender Research MCP add-on."""

import bpy
from bpy.app.handlers import persistent
from bpy.props import IntProperty

from .runtime import ADDON_VERSION
from .state import AddonState

bl_info = {
    "name": "Blender Research MCP",
    "author": "Blender Research MCP contributors",
    "version": (0, 4, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Research MCP",
    "description": "Local semantic, observable, and reversible MCP bridge",
    "category": "Development",
}

STATE: AddonState | None = None


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
        preferences = context.preferences.addons[__package__].preferences
        if STATE is not None:
            STATE.stop()
        STATE = AddonState()
        STATE.runtime.port = preferences.port
        STATE.start()
        return {"FINISHED"}


class BRMCP_PT_status(bpy.types.Panel):
    bl_label = "Blender Research MCP"
    bl_idname = "BRMCP_PT_status"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Research MCP"

    def draw(self, context: bpy.types.Context) -> None:
        del context
        layout = self.layout
        if STATE is None:
            layout.label(text="Stopped", icon="PAUSE")
            return
        runtime = STATE.runtime
        icon = "CHECKMARK" if runtime.status in {"listening", "connected"} else "ERROR"
        layout.label(text=f"Add-on: {ADDON_VERSION}")
        layout.label(text=f"Status: {runtime.status}", icon=icon)
        layout.label(text=f"Endpoint: 127.0.0.1:{runtime.port}")
        layout.label(text=f"Connected: {'yes' if runtime.connected else 'no'}")
        layout.label(text=f"Heartbeat: {STATE.heartbeat}")
        layout.label(text=f"Scene generation: {STATE.scene_generation}")
        layout.label(text=f"Capture: {STATE.last_capture_backend}")
        layout.label(text=f"Transaction: {STATE.transactions.last_status}")
        if STATE.active_command:
            layout.label(text=f"Running: {STATE.active_command}")
        if runtime.last_error or STATE.last_error:
            box = layout.box()
            box.label(text=runtime.last_error or STATE.last_error, icon="ERROR")
        layout.operator(BRMCP_OT_restart.bl_idname, icon="FILE_REFRESH")


CLASSES = (BRMCP_AddonPreferences, BRMCP_OT_restart, BRMCP_PT_status)


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


def register() -> None:
    global STATE
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    preferences = bpy.context.preferences.addons[__package__].preferences
    STATE = AddonState()
    STATE.runtime.port = preferences.port
    STATE.start()
    bpy.app.handlers.depsgraph_update_post.append(_depsgraph_update)
    bpy.app.handlers.load_post.append(_load_post)
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
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
