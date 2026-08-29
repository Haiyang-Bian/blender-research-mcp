# 0004 — Capture-bound spatial diagnosis

- Status: accepted
- Date: 2026-08-29

## Context

An image alone cannot identify which evaluated Blender object or polygon produced a
visible symptom. Casting against the user's current viewport is also unsafe: the user
may move or resize that viewport after the image was captured, silently changing the
meaning of the same pixel coordinate.

The add-on UI has a related boundary. Python panels can live in existing Blender
regions, but the bridge must not rearrange a user's areas or require a custom Blender
build merely to display connection status.

## Decision

- Every successful viewport capture creates a random session-local `capture_id` and
  records its scene generation, scene, view layer, target identity, dimensions, image
  hash, and exact view/projection matrices.
- Keep at most 32 records in LRU order. Clear them when the add-on stops, restarts, or
  loads another blend file; retain records across ordinary scene updates so callers
  receive `CAPTURE_STALE` rather than a misleading not-found result.
- Define image coordinates as normalized `[0, 1]` values with the origin at the top
  left. Reconstruct a world-space near/far segment from the stored perspective matrix
  and cast it through the evaluated scene.
- Reject raycasts when the generation, scene, view layer, or projection evidence no
  longer matches. A miss is a successful `hit: false` result.
- Raycasts describe the first geometric surface and do not reproduce material
  transparency or compositing.
- Evaluated mesh inspection returns counts and bounded summaries, never raw arrays.
  Python-level per-polygon diagnostics stop above 250,000 polygons and return a stable
  truncation warning.
- Keep a compact status panel in the 3D Viewport sidebar and a complete panel in the
  existing Scene Properties region. Never split areas or create a workspace at add-on
  startup.

## Alternatives

- Querying the current viewport was rejected because the coordinate would not remain
  tied to the image shown to the caller.
- Sending arbitrary matrices back into Blender was rejected because it would allow
  evidence-free rays and weaken validation.
- Returning raw evaluated vertices and faces was rejected because response size and
  Blender main-thread time would be unbounded.
- A custom Blender editor was rejected because ordinary Python add-ons cannot define a
  new native editor type and a custom Blender build is outside this project's scope.

## Consequences

Protocol 1 remains valid, while `viewport_capture` advances to capability version 3
and new `viewport_raycast` and `geometry_inspection` capabilities are required by the
0.4 server. Capture IDs are ephemeral evidence, not persistent scene identifiers.
