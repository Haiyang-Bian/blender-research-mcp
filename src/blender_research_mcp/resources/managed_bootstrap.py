"""Fixed Blender bootstrap for a managed Blender Research MCP session."""

from __future__ import annotations

import os
import sys

import addon_utils  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]

ADDON_PATH_ENV = "BLENDER_RESEARCH_MCP_ADDON_RESOURCE_PATH"
ADDON_MODULE = "blender_research_mcp_addon"


def main() -> None:
    # Enter a usable project immediately instead of waiting on Blender's modal
    # splash/project chooser. This only changes the current process; preferences
    # are never saved by the bootstrap.
    bpy.context.preferences.view.show_splash = False
    addon_path = os.environ.get(ADDON_PATH_ENV)
    if not addon_path:
        raise RuntimeError(f"{ADDON_PATH_ENV} is required")
    loaded = [
        name
        for name in sys.modules
        if name == ADDON_MODULE or name.startswith(f"{ADDON_MODULE}.")
    ]
    if loaded:
        addon_utils.disable(ADDON_MODULE, default_set=False)
        for name in loaded:
            sys.modules.pop(name, None)
    if addon_path not in sys.path:
        sys.path.insert(0, addon_path)
    addon_utils.enable(ADDON_MODULE, default_set=False, persistent=True)
    if addon_utils.check(ADDON_MODULE)[1] is False:
        raise RuntimeError(f"Could not enable {ADDON_MODULE}")


main()
