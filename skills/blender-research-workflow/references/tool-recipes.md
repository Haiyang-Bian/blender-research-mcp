# Blender Research MCP recipes

Use only the tools registered by `blender_research`. These sequences preserve the
current blend file and never authorize arbitrary Python.

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

Do not hide an active or selected object, because that would leave context debt. Do not
add, remove, reorder, or parameterize modifiers, and do not write Basis or driven shape
keys.

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

Any candidate failure stops the sequence. Do not combine partial images with an older
baseline, retry over a property conflict, or manually force the original value.

## Recovery

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
- Connection loss: reconnect and ping. The add-on attempts safe rollback after its
  reconnect grace period; inspect transaction status before further mutation.
