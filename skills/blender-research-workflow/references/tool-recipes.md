# Blender Research MCP recipes

Use only the tools registered by `blender_research`. These sequences preserve the
current blend file and never authorize arbitrary Python.

## Read-only diagnosis

1. Call `connection.ping`; stop on a version or capability mismatch.
2. Call `context.get` and identify an exact object name.
3. Call `object.inspect` for transforms, visibility, bounds, and stable session identity.
4. Call `observation.bundle` with one to three useful views. FRONT, RIGHT, and TOP are
   the default geometry-diagnosis set.
5. Check `context_unchanged`, `object_unchanged`, image hashes, warnings, and the final
   `scene_generation` before drawing conclusions.

Use `viewport.capture` for one image. Its returned generation is already settled and
can be passed directly to `transaction.begin`.

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

## Recovery

- `CAPABILITY_MISMATCH`: install the matching add-on ZIP, re-enable it, restart the
  bridge, then ping again.
- `CAPTURE_GPU_UNAVAILABLE`: ensure Blender is open, not minimized, and has a `VIEW_3D`
  area. Do not accept a black image as evidence.
- `SCENE_UNSTABLE`: wait for playback, loading, or user editing to stop, then repeat the
  observation from the beginning.
- `OBSERVATION_CONTEXT_DRIFT` or `OBSERVATION_SCENE_CHANGED`: discard the bundle and
  re-observe. Do not combine its partial images.
- Transaction conflict: preserve user state and report the exact conflict. Never force
  rollback over a value the user changed.
- Connection loss: reconnect and ping. The add-on attempts safe rollback after its
  reconnect grace period; inspect transaction status before further mutation.
