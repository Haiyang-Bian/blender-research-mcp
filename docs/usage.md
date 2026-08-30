# Using Blender Research MCP

## Start and connect

1. Configure the `blender_research` STDIO MCP as shown in the repository README.
2. For managed launch, set `BLENDER_RESEARCH_MCP_BLENDER_EXECUTABLE` or pass
   `--blender-executable`. `application.launch` also searches `PATH` last.
   On Windows, point this at a real `blender.exe` that accepts command-line arguments;
   the Microsoft Store execution alias launches Blender but drops the managed bootstrap
   environment and arguments. A manually started Store Blender session can still be
   discovered normally.
3. Call `application.status`. If `running=false` and the user wants Blender opened,
   call `application.launch`; this starts Blender but does not open a project.
4. Call `connection.ping`. Protocol 1, `viewport_capture: 3`,
   `viewport_raycast: 1`, `geometry_inspection: 1`, `lookdev_inspection: 1`,
   `transactions: 2`, and all advertised legacy bounded-write capabilities at version 1
   are required. Static authoring additionally requires `transactions: 3` and the
   relevant 0.8 authoring/render capability. Unified object configuration additionally
   requires `object_settings: 1`; typed Modifier authoring requires
   `modifier_authoring: 1`.

Manual installation remains available through
`artifacts/blender-research-mcp-addon-0.10.2.zip`. Managed launch instead materializes
the version-matched add-on and fixed bootstrap for the current session without changing
Blender preferences or the startup file.

## Manage the Blender application and project

Application launch and project opening are intentionally separate:

1. For “start Blender”, call `application.status`, then `application.launch` only if
   needed. Do not supply or infer a project path.
2. For “open this project”, call `application.status`, launch if needed, then call
   `project.open` with the user's absolute existing `.blend` path.
3. Do not repeat a confirmation after the user has explicitly asked to save, open,
   switch, reload, or close. That intent authorizes the corresponding lifecycle chain.

`application.status` returns `running=false` normally when Blender is absent. A running
session includes PID, instance and launch IDs, versions, port, managed status,
capabilities, and a project summary; the session token is never returned.

`project.status` works without a 3D Viewport and reports filepath, saved/dirty state,
scene generation, active transaction, and the last lifecycle operation.

- `project.save()` commits an active transaction and saves the current file. An
  untitled project requires an absolute `path`; a different path performs Save As and
  overwrites an existing target without a file selector.
- `project.open(path)` defaults to committing the active transaction and saving a dirty
  current project before switching. A dirty untitled current project requires
  `save_current_as`. Set `save_current=false` to switch and discard the old unsaved
  state. `use_scripts=true` and `load_ui=true` are the defaults.
- Opening the already current path returns `already_open` after any required save. Use
  `project.reload()` for a real disk reload.
- `project.reload()` defaults to discarding unsaved changes. Set `save_current=true`
  when the user wants to preserve them first.
- `application.quit()` defaults to committing and saving before closing. A dirty
  untitled project requires `save_current_as`; `save_current=false` closes without
  saving.

All file parameters must be absolute `.blend` paths. Open targets must exist; Save As
targets may be new if their parent directory exists. Paths are not restricted to a
project root. `project.*` never starts Blender implicitly and returns
`APPLICATION_NOT_RUNNING` when no session exists.

## Observe a target

Use exact object names from `context.get` or other verified scene metadata.

- `object.inspect` returns type, transforms, visibility, bounds, and session identity.
  Light and Camera objects also return typed data identity, users/shared/library state,
  current settings, writable fields, and ranges.
- `object.geometry.inspect` returns a bounded evaluated-mesh summary.
- `viewport.capture` returns one focus-independent image and a settled scene generation.
- `observation.bundle` returns one to three ordered images, defaulting to FRONT, RIGHT,
  and TOP, plus before/after context and object evidence.

A successful bundle has `context_unchanged: true`, `object_unchanged: true`, and equal
start/end scene generations. A duplicate-view warning is diagnostic, not automatically
an error.

Capture shading can be `CURRENT`, `WIREFRAME`, `SOLID`, `MATERIAL`, or `RENDERED`;
overlays can remain current or be forced on/off. A single capture can also orbit from a
semantic base view with bounded absolute yaw and pitch. Orbit is not accepted with
`CURRENT`, and bundle views remain semantic axes.

## Locate image evidence in 3D

1. Capture the smallest useful image and retain its `capture_id`, hash, dimensions,
   matrices, and scene generation.
2. Express the point of interest as normalized coordinates: `x=0` is left, `x=1` is
   right, `y=0` is top, and `y=1` is bottom.
3. Call `viewport.raycast(capture_id, x, y)`.
4. If `hit` is true, use the returned exact object name with
   `object.geometry.inspect`; `hit_target` only indicates whether the first geometric
   surface is the object originally framed by the capture.

Raycasts use evaluated geometry and do not model material transparency. A miss is a
valid result. `CAPTURE_STALE` or `CAPTURE_CONTEXT_STALE` means the image is no longer
valid spatial evidence and must be captured again.

For meshes above 250,000 polygons, geometry inspection returns counts and bounds but
omits Python-level per-polygon material, area, and edge-topology diagnostics with a
`GEOMETRY_DIAGNOSTICS_TRUNCATED` warning.

## Blender status panels

Use the compact **Research MCP** N-panel for connection, capture backend, transaction,
and error status. The complete status panel is under
**Scene Properties > Blender Research MCP**. Neither panel changes Blender areas or
workspaces. The full panel lists authorized write categories and shows the active
transaction's label, delta count, and kinds plus the current project path, dirty state,
and last lifecycle operation, without exposing the session token.

## Inspect writable LookDev targets

Call `object.lookdev.inspect` with an exact object name before any new preview. Retain
the returned object identity and select a target from its bounded results rather than
guessing names:

- visibility exposes only `hide_viewport` and `hide_render`;
- modifiers expose identities plus `show_viewport` and `show_render`; use
  `modifier.inspect` for the authoritative complete ordered stack and typed settings;
- shape keys exclude Basis and report slider ranges and driver state;
- material slots report indices, identities, library state, and current user counts.

For a material target, call `material.inspect(object_name, material_slot_index)` and
choose a socket whose `writable` field is true. Retain the exact material, node, and
socket identities, socket identifier, value range, material user count, and affected
object list. At most 256 shape keys, 64 material slots, and 256 material sockets are
returned; check `warnings` before assuming the list is complete.

## Author a bounded static scene

A direct request to create or materially revise a static scene authorizes one complete
in-memory authoring batch:

1. Call `scene.inspect` for the resource kinds needed by the request. Use exact object,
   collection, material, image, World, and Camera identities rather than remembered
   names.
2. Begin one transaction with `transactions: 3`. Each distinct writer gets one UUID;
   only a transport replay of the same payload reuses it.
3. Create supported primitives, Empty, Camera, or lights with `object.create`; use
   `object.duplicate` for linked or independent data. For subsequent coherent settings
   on one object, prefer one `object.set` request containing distinct transform,
   visibility, Light, and/or Camera patches.
4. Create a canonical Principled material, assign its exact slot, load absolute local
   images, and bind base color, roughness, metallic, normal, bump, emission, or alpha.
   `material.inspect` returns exact node/socket/link evidence; replacing a link requires
   the complete current link-identity set.
5. Set the World background/environment and active Camera when required. Shared mesh
   data, materials, World, and images retain exact identity/user-count guards.
6. Re-inspect critical resources and call `render.preview` for final-camera evidence.
   On success, commit automatically; the original scene-building request is already the
   retention intent. On any context/property/structure/link/preview failure, roll the
   whole transaction back and verify current state.
7. After commit, call `render.save` for requested absolute PNG/EXR deliverables.
   Call `project.save` only when the user requested a saved/delivered `.blend`.

Transactions contain at most 256 property plus structural deltas. `object.delete`
unlinks first, restores links on rollback, and removes the object only after commit
guards pass. The current surface does not expose arbitrary mesh editing, arbitrary
shader nodes, Geometry Nodes, unsupported Modifier parameters, apply, animation, rigs,
compositor operations, Cycles, network downloads, or image pack/unpack/reload.

`object.set` changes properties only. Continue to use `object.create/duplicate/delete`
for structure and `scene.camera.set` for the scene's active Camera. It does not replace
material, World, Modifier, image, project, or render tools.

## Author a bounded Modifier stack

Use `modifier.inspect(object_name)` immediately before editing a Mesh object's stack.
Retain the object identity, ordered item identities/types/indices, and exact
`stack_fingerprint` from that response.

- `modifier.create` adds Bevel, Subdivision, Solidify, or Boolean with a unique name and
  optional insertion index. Boolean operands require an exact other Mesh identity.
- `modifier.set` applies a non-empty typed partial patch after validating the final
  state, including solver/rim dependencies and geometry budgets.
- `modifier.move` reorders one exact identity to any legal stack index, including
  across an unsupported Modifier without editing that item's properties.
- `modifier.delete` disables and marks an item pending during the transaction; commit
  removes it, while rollback restores the same identity and public state.

Every response returns before/after fingerprints, an ordered stack summary, and
path-sorted changes. Re-inspect after a committed transaction. Do not reuse a stale
fingerprint after the user renames, adds, removes, reorders, or edits a guarded field.
The tools do not apply Modifiers or expose arbitrary RNA; direct vertex/edge/face work
belongs to the later semantic Mesh surface.

`render.preview` and `render.save` use Eevee Next, exact Camera identity, dimensions
from 256 to 1000, and 1–64 samples. Both restore the previous Camera, engine,
resolution, transparency, output settings, and sample count. Preview returns a PNG only
after dimension/hash/byte-count/nonblank validation. Save overwrites an absolute `.png`
or `.exr` whose parent directory exists and reports the actual file hash.

## Preview one supported change

1. Observe or inspect the object and retain the latest `scene_generation`.
2. Begin a transaction with that generation and a unique idempotency key.
3. Call exactly one supported writer with an absolute value, exact inspected identities,
   the transaction ID, the current generation, and a new idempotency key.
4. Inspect the structured before/after result and capture the smallest useful visual
   evidence. Use the returned settled generation for the final transaction
   command.
5. Roll back unless the result should explicitly remain in Blender memory. Commit does
   not save the blend file.

The preferred object-property writer is `object.set`. It accepts one to four
non-repeated typed patches, validates all of them before writing, applies transform then
visibility then object data, and advances generation once. Light/Camera data patches
must include the exact identity, type, and user count from `object.inspect`; set
`allow_shared_data=true` only when the requested scope includes every user.

Legacy single-property preview writers are `object.transform`, `object.visibility.set`,
`modifier.set_state`, `shape_key.set_value`, and `material.set_input`. The structural
authoring tools separately permit bounded location/rotation, fixed semantic node links,
lights, local images, and four typed Modifier families inside structural transactions;
no surface permits arbitrary modifiers/nodes/Python or implicit `.blend` saving.

Shape-key values must be finite and inside the inspected slider range; no clamp occurs.
Material input values preserve their inspected Boolean, Int, Float, three-component
Vector, or four-component Color type. Linked, driven, read-only, unsupported, or
library-linked inputs are rejected.

If `material.users > 1`, the default material write fails. To proceed, provide the
unchanged `expected_material_users` from inspection and `allow_shared=true`; review the
returned affected-object list first. This intentionally changes the same material for
all users. No automatic single-user copy is available.

Only one transaction may be active. If the user changes the same context or property,
rollback returns `CONTEXT_CONFLICT`, `PROPERTY_CONFLICT`, or
`TARGET_IDENTITY_CONFLICT` and preserves the user's value. No force operation exists.
Use a distinct idempotency key for each distinct payload; a replay of the same payload
returns the cached result.

## Compare candidates

Use `lookdev.compare` after inspecting one exact target and choosing one to three
absolute candidate values:

1. Build the matching discriminated `target` with every inspected session identity.
2. Provide unique `{label, value}` candidates of the target's exact type and range.
   The `object_setting` target uses a closed transform-axis, visibility, Light, or
   Camera locator. `modifier_setting` uses one exact object/Modifier identity, type,
   stack index/fingerprint, and comparable setting. Light colors are `#RRGGBB` sRGB
   and enum candidates use exact strings.
3. Choose one evidence object and capture view, display mode, overlays, and size.
4. Review the returned images in order: baseline, then each candidate in request order.
5. Check every candidate's writer, capture, rollback, difference statistics, and the
   final `context_unchanged`, `object_unchanged`, and `target_restored` flags.

The tool re-inspects before every candidate and uses a separate transaction for each
begin, write, capture, and rollback cycle. Boolean targets accept only the one value
opposite the baseline. Shared material inputs still require the exact user count and
`allow_shared=true`. Visually indistinguishable candidates produce a warning rather
than an error.

Shared Light/Camera data similarly requires the exact users and
`allow_shared_data=true`. Color candidates retain their submitted hexadecimal form in
the report, but equality and restoration are checked against Blender's linear RGB
value.

Modifier comparisons support typed numeric, integer, Boolean, and enum settings. They
do not compare Boolean operands, creation, ordering, or deletion. Each candidate uses
`modifier.set` in its own transaction and must restore the complete stack fingerprint.

Comparison never ranks, commits, or saves a candidate. After the operator chooses a
direction, apply that value through a new ordinary transaction. Any context, identity,
property, capture, or rollback conflict stops the remaining candidates without a force
path.

## Install the Codex workflow skill

The repository copy under `skills/blender-research-workflow` is authoritative. Install
or update its managed personal copy with:

~~~powershell
uv run --no-sync python scripts/install_codex_skill.py
uv run --no-sync python scripts/install_codex_skill.py --check
~~~

The installer refuses to overwrite an unrelated skill with the same name. Restart
Codex after the first installation so automatic skill discovery can see it.

## Common failures

- `APPLICATION_NOT_RUNNING`: call `application.status`, then launch Blender only when
  that matches the user's intent; retry the project operation afterward.
- `BLENDER_EXECUTABLE_NOT_CONFIGURED` or `BLENDER_EXECUTABLE_NOT_FOUND`: configure the
  executable through CLI, environment, or `PATH` and call `application.launch` again.
- `APPLICATION_LAUNCH_FAILED` or `APPLICATION_LAUNCH_TIMEOUT`: inspect the returned
  launch ID and log path; do not loop indefinitely.
- `CURRENT_PROJECT_UNTITLED`: provide `path` for `project.save` or `save_current_as` for
  the requested open/quit operation.
- `PROJECT_PATH_INVALID` or `PROJECT_NOT_FOUND`: use an absolute `.blend` path with the
  required existing target or parent directory.
- `PROJECT_SAVE_FAILED`: the current project was not switched or closed; correct the
  reported Blender/operator failure and retry the original intent.
- `PROJECT_OPEN_FAILED`, `PROJECT_OPEN_TIMEOUT`, or `PROJECT_PATH_MISMATCH`: inspect
  `project.status.last_operation` and the actual filepath before another switch.
- `PROJECT_RELOAD_UNAVAILABLE`: save the untitled project first or open an existing
  `.blend` file.
- `APPLICATION_QUIT_TIMEOUT`: inspect `application.status`; the server does not kill
  Blender after a timed-out semantic quit.
- `CAPABILITY_MISMATCH`: install and fully restart an add-on with the required
  capability versions; do not infer compatibility from the version string alone.
- `CAPTURE_GPU_UNAVAILABLE`: restore the Blender window and confirm a 3D Viewport exists.
- `CAPTURE_BLANK`: discard the image; it is not valid evidence.
- `SCENE_UNSTABLE`: stop playback/loading/editing and restart the observation.
- `COMPARISON_RESTORE_FAILED`: stop all writes and inspect the target, transaction, and
  user context before starting another comparison.
- `OBSERVATION_CONTEXT_DRIFT` or `OBSERVATION_SCENE_CHANGED`: discard the whole bundle
  and capture it again.
- `CAPTURE_NOT_FOUND`: the ID was evicted or the bridge/file restarted; capture again.
- `CAPTURE_STALE` or `CAPTURE_CONTEXT_STALE`: discard the old image-coordinate pair and
  capture again.
- Transaction conflict: preserve the user's state and inspect before attempting any
  further mutation.
- `SHARED_MATERIAL_CONFIRMATION_REQUIRED`: inspect the material's users and affected
  objects; set `allow_shared=true` only when the user intends a shared edit.
- `MATERIAL_USERS_CONFLICT`: re-inspect; never reuse a user count from stale evidence.
- Material socket linked/driven/read-only/type/range failure: choose a different
  inspected writable socket or revise the absolute value. Do not edit node topology or
  fall back to arbitrary Python.
- `STRUCTURE_CONFLICT`: preserve user state, stop the batch, and re-inspect identities,
  users, slots, nodes, links, World, Camera, and transaction status.
- `SHARED_OBJECT_DATA_CONFIRMATION_REQUIRED`: inspect the Light/Camera data users and
  proceed only when the user's requested scope includes all of them.
- `OBJECT_DATA_IDENTITY_MISMATCH` or `OBJECT_DATA_USERS_MISMATCH`: discard the stale
  `object.inspect` evidence and rebuild the typed patch.
- `OBJECT_SETTINGS_RESTORE_FAILED`: stop subsequent writes and inspect the object,
  data, transaction, and user context; do not force an assumed baseline.
- `MODIFIER_STACK_CONFLICT`: preserve the user's current stack and re-run
  `modifier.inspect`; never force a stale identity, index, or fingerprint.
- Modifier name/target/type/index, driver/read-only, operand/cycle, or budget failure:
  correct the typed request from fresh inspection instead of using arbitrary RNA.
- `MODIFIER_SETTINGS_RESTORE_FAILED`, `MODIFIER_CREATE_RESTORE_FAILED`, or
  `MODIFIER_MOVE_RESTORE_FAILED`: stop the transaction and inspect the complete stack
  before any further mutation.
- Object, collection, data, material, or image identity/user conflict: discard stale
  evidence and rebuild the remaining authoring steps from current inspection.
- `MATERIAL_LINK_CONFLICT` or `MATERIAL_LINK_IDENTITY_MISMATCH`: inspect the complete
  Principled incoming-link set; do not partially replace or guess links.
- `RENDER_FAILED`, `RENDER_RESULT_INVALID`, or `RENDER_BLANK`: reject the preview and
  roll back an uncommitted authoring batch. Inspect Camera, World, and visibility.
- Render output path or save failure after commit: preserve the committed in-memory
  scene and retry only the explicit export with a valid absolute path and parent.
