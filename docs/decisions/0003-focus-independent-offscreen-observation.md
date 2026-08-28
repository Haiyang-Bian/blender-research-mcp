# 0003 — Focus-independent off-screen observation

- Status: accepted
- Date: 2026-08-29

## Context

The first viewport implementation used `bpy.ops.screen.screenshot_area`. On the
Windows Blender 4.2.23 integration fixture it produced identical black images when
Blender was minimized or left in the background. Window pixels are therefore not a
safe source of visual evidence for an autonomous local workflow.

Multiple semantic views also need one consistency contract. Returning images captured
from different scene or user-context states would make visual comparison misleading.

## Decision

- Render the selected `VIEW_3D` through `gpu.types.GPUOffScreen` on Blender's main
  thread and encode the RGBA buffer as a bounded PNG inside the add-on.
- Do not use desktop pixels, temporary screenshot files, or a silent
  `screenshot_area` fallback.
- Guarantee capture while Blender is running with a `VIEW_3D` area even when another
  window has focus or obscures it. Minimized Blender is not a compatibility guarantee.
- Return `CAPTURE_GPU_UNAVAILABLE` or `CAPTURE_BLANK` instead of accepting missing GPU
  context or an all-black image.
- Negotiate `viewport_capture` capability version 2 while retaining wire protocol 1.
- Compose `observation.bundle` in the external server as sequential capture requests,
  allowing Blender's main-thread timer to run between views.
- Require stable scene generation plus identical context and object state around a
  successful bundle. Duplicate image hashes are warnings because symmetric geometry
  can legitimately produce identical views.

## Alternatives

- OS/editor screenshots were rejected because their result depends on window
  visibility and focus.
- A temporary camera and full render were rejected because they change more scene
  state and are unnecessarily expensive for geometric inspection.
- One monolithic three-view Blender command was rejected because it would block the
  main thread for all three draws without yielding between them.

## Consequences

Blender must still own a valid GPU context and at least one `VIEW_3D`. Clients can use
the final bundle generation directly for a transaction. Older 0.2 add-ons are rejected
with `CAPABILITY_MISMATCH` instead of silently providing focus-dependent images.
