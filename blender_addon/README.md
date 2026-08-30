# Blender add-on

This directory contains the installable Blender-side package.

The add-on remains limited to local transport, main-thread semantic dispatch,
observation, bounded static-scene authoring, reviewed Eevee output, and rollback. Do
not add asset marketplaces, telemetry, arbitrary network integrations, or arbitrary
Python/RNA execution.

Add-on source must remain compatible with Blender 4.2's Python 3.11 runtime.

Build the local development ZIP from the repository root:

~~~powershell
uv run --no-sync python scripts/build_addon.py
~~~

The ignored output is written to
`artifacts/blender-research-mcp-addon-0.12.0.zip`. Install that ZIP in Blender
4.2, then enable **Blender Research MCP**. The listener binds only to
`127.0.0.1:9877`, creates a random per-session token, and publishes its
ephemeral manifest under the current user's local application data directory.

The current surface includes authenticated ping, context snapshots, exact object and
evaluated-mesh inspection, focus-independent GPU off-screen viewport capture,
capture-bound raycasts, bounded LookDev inspection, and transaction-scoped absolute
scale, visibility, modifier-state, shape-key, and material-input preview operations,
plus structural transaction v3 object/material/image/World/Camera authoring and bounded
Eevee preview/export. Version 0.9 adds one closed `object.set` command for atomic
transform, visibility, typed Light, and typed Camera data patches; it does not expose
generic RNA or separate `light.set`/`camera.set` commands.
Version 0.10 adds exact Modifier-stack inspection and transaction-scoped create, typed
set, reorder, and deferred delete operations for Bevel, Subdivision, Solidify, and
Boolean. Version 0.11 adds transaction-v4 exact base-Mesh inspection and one bounded
component editor with reversible snapshots and explicit object/shared-data scope. It
does not expose apply, UV editing, arbitrary BMesh/RNA, or component-array replacement.
Captures can temporarily use solid, material, wireframe, or rendered shading and an
absolute orbit while restoring the user's original context. Blender may be obscured by
another window, but it must remain running with a `VIEW_3D`; minimized capture is not
guaranteed. The add-on returns a structured GPU error instead of a black image when
capture is unavailable.

The 3D Viewport sidebar contains a compact status panel. Complete endpoint,
heartbeat, generation, command timing, transaction, and error information is also
available under **Scene Properties > Blender Research MCP**. The add-on never creates,
splits, or rearranges Blender areas automatically.

The Scene Properties panel lists the semantic authoring categories and, while a
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
restores guarded property/structure deltas and user context. Structural operations are
limited to supported primitives, canonical Principled channels, absolute local images,
World/Camera state, and reviewed Eevee renders. Explicit project lifecycle tools may
commit and save before switching or quitting according to the request. The add-on does
not execute arbitrary Python, enable telemetry, or contact third-party services.
