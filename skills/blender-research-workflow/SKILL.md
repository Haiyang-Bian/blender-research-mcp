---
name: blender-research-workflow
description: Inspect, diagnose, compare, and reversibly preview supported changes in a live Blender 4.2 scene through Blender Research MCP. Use when the user asks to observe objects or viewports, capture multi-view evidence, adjust supported object scale values, or verify and roll back Blender changes; do not use for arbitrary Python or saving blend files.
---

# Blender Research Workflow

Use the semantic `blender_research` MCP as the source of truth for live Blender state.

## Start safely

1. Call `connection.ping` before relying on the tools. Require protocol `1`, add-on
   `0.3.x`, and `capability_versions.viewport_capture >= 2`.
2. Call `context.get` and use exact object names. Never infer live connectivity from
   tool registration alone.
3. For visual diagnosis, prefer `observation.bundle` with the smallest useful set of
   views. Use `object.inspect` when structured state answers the question without an
   image.

Blender may be behind another window. It must remain running with a `VIEW_3D` area;
minimized capture is not guaranteed. Treat `CAPTURE_GPU_UNAVAILABLE` as a request to
restore the Blender window, not permission to use desktop automation or raw Python.

## Mutate through a preview

Use a transaction for every supported scene mutation. Pass the latest returned
`scene_generation` and a new idempotency key to each distinct mutation request. Change
one variable at a time, collect structured and visual evidence, and roll back unless
the user has explicitly asked to retain or accepted the result. Commit retains only
the in-memory Blender state and never saves the blend file.

If a context, property, generation, or idempotency conflict occurs, stop. Do not force
restore, overwrite user state, open a second transaction, or fall back to unrestricted
Python. Connection loss may trigger the add-on's automatic rollback.

Read [references/tool-recipes.md](references/tool-recipes.md) when executing a
multi-step observation, preview, reconnect, or recovery workflow.
