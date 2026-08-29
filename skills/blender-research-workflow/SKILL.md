---
name: blender-research-workflow
description: Inspect, spatially diagnose, and reversibly preview bounded LookDev changes in a live Blender 4.2 scene through Blender Research MCP. Use for viewport evidence, capture-bound raycasts, evaluated mesh summaries, scale, visibility, modifier-state, shape-key, or material-input previews; do not use for arbitrary Python or saving blend files.
---

# Blender Research Workflow

Use the semantic `blender_research` MCP as the source of truth for live Blender state.

## Start safely

1. Call `connection.ping` before relying on the tools. Require protocol `1`, add-on
   `0.5.x`, `viewport_capture >= 3`, `viewport_raycast >= 1`,
   `geometry_inspection >= 1`, `lookdev_inspection >= 1`, and
   `transactions >= 2`.
2. Call `context.get` and use exact object names. Never infer live connectivity from
   tool registration alone.
3. For visual diagnosis, prefer `observation.bundle` with the smallest useful set of
   views. Use `object.inspect` or `object.geometry.inspect` when structured state
   answers the question without an image.

Blender may be behind another window. It must remain running with a `VIEW_3D` area;
minimized capture is not guaranteed. Treat `CAPTURE_GPU_UNAVAILABLE` as a request to
restore the Blender window, not permission to use desktop automation or raw Python.

## Ground image evidence

Use a successful capture's own `capture_id` when mapping normalized top-left image
coordinates through `viewport.raycast`. Do not raycast against a different capture or
infer that a transparent rendered surface is absent from geometry. Inspect the exact
hit object when a structured mesh summary would clarify the diagnosis.

If the capture is missing, stale, or belongs to a different scene/view layer, discard
the image-coordinate pair and capture again. Never reuse matrices or coordinates from
rejected evidence.

## Mutate through a preview

Use a transaction for every supported scene mutation. Pass the latest returned
`scene_generation` and a new idempotency key to each distinct mutation request. Change
one variable at a time, collect structured and visual evidence, and roll back unless
the user has explicitly asked to retain or accepted the result. Commit retains only
the in-memory Blender state and never saves the blend file.

Before a visibility, modifier, shape-key, or material preview, call
`object.lookdev.inspect` and use its exact target identities. For a material input,
also call `material.inspect` and choose a socket reported as writable. Do not infer
slot, node, socket, modifier, or shape-key names. Treat a shared material as a broader
change: require the user's intent before setting `allow_shared=true`, use the exact
inspected user count, and review affected objects. Never create a single-user copy,
edit topology, or substitute another unsupported write.

If a context, property, generation, or idempotency conflict occurs, stop. Do not force
restore, overwrite user state, open a second transaction, or fall back to unrestricted
Python. Connection loss may trigger the add-on's automatic rollback.

Read [references/tool-recipes.md](references/tool-recipes.md) when executing a
multi-step observation, preview, reconnect, or recovery workflow.
