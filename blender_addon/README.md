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
`artifacts/blender-research-mcp-addon-0.2.0.zip`. Install that ZIP in Blender
4.2, then enable **Blender Research MCP**. The listener binds only to
`127.0.0.1:9877`, creates a random per-session token, and publishes its
ephemeral manifest under the current user's local application data directory.

The first observation surface includes authenticated ping, context snapshots,
exact object inspection, and context-restoring viewport capture. It does not
save the blend file, execute arbitrary Python, enable telemetry, or contact
third-party services.
