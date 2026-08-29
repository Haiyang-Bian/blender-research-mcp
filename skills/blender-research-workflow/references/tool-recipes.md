# Blender Research MCP recipes

Use only the tools registered by `blender_research`. Lifecycle tools may intentionally
save or replace the current blend file when that matches the user's request; they never
authorize arbitrary Python.

## Application and project lifecycle

Choose the chain from user intent rather than combining tools implicitly:

- **Start Blender only:** call `application.status`; if `running=false`, call
  `application.launch`. Stop there unless the user also asked to open a project.
- **Open or switch project:** call `application.status`, launch if needed, then call
  `project.open(path)` with the user's exact absolute existing `.blend` path.
- **Save:** call `project.save()`. For an untitled project, provide the absolute target
  path the user supplied; a different path is Save As and becomes current.
- **Reload:** call `project.reload()`. Its default discards unsaved changes. Use
  `save_current=true` only when requested or when the user explicitly wants the edits
  preserved before reload.
- **Quit:** call `application.quit()`. It saves dirty current state by default. Use
  `save_current=false` when the user asked to close without saving.

The user's explicit save/open/switch/reload/close request is sufficient authorization;
do not insert another confirmation. `project.open` and `application.quit` commit an
active transaction and save a dirty current project by default. A dirty untitled
project requires `save_current_as`; use the provided absolute path rather than
inventing one.

Opening and Save As paths may be anywhere accessible; there is no project-root
allowlist. Open targets must exist and Save As parents must exist. Default
`use_scripts=true` and `load_ui=true` honor trusted project scripts and saved UI. Pass
false only when the caller requests that behavior.

After an open or reload, accept success only after the tool returns a verified final
`project.status` whose absolute path matches the target. `already_open` is a successful
no-op; call reload when the user wants an actual disk reload. If a lifecycle call times
out, inspect `application.status` or `project.status.last_operation` before retrying.

## Read-only diagnosis

1. Call `connection.ping`; stop on a version or capability mismatch.
2. Call `context.get` and identify an exact object name.
3. Call `object.inspect` for transforms, visibility, bounds, and stable session identity.
4. Call `object.geometry.inspect` when evaluated mesh counts, topology, materials, or
   modifiers can answer the question without an image.
5. Call `observation.bundle` with one to three useful views. FRONT, RIGHT, and TOP are
   the default geometry-diagnosis set.
6. Check `context_unchanged`, `object_unchanged`, image hashes, warnings, and the final
   `scene_generation` before drawing conclusions.

When the question involves writable LookDev state, call `object.lookdev.inspect` after
identifying the exact object. It reports authoritative target identities and bounded
visibility, modifier, shape-key, and material-slot state; names from memory are not
write authority.

Use `viewport.capture` for one image. Its returned generation is already settled and
can be passed directly to `transaction.begin`.

## Image-to-geometry localization

1. Call `viewport.capture` with an explicit semantic view and the least expensive
   useful display mode. Use SOLID or WIREFRAME before RENDERED when material evidence
   is unnecessary.
2. Retain the returned image, `capture_id`, hash, coordinate-space declaration, and
   settled generation as one evidence unit.
3. Choose normalized top-left `x/y` coordinates on that exact image and call
   `viewport.raycast` with its `capture_id`.
4. Treat `hit: false` as a valid geometric miss. On a hit, use the exact returned
   object name for `object.inspect` or `object.geometry.inspect`.
5. Remember that the ray returns the first evaluated geometric surface, not the first
   opaque rendered surface.

For a non-axis view, use an absolute orbit on a single capture. Do not convert it into
incremental viewport operations or leave selection/view changes behind.

## Reversible scale preview

1. Start from the latest settled `scene_generation` returned by an observation.
2. Call `transaction.begin` with that generation, a unique idempotency key, and a short
   label.
3. Call `object.transform` with the transaction ID, an absolute partial `scale` patch,
   the transaction's returned generation, and a new idempotency key.
4. Inspect the returned before/after values and capture the smallest useful evidence.
5. Use the observation's final generation for `transaction.rollback`, unless the user
   explicitly asks to retain or accepts the result; only then use `transaction.commit`.
6. Inspect the object and context after rollback. Do not describe the rollback as
   successful without verifying both.

Never reuse an idempotency key for a different payload. Do not convert an absolute
scale request into an incremental expression.

## Reversible object-local LookDev preview

1. Call `object.lookdev.inspect` and retain the exact object identity plus the chosen
   modifier or shape-key identity when applicable.
2. Choose one property and one absolute value. Visibility is limited to
   `hide_viewport` / `hide_render`; modifier state to `show_viewport` / `show_render`;
   shape keys to non-Basis, undriven values inside the reported slider range.
3. Begin a transaction from the latest settled generation and call the matching writer
   with a separate idempotency key and all inspected identities.
4. Confirm structured before/after evidence. Capture an image only when the property is
   expected to have a visual effect.
5. Roll back by default, then re-inspect the property and user context. Commit only
   after explicit intent to retain the in-memory preview.

Do not hide an active or selected object, because that would leave context debt. This
legacy recipe changes only Modifier viewport/render state; use the typed stack recipe
below for supported creation, ordering, deletion, or parameters. Do not write Basis or
driven shape keys.

## Reversible material-input preview

1. Use `object.lookdev.inspect` to choose an exact material slot, then call
   `material.inspect` for that object and slot.
2. Choose one socket with `writable: true`. Retain the exact object, material, node, and
   socket identities, socket identifier, type, range, current material user count, and
   affected object list.
3. Prefer a single-user material. If `material_users > 1`, stop unless the user intends
   all affected users to change. Only then pass the exact `expected_material_users` and
   `allow_shared=true`.
4. Begin a transaction and call `material.set_input` with a value of the inspected
   type: Boolean, Int, finite Float, three-float Vector, or four-float Color. Do not
   clamp or convert it.
5. Capture the smallest useful evidence, then roll back by default and verify the
   socket value. Commit retains memory only and does not save the file.

Never write a linked, driven, read-only, unsupported, or library-linked socket. Do not
rewire nodes or create a single-user material as a workaround.

## Comparative LookDev review

1. Inspect one exact writable target. For material inputs, inspect the exact slot and
   retain object, material, node, socket, user-count, type, and range evidence.
2. Choose one to three unique absolute candidates of the exact target type. Do not use
   relative expressions, implicit conversion, clamps, or a value equal to the baseline.
3. Call `lookdev.compare` with the closed target union, ordered labeled candidates, and
   one evidence capture specification. A Boolean target accepts only the opposite value.
4. Review content in order: baseline, then each candidate. Match images to
   `content_index`; inspect hashes, writer/rollback results, and difference statistics.
5. Accept the comparison only when `context_unchanged`, `object_unchanged`, and
   `target_restored` are all true. An indistinguishable warning means the requested
   change did not escape the rendered-noise threshold; it is not an automatic winner.
6. Ask the user to select a direction. If they want it retained, start a new ordinary
   transaction and apply that exact value; comparison itself never commits or saves.

For `object_setting`, choose exactly one locator:

- transform channel plus axis;
- visibility property;
- Light property plus exact data identity/users/type/shared scope;
- Camera property plus exact data identity/users/type/shared scope.

Numeric candidates are finite floating-point values in the inspected range. A Boolean
locator accepts only the value opposite the baseline. Area shape values are exact enum
strings. Light colors are `#RRGGBB`; hexadecimal case variants are equivalent, and the
rollback guard compares Blender's linear RGB while the report preserves the submitted
string.

For `modifier_setting`, retain the exact object identity, Modifier identity/type/index,
and `stack_fingerprint` from `modifier.inspect`, then select one writable typed field.
Numeric and integer candidates must stay inside the reported range; Boolean candidates
contain only the opposite baseline; enum values are exact. Do not compare Boolean
operands, create/delete, or ordering.

Any candidate failure stops the sequence. Do not combine partial images with an older
baseline, retry over a property conflict, or manually force the original value.

## Intent-driven static scene authoring

For a user request to build or materially revise a static scene:

1. Establish the requested application/project state, then call `scene.inspect` for
   objects, collections, materials, images, World, Camera, and render state as needed.
2. Begin one transaction with `transactions >= 3` and a task label. Generate one UUID
   per logical writer and reuse only that UUID if transport retries the same payload.
3. Create and place objects, then create/load/assign materials and images, set World
   and active Camera. After each response, use its returned generation and exact new
   identities for the next write.
4. Re-inspect the smallest decisive subset. Call `render.preview` from an exact Camera
   when final-camera evidence matters; otherwise use a bounded viewport observation.
5. If every structural, context, and image check succeeds, commit automatically. The
   original creation/modification request is the retention decision; do not ask for a
   confirmation after every primitive or material.
6. On any conflict or failed preview, stop subsequent writes and roll back the whole
   transaction. Verify scene/resource state after rollback before starting over.
7. After commit, call `render.save` for requested PNG/EXR deliverables. Call
   `project.save` only when the user asked to save or deliver the `.blend` project.

Do not combine unrelated artistic alternatives in one transaction. A complete coherent
scene build may use many writes, up to the transaction's 256-delta bound.

## Object authoring

- Use `object.create` only for plane/grid/cube, UV or ico sphere, cylinder, cone,
  Empty, Camera, and point/sun/spot/area lights. Names must be unique. When targeting a
  non-root collection, use the exact collection identity returned by `scene.inspect`.
- Initial and later transforms are absolute Blender-unit location, XYZ Euler degrees,
  and local scale. Location/rotation writes require the exact object identity.
- Use `object.duplicate(linked_data=false)` for independent object data or
  `linked_data=true` only when the requested objects should share edits. Preserve the
  returned data identity and user count.
- `object.delete` unlinks during the transaction and removes the object only at commit.
  It rejects the active or selected object because that would invalidate the captured
  user context; change selection explicitly outside the authoring transaction if the
  user's request requires that deletion.
- Do not approximate unsupported topology with arbitrary mesh-component edits. Compose
  supported primitives or report the current boundary.

## Unified object settings

Use `object.inspect` immediately before configuring an existing object. Retain the
object identity and, for a Light or Camera, the data identity, users, concrete data
type, writable fields, and ranges.

1. Begin or continue one transaction at the returned generation.
2. Build one `object.set` request with one to four non-repeated patches. Combine fields
   when they form one coherent object setting: for example Camera transform plus lens,
   or Area-light transform plus color, energy, shape, and size.
3. Use absolute Blender units, XYZ Euler degrees, and local scale. Light colors use
   `#RRGGBB` sRGB; Point/Spot radius, Area shape/size, Spot cone/blend, and Sun angle are
   available only when reported for the inspected type.
4. For Camera data, use lens/sensor width only with `PERSP`, ortho scale only with
   `ORTHO`, and keep clip end greater than clip start. Projection type and DOF are not
   writable.
5. When data users exceed one, verify the requested scope includes all users and pass
   the unchanged count plus `allow_shared_data=true`. Re-inspect on any identity, type,
   or user-count mismatch.
6. Check the returned path-sorted changes, object summary, delta count, and delta kinds.
   A `changed=false` response is a successful no-op and does not advance generation.

The request validates all patches before writing and applies transform, visibility,
then data as one generation step. On `OBJECT_SETTINGS_APPLY_FAILED`, verify the object
before retrying. On `OBJECT_SETTINGS_RESTORE_FAILED`, stop the transaction workflow and
inspect current object, data, context, and transaction state; never force a guessed
baseline.

`object.set` does not create/delete objects, choose the active Camera, change World or
materials, edit Modifier parameters, or expose arbitrary RNA. Use the corresponding
semantic tool for each of those responsibilities.

## Typed Modifier stack authoring

Use this flow for Bevel, Subdivision, Solidify, or Boolean on an exact Mesh object:

1. Call `modifier.inspect(object_name)`. Retain object identity, full ordered stack,
   every relevant Modifier identity/type/index, and `stack_fingerprint`. Treat a
   truncation warning as insufficient authority to mutate the stack.
2. Begin or continue a structural transaction. Pass the latest fingerprint to exactly
   one `modifier.create`, `modifier.set`, `modifier.move`, or `modifier.delete` call,
   then carry its returned generation and after-fingerprint to the next stack write.
3. Use `modifier.create` with a unique name. A null stack index appends; an integer
   inserts at that exact position. Use only the four supported definitions.
4. Use `modifier.set` for a non-empty partial patch. Final-state constraints still
   apply: Subdivision stays within the reported face budget, Solidify rim-only retains
   rim, and Boolean solver-specific flags/threshold remain valid.
5. Use `modifier.move` for an exact identity and target index. It may cross unsupported
   items, but never configure them.
6. Use `modifier.delete` only when the user wants the item removed. It is disabled and
   marked pending until commit; rollback must restore the same identity, index, and
   viewport/render state.
7. Re-inspect and preview the smallest useful evidence. Commit a requested coherent
   modeling change after verification; otherwise rollback and verify the original full
   fingerprint. Commit remains memory-only until `project.save` is requested.

For Boolean, inspect the operand object separately and send its exact Mesh identity.
Never use the owner as its operand. Direct or transitive Boolean cycles are invalid.
FAST supports `double_threshold`; EXACT supports self and hole-tolerant options. A
budget rejection is a deliberate bound, not permission to bypass the tool.

The stack guard protects order, identity, type, public visibility, typed settings,
operand, and pending-delete state. On `MODIFIER_STACK_CONFLICT`, preserve the user's
current value/order and start from a fresh inspection. On a create/set/move restoration
failure, stop the transaction; do not reconstruct or force the old stack through Python.

These tools do not apply Modifiers. Use them for non-destructive whole-object effects,
not as a substitute for direct mesh-component or UV operations.

## Principled materials and local textures

1. Create a canonical material with `material.create`; colors may be `#RRGGBB` sRGB or
   explicit linear RGBA. Retain the returned material and Principled node identities.
2. Inspect object/data identities and user count. Use `material.assign` append, replace,
   or clear with the exact slot guard. Set `allow_shared_data=true` only when the
   requested scope includes every object sharing that mesh data.
3. Load an arbitrary absolute local file with `image.load`. Use `AUTO` unless semantic
   meaning calls for `SRGB` color data or `NON_COLOR` numeric/normal data. Retain image
   identity, users, path, dimensions, and color space from `image.inspect`.
4. Call `material.inspect` again and choose the exact Principled node/channel. Bind only
   base color, roughness, metallic, normal, bump, emission, or alpha through
   `material.texture.bind` with UV or Generated coordinates and bounded mapping.
5. Do not overwrite an existing incoming link by default. To replace it, pass
   `replace_existing=true` and the complete inspected `incoming_link_identities` set.
   Use the same exact set for `material.texture.clear`; rollback restores those links.

These tools do not download, pack, unpack, or reload assets and do not expose arbitrary
shader nodes. Shared materials require the exact current material user count and the
requested scope before `allow_shared=true`.

## World, Camera, and reviewed renders

- `world.set` creates a World when the scene has none. Modifying an existing World
  requires its exact identity/users; shared intent permits `allow_shared=true`.
  Environment images use exact image identity/users and optional Z rotation. An
  unsupported pre-existing World graph returns a link conflict instead of rewiring it.
- `scene.camera.set` takes one exact Camera object and restores the previous active
  Camera on rollback.
- `render.preview` temporarily uses Eevee Next at 256–1000 pixels and 1–64 samples,
  returns a validated PNG/hash, and restores the active Camera and all render settings.
  A preview is evidence, not an automatic aesthetic ranking.
- `render.save` overwrites an absolute `.png` or `.exr` whose parent exists. It restores
  temporary render settings and returns the actual file hash and byte count. Export is
  independent from `.blend` saving and can be retried after a committed scene build.

## Recovery

- `APPLICATION_NOT_RUNNING`: call `application.status`, then launch only if the user's
  intent requires Blender to run; retry the project operation afterward.
- `BLENDER_EXECUTABLE_NOT_CONFIGURED` or `BLENDER_EXECUTABLE_NOT_FOUND`: report the
  required CLI/environment/PATH configuration. Do not turn project.open into a hidden
  process launcher.
- `APPLICATION_LAUNCH_FAILED` or `APPLICATION_LAUNCH_TIMEOUT`: report PID, launch ID,
  and log path. Do not create an unbounded retry loop.
- `CURRENT_PROJECT_UNTITLED`: use the user's absolute `path` or `save_current_as` and
  repeat the requested save/open/quit chain.
- `PROJECT_SAVE_FAILED`: the following open/quit was not scheduled; resolve the save
  failure before retrying the original intent.
- `PROJECT_OPEN_FAILED`, `PROJECT_OPEN_TIMEOUT`, or `PROJECT_PATH_MISMATCH`: inspect the
  actual filepath and `last_operation`; do not report a switch that was not verified.
- `PROJECT_RELOAD_UNAVAILABLE`: save the untitled project or open a named project first.
- `APPLICATION_QUIT_TIMEOUT`: inspect status; do not claim Blender exited while its PID
  or instance manifest remains.
- `CAPABILITY_MISMATCH`: install the matching add-on ZIP, re-enable it, restart the
  bridge, then ping again.
- `CAPTURE_GPU_UNAVAILABLE`: ensure Blender is open, not minimized, and has a `VIEW_3D`
  area. Do not accept a black image as evidence.
- `SCENE_UNSTABLE`: wait for playback, loading, or user editing to stop, then repeat the
  observation from the beginning.
- `OBSERVATION_CONTEXT_DRIFT` or `OBSERVATION_SCENE_CHANGED`: discard the bundle and
  re-observe. Do not combine its partial images.
- `CAPTURE_NOT_FOUND`, `CAPTURE_STALE`, or `CAPTURE_CONTEXT_STALE`: discard both the
  old image and its coordinates, then capture again.
- `GEOMETRY_DIAGNOSTICS_TRUNCATED`: use the returned counts and bounds; do not seek raw
  mesh arrays or fall back to unrestricted Python.
- Transaction conflict: preserve user state and report the exact conflict. Never force
  rollback over a value the user changed.
- `COMPARISON_RESTORE_FAILED`: stop all further writes and re-inspect the target,
  active transaction, user context, and evidence object before another comparison.
- `SHARED_MATERIAL_CONFIRMATION_REQUIRED`: report the user count and affected objects;
  obtain intent before a shared preview.
- `MATERIAL_USERS_CONFLICT`: re-inspect the material rather than reusing stale scope.
- Material socket link, driver, read-only, type, or range failure: choose a different
  inspected writable input or report the boundary; do not rewire nodes or use Python.
- `STRUCTURE_CONFLICT`: preserve user edits, stop the batch, and inspect the affected
  identity, fingerprint, links, users, and transaction status before another attempt.
- `MODIFIER_STACK_CONFLICT`: preserve the user's current Modifier values and order;
  re-run `modifier.inspect` and rebuild the remaining stack plan from its fingerprint.
- Modifier target/type/index, name, driver/read-only, Boolean operand/cycle, or geometry
  budget failure: correct the typed request from current evidence; do not use RNA.
- `MODIFIER_SETTINGS_RESTORE_FAILED`, `MODIFIER_CREATE_RESTORE_FAILED`, or
  `MODIFIER_MOVE_RESTORE_FAILED`: stop further writes and inspect the entire stack and
  transaction; do not force an assumed baseline.
- Object/material/image/collection identity or user-count conflict: re-run the relevant
  inspect tool and rebuild the remainder of the plan from current state.
- `MATERIAL_LINK_CONFLICT` or `MATERIAL_LINK_IDENTITY_MISMATCH`: re-inspect the complete
  Principled incoming-link set; never guess or partially replace it.
- `RENDER_FAILED`, `RENDER_RESULT_INVALID`, or `RENDER_BLANK`: roll back an uncommitted
  authoring batch. Re-inspect Camera, World, visibility, and render evidence rather than
  accepting a missing or uniform image.
- Render output path/save failure after commit: keep the committed in-memory scene and
  retry only the explicit export after correcting the absolute path or parent.
- Connection loss: reconnect and ping. The add-on attempts safe rollback after its
  reconnect grace period; inspect transaction status before further mutation.
