# Using Blender Research MCP

## Start and connect

1. Install `artifacts/blender-research-mcp-addon-0.6.0.zip` in Blender 4.2.23 and
   enable **Blender Research MCP**.
2. Keep the blend file open with at least one 3D Viewport.
3. Configure the `blender_research` STDIO MCP as shown in the repository README and
   restart Codex after changing its MCP configuration.
4. Call `connection.ping`. Protocol 1, `viewport_capture: 3`,
   `viewport_raycast: 1`, `geometry_inspection: 1`, `lookdev_inspection: 1`,
   `transactions: 2`, and all advertised bounded-write capabilities at version 1 are
   required.

Blender does not need focus for semantic operations or off-screen capture. It may be
behind another window. Minimized capture is not guaranteed; restore the Blender window
if `CAPTURE_GPU_UNAVAILABLE` is returned.

## Observe a target

Use exact object names from `context.get` or other verified scene metadata.

- `object.inspect` returns type, transforms, visibility, bounds, and session identity.
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
transaction's label, delta count, and kinds without exposing the session token.

## Inspect writable LookDev targets

Call `object.lookdev.inspect` with an exact object name before any new preview. Retain
the returned object identity and select a target from its bounded results rather than
guessing names:

- visibility exposes only `hide_viewport` and `hide_render`;
- modifiers expose identities plus `show_viewport` and `show_render`;
- shape keys exclude Basis and report slider ranges and driver state;
- material slots report indices, identities, library state, and current user counts.

For a material target, call `material.inspect(object_name, material_slot_index)` and
choose a socket whose `writable` field is true. Retain the exact material, node, and
socket identities, socket identifier, value range, material user count, and affected
object list. At most 256 shape keys, 64 material slots, and 256 material sockets are
returned; check `warnings` before assuming the list is complete.

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

Supported writers are `object.transform`, `object.visibility.set`,
`modifier.set_state`, `shape_key.set_value`, and `material.set_input`. They never
change object location/rotation, add or reorder modifiers, edit node topology, change
lights, import assets, or save files.

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
3. Choose one evidence object and capture view, display mode, overlays, and size.
4. Review the returned images in order: baseline, then each candidate in request order.
5. Check every candidate's writer, capture, rollback, difference statistics, and the
   final `context_unchanged`, `object_unchanged`, and `target_restored` flags.

The tool re-inspects before every candidate and uses a separate transaction for each
begin, write, capture, and rollback cycle. Boolean targets accept only the one value
opposite the baseline. Shared material inputs still require the exact user count and
`allow_shared=true`. Visually indistinguishable candidates produce a warning rather
than an error.

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
