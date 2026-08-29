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
`artifacts/blender-research-mcp-addon-0.7.0.zip`. Install that ZIP in Blender
4.2, then enable **Blender Research MCP**. The listener binds only to
`127.0.0.1:9877`, creates a random per-session token, and publishes its
ephemeral manifest under the current user's local application data directory.

The current surface includes authenticated ping, context snapshots, exact object and
evaluated-mesh inspection, focus-independent GPU off-screen viewport capture,
capture-bound raycasts, bounded LookDev inspection, and transaction-scoped absolute
scale, visibility, modifier-state, shape-key, and material-input preview operations.
Captures can temporarily use solid, material, wireframe, or rendered shading and an
absolute orbit while restoring the user's original context. Blender may be obscured by
another window, but it must remain running with a `VIEW_3D`; minimized capture is not
guaranteed. The add-on returns a structured GPU error instead of a black image when
capture is unavailable.

The 3D Viewport sidebar contains a compact status panel. Complete endpoint,
heartbeat, generation, command timing, transaction, and error information is also
available under **Scene Properties > Blender Research MCP**. The add-on never creates,
splits, or rearranges Blender areas automatically.

The Scene Properties panel lists the authorized preview-write categories and, while a
transaction is active, its ID prefix, delta count, and delta kinds. The compact N-panel
remains limited to connection, capture, transaction, and error status.

The full panel also shows the current project path, dirty state, and most recent
lifecycle operation. The add-on exposes `project.status/save/open/reload` and
`application.quit`; open, reload, and quit are accepted in one request and executed on
the next main-thread timer tick after the socket response can be sent.

Comparative previews use transaction labels such as `compare:A`. The full Scene
Properties panel shows that label beside the active command so the operator can follow
candidate application, capture, and rollback without expanding the compact panel.

Material writes require the exact inspected object, slot, material, node, and socket
identities. Linked, driven, read-only, unsupported, and linked-library sockets are
rejected. Shared materials require both the exact current user count and an explicit
`allow_shared` confirmation; the add-on never makes a material single-user implicitly.

Ordinary transaction commit affects only the in-memory Blender session; rollback
restores guarded property deltas and user context. Explicit project lifecycle tools may
commit and save before switching or quitting according to the request. The add-on does
not execute arbitrary Python, enable telemetry, or contact third-party services.
