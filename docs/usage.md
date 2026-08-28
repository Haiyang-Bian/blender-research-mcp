# Using Blender Research MCP

## Start and connect

1. Install `artifacts/blender-research-mcp-addon-0.3.1.zip` in Blender 4.2.23 and
   enable **Blender Research MCP**.
2. Keep the blend file open with at least one 3D Viewport.
3. Configure the `blender_research` STDIO MCP as shown in the repository README and
   restart Codex after changing its MCP configuration.
4. Call `connection.ping`. Protocol 1 and `viewport_capture: 2` are required.

Blender does not need focus for semantic operations or off-screen capture. It may be
behind another window. Minimized capture is not guaranteed; restore the Blender window
if `CAPTURE_GPU_UNAVAILABLE` is returned.

## Observe a target

Use exact object names from `context.get` or other verified scene metadata.

- `object.inspect` returns type, transforms, visibility, bounds, and session identity.
- `viewport.capture` returns one focus-independent image and a settled scene generation.
- `observation.bundle` returns one to three ordered images, defaulting to FRONT, RIGHT,
  and TOP, plus before/after context and object evidence.

A successful bundle has `context_unchanged: true`, `object_unchanged: true`, and equal
start/end scene generations. A duplicate-view warning is diagnostic, not automatically
an error.

## Preview a supported scale change

1. Observe or inspect the object and retain the latest `scene_generation`.
2. Begin a transaction with that generation and a unique idempotency key.
3. Call `object.transform` with an absolute partial `scale` patch and a new key.
4. Capture evidence; use the returned settled generation for the final transaction
   command.
5. Roll back unless the result should explicitly remain in Blender memory. Commit does
   not save the blend file.

Only one transaction may be active. Conflicts protect user context and properties; no
force operation exists.

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

- `CAPABILITY_MISMATCH`: install and restart the matching 0.3 add-on.
- `CAPTURE_GPU_UNAVAILABLE`: restore the Blender window and confirm a 3D Viewport exists.
- `CAPTURE_BLANK`: discard the image; it is not valid evidence.
- `SCENE_UNSTABLE`: stop playback/loading/editing and restart the observation.
- `OBSERVATION_CONTEXT_DRIFT` or `OBSERVATION_SCENE_CHANGED`: discard the whole bundle
  and capture it again.
- Transaction conflict: preserve the user's state and inspect before attempting any
  further mutation.
