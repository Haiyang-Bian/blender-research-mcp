---
name: blender-research-workflow
description: Launch Blender, manage .blend projects, inspect and diagnose scenes, author bounded static objects, revision-aware Mesh topology, UV layouts, skin weights, materialized or extracted Mesh modules, exact Armature bindings, separated Mesh branches and surface fits, typed Modifier stacks, and Principled materials, and produce reviewed Eevee renders through Blender Research MCP. Use for application/project lifecycle, scene evidence, reversible LookDev, local textures, World/Camera setup, semantic Mesh modeling, evaluated-surface fitting, UV/weight authoring, modular Mesh assembly, non-destructive Modifier modeling, or static scene delivery; do not use for arbitrary Python, arbitrary BMesh/RNA, arbitrary node graphs, animation, Modifier apply, generic attributes, or retopology.
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

When `transactions >= 5`, treat user navigation, display, selection, and active-object
changes as collaborative UI, not transaction conflicts. Preserve them through rollback.
If native Blender save accepts the transaction, stop writing, comparing, rolling back,
or saving again; the user's saved visible state is final for that operation.

## Follow scene-authoring intent

When the user asks to build or modify a static scene, that request authorizes the
complete in-memory authoring chain. Inspect exact scene/resource identities, begin one
structural transaction, execute the bounded object/material/image/World/Camera writes,
render the smallest useful preview, and commit when the structured and visual checks
succeed. Do not stop for per-object or per-material confirmation. On any write,
preview, hard-context, property, or structure conflict, roll the whole transaction back
and report the preserved state. User view, display, selection, and active-object changes
are not hard-context conflicts with transaction capability 5.

Authoring requires `transactions >= 3` plus the capability for each requested domain;
exact component editing requires `transactions >= 4` and `mesh_topology: 1`.
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

## Choose Modifier authoring deliberately

Use `modifier.inspect` and the typed `modifier.create/set/move/delete` tools when a
supported Bevel, Subdivision, Solidify, or Boolean can express a non-destructive
whole-object effect. Keep Modifier structure separate from `object.set`, and carry the
exact object/Modifier identities, index, type, and complete stack fingerprint from the
latest inspection. Use `modifier.set_state` only for legacy viewport/render flags.

Do not use a Modifier as a substitute for requested vertex/edge/face editing or UV
work, and do not apply it: use exact Mesh authority for real component changes and the
typed UV domain for mapping work. If
`modifier_authoring: 1` is unavailable, retain compatible older tools and report the
new-domain boundary rather than falling back to arbitrary RNA or Python.

## Choose material, Modifier, or Mesh authority

Use material tools for visual surface detail such as water ripples, roughness, color,
and emission when the silhouette and real topology should remain unchanged. Use typed
Modifiers for reversible whole-object Bevel, Subdivision, Solidify, or Boolean effects.
Use `mesh.inspect` plus `mesh.edit` only when the user's intent requires a real local
silhouette, structural, or vertex/edge/face change.

When `mesh_selection: 1` is available, prefer a revision-bound SelectionSet for a
semantic, screen-derived, weighted, repeated, or large region. Use a SurfaceRef when
the task depends on BASE or evaluated Shape-Key/Armature/Modifier geometry. Require
transaction capability 6 for topology-preserving selection deformation, carry the
returned rebound SelectionSet after each changed write, and quantify fit with surface
query/validation before commit. UI navigation and Blender object selection do not
invalidate these resources; actual Mesh, user, transform, frame, or evaluated-geometry
drift does.

Mesh component indices are evidence scoped to the exact full `mesh_fingerprint` that
reported them. With `mesh_component_map: 1`, use each changed topology response's
one-revision ComponentMap and rebound SelectionSet for the next operation; never infer
new indices by location. With `mesh_component_map: 2`, compose only an exact continuous
Map chain. Use `mesh.separate` when a connected face region must become an independent
object branch. Use `mesh.batch.execute` when dependent selection/edit/separation/
validation steps benefit from named intermediate resources and automatic remapping;
do not use it as a generic scene script. Explicitly choose `OBJECT` when only
the target object should leave shared
Mesh data, or `SHARED_DATA` when all inspected users should change together. Do not use
topology to imitate a material effect, edit UVs through Mesh operations, or fall back to
arbitrary BMesh, RNA, or Python.

Use `mesh.uv.inspect/edit` for UV layers, seams, pins, corner coordinates, island
transforms, unwrap, and pack. Use `mesh.weights.inspect/edit` for Vertex Group schema
and deform weights. For topology changes, keep the default preserve/interpolate policy
unless the user's intent requires an explicit reject-or-discard policy; choose SOURCE
and SEPARATED policies independently. Use `mesh.attribute.transfer` when exact lineage
or a bounded nearest mapping is better than authoring values directly, then validate
UV or weight evidence before commit. Shape-Key Meshes may receive topology-stable UV or
weight edits, but not topology changes.

When a source must remain intact, use `mesh.materialize` to create an independent
working object from explicit BASE, SHAPE_KEYS_CURRENT, or FINAL_EVALUATED evidence.
Use `mesh.extract` for a logical module made of multiple disconnected face components;
keep `mesh.separate` for exactly one connected region. Transfer or repair weights with
the 0.14 tools first, then use `rig.bind` only to assemble verified groups against one
exact Armature. Never assume FINAL_EVALUATED is a reusable rest Mesh after Armature
deformation has been baked.

When one FACE region contains many disconnected shells, use a revision-bound
ComponentCatalog to review compact component metrics and materialize SelectionSets only
for chosen identities. Use exact Collection link and object-parent tools for isolated
organization changes. Prefer `mesh.batch.execute` v3 when materialize, catalog, extract,
organization, and rig binding form one atomic assembly chain; review its response-only
assembly manifest and never treat it as persistent project metadata.

When an editable template lives in an external local `.blend`, inspect its SHA-bound
Library catalog first and append one exact Object, Collection, or Mesh root as local
data. Use `mesh.materialize` instead when the source already exists in the current
scene. After append, align the object before creating SelectionSets or SurfaceRefs;
fit only visible high-confidence anchors and preserve the template/cage prior where
source geometry is hidden. Keep weight transfer and `rig.bind` as separately verified
steps. Prefer batch v4 when append, alignment, dynamic surfaces, fitting, organization,
weights and binding must succeed or roll back as one chain.

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

If a hard-context, property, generation, or idempotency conflict occurs, stop. Do not force
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

For one supported Modifier parameter, use `modifier_setting` when
`modifier_authoring: 1` is available. Never encode Boolean operand changes, stack
creation/deletion, or reordering as a comparison candidate.

Read [references/tool-recipes.md](references/tool-recipes.md) when executing an
application/project lifecycle, multi-step scene authoring, exact Mesh editing,
SelectionSet/surface fitting, revision-aware topology, Modifier-stack authoring,
UV/weight authoring, materialize/extract/rig assembly, rendering, observation,
ComponentCatalog/cross-object assembly, comparison, preview, reconnect, or recovery
workflow.
