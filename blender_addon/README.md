# Blender add-on

This directory will contain the installable Blender-side package.

The first implementation milestone is deliberately limited to local transport,
main-thread dispatch, context observation, viewport capture, and rollback. Do
not add asset marketplaces, telemetry, or arbitrary network integrations.

Add-on source must remain compatible with Blender 4.2's Python 3.11 runtime.
