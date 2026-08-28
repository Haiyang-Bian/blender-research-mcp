# Blender add-on

This directory contains the installable Blender-side package.

The first implementation milestone is deliberately limited to local transport,
main-thread dispatch, context observation, viewport capture, and rollback. Do
not add asset marketplaces, telemetry, or arbitrary network integrations.

Add-on source must remain compatible with Blender 4.2's Python 3.11 runtime.

Build the local development ZIP from the repository root:

~~~powershell
uv run --no-sync python scripts/build_addon.py
~~~

The ignored output is written to
`artifacts/blender-research-mcp-addon-0.4.0.zip`. Install that ZIP in Blender
4.2, then enable **Blender Research MCP**. The listener binds only to
`127.0.0.1:9877`, creates a random per-session token, and publishes its
ephemeral manifest under the current user's local application data directory.

The current surface includes authenticated ping, context snapshots, exact object and
evaluated-mesh inspection, focus-independent GPU off-screen viewport capture,
capture-bound raycasts, and one transaction-scoped absolute local-scale operation.
Captures can temporarily use solid, material, wireframe, or rendered shading and an
absolute orbit while restoring the user's original context. Blender may be obscured by
another window, but it must remain running with a `VIEW_3D`; minimized capture is not
guaranteed. The add-on returns a structured GPU error instead of a black image when
capture is unavailable.

The 3D Viewport sidebar contains a compact status panel. Complete endpoint,
heartbeat, generation, command timing, transaction, and error information is also
available under **Scene Properties > Blender Research MCP**. The add-on never creates,
splits, or rearranges Blender areas automatically.

Commit affects only the in-memory Blender session; rollback restores guarded
property deltas and user context. The add-on does not save the blend file,
execute arbitrary Python, enable telemetry, or contact third-party services.
