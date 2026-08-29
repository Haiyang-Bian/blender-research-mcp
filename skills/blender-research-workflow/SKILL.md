---
name: blender-research-workflow
description: Launch Blender, manage .blend projects, inspect and diagnose scenes, author bounded static objects and Principled materials, and produce reviewed Eevee renders through Blender Research MCP. Use for application/project lifecycle, scene evidence, reversible LookDev, local textures, World/Camera setup, or static scene delivery; do not use for arbitrary Python, arbitrary node graphs, animation, or mesh-component editing.
---

# Blender Research Workflow

Use the semantic `blender_research` MCP as the source of truth for live Blender state.

## Follow application and project intent

Treat application launch and project opening as separate decisions:

1. When the user wants Blender started, call `application.status`; call
   `application.launch` only when `running=false`. Never pass a project path to launch.
2. When the user wants a project opened, call `application.status`, launch if needed,
   then call `project.open` with the exact absolute `.blend` path.
3. A direct request to save, switch, reload, or close is already authorization. Execute
   the corresponding lifecycle chain without asking the same question again.

`project.open` and `application.quit` save the current dirty project by default and
commit an active transaction first. `project.reload` discards unsaved changes by
default. Follow explicit `save_current=false/true`, `save_current_as`, `use_scripts`, or
`load_ui` intent when the user specifies it. Project tools never launch Blender
implicitly; handle `APPLICATION_NOT_RUNNING` with the separate status/launch sequence.

After selecting the application/project state:

1. Call `connection.ping` before relying on scene tools. Require protocol `1`, a
   capability-compatible add-on, `viewport_capture >= 3`, `viewport_raycast >= 1`,
   `geometry_inspection >= 1`, `lookdev_inspection >= 1`, and
   `transactions >= 2`.
2. Call `context.get` and use exact object names. Never infer live connectivity from
   tool registration alone.
3. For visual diagnosis, prefer `observation.bundle` with the smallest useful set of
   views. Use `object.inspect` or `object.geometry.inspect` when structured state
   answers the question without an image.

Viewport capture requires a `VIEW_3D` area. Treat `CAPTURE_GPU_UNAVAILABLE` as a failed
evidence capture, not permission to use desktop automation or raw Python.

## Follow scene-authoring intent

When the user asks to build or modify a static scene, that request authorizes the
complete in-memory authoring chain. Inspect exact scene/resource identities, begin one
structural transaction, execute the bounded object/material/image/World/Camera writes,
render the smallest useful preview, and commit when the structured and visual checks
succeed. Do not stop for per-object or per-material confirmation. On any write,
preview, context, property, or structure conflict, roll the whole transaction back and
report the preserved state.

Authoring requires `transactions >= 3` plus the capability for each requested domain.
Use `scene.inspect`, `image.inspect`, and the extended `material.inspect` rather than
guessing names, slots, nodes, links, users, or session identities. Use only the exposed
primitive, Principled PBR channel, local-image, World, Camera, and Eevee tools; do not
substitute arbitrary Python or generic node/RNA operations.

`transaction.commit` retains the successful scene only in Blender memory. Call
`project.save` when the user asked to save or deliver a `.blend`. Call `render.save`
only for an explicit image export path; a failed export may be retried after commit
without repeating the scene transaction.

## Prefer the unified object setting entry

After `object.inspect`, use one `object.set` when the same object needs a coherent
combination of transform, visibility, Light, or Camera property changes. Supply every
exact object/data identity, type, and user count from that inspection. Shared
Light/Camera data is a broader edit; set `allow_shared_data=true` only when the user's
requested scope includes every user.

Keep structure and scene ownership separate: use `object.create/duplicate/delete` for
object structure and `scene.camera.set` for the active Camera. Materials, World,
Modifiers, images, lifecycle, and renders retain their own tools. If the connected
add-on lacks `object_settings: 1`, use compatible legacy transform/visibility tools
where they cover the request; do not invent `light.set`, `camera.set`, or generic RNA.

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

Use `lookdev.compare` when the decision needs one to three absolute candidates for one
already inspected property. Supply every exact target identity, preserve candidate
order, and choose one evidence object/view that makes the difference reviewable. The
tool must return the baseline first, restore after every candidate, and finish with all
three restoration flags true. Treat visually indistinguishable candidates as evidence,
not failure. Comparison never chooses, commits, or saves a result; after the user picks
a direction, apply it through a new ordinary transaction.

For transform, visibility, Light, or Camera alternatives, prefer the typed
`object_setting` comparison target when `object_settings: 1` is available. It uses the
same inspected scope and `object.set` writer for every independently rolled-back
candidate.

Read [references/tool-recipes.md](references/tool-recipes.md) when executing an
application/project lifecycle, multi-step scene authoring, material/texture binding,
rendering, observation, comparison, preview, reconnect, or recovery workflow.
