# Blender 4.2.23 autonomous observation smoke

- Status: passed
- Date: 2026-08-29
- Branch: `codex/autonomous-observation`
- Run ID: `20260828T181524Z-12290527`
- Blender: `4.2.23 LTS` (Microsoft Store package)
- Add-on and external MCP server: `0.3.1`
- Local protocol: `1`
- MCP protocol: `2025-11-25`
- Capture capability: `viewport_capture: 2`
- Capture backend: `gpu_offscreen`

## Fixture and file boundary

The source fixture remained at
`C:\Users\26687\Work\projects\blender-projects\test-model.blend`. Its SHA-256
before and after the run was
`255e6c0a1730e80f2a57dc870dd51bbe45ea210546784f8a7af71b88d6014da3`.
The source repository status also remained unchanged at `main` with the
pre-existing `M test-model.blend` modification.

At the user's direction, the run reused an earlier isolated `%TEMP%` copy rather
than opening the newly prepared copy. The harness recorded that deviation and
accepted the reused file's current hash
`b5df875233506aa83d14d5f6b5d56440a37421a5bcc098cc281b6dcc6c439290`
as the live baseline. That hash was identical after the smoke. Neither the MCP
surface nor the smoke harness saved a blend file.

The tested development ZIP was
`blender-research-mcp-addon-0.3.1.zip`, SHA-256
`93f72129565a149ca81bee9585f3de7955cc15a56e3f1940a7ecfeac11154b21`.

## Focus-independent observation

- Blender PID `38888` owned the foreground baseline. During the obscured phase,
  foreground PID `15132` owned focus both before and after all captures and the
  transaction, so Blender never had to be brought forward again.
- A manual **Restart Bridge** changed the instance ID from
  `fd192bd0-c7a2-4f37-b511-d289ef2e8b53` to
  `8a362378-cb1c-48aa-b3bb-e35633e819c2`; the existing stdio session reconnected.
- The non-ASCII object `绯雪_edit_mesh` round-tripped through the context and
  inspection tools while remaining the active selected object.
- FRONT, RIGHT, and TOP images of
  `Portrait_ID_V13_SubjectFX_Sclera_L` were returned in request order at
  1000 by 504 pixels. Each used `gpu_offscreen`; visual review confirmed valid,
  distinct semantic views rather than black or corrupted buffers.
- The three-view bundle took 2486.565 ms. Heartbeat advanced from 612 to 627
  during that bundle and reached 660 by the end of the run.
- The foreground and obscured FRONT images had mean absolute difference
  `0.03828/255` and blurred structural mean difference `0.03255/255`. The maximum
  single-channel outlier was 77 because the viewport used stochastic `RENDERED`
  shading; aggregate structure, not maximum per-pixel noise, is the stable gate.

Blender must still be running with a `VIEW_3D` area. Minimization was not claimed
or tested as supported.

## Transaction and rollback

The consistent observation bundle returned scene generation `0`, and that exact
generation opened the transaction without an extra ping. The only modified
property was
`Portrait_ID_V13_SubjectFX_ScleraAperture_L.scale.z`, from
`0.9999999403953552` to the absolute value `1.08`.

Rollback restored the property to `0.9999999403953552`, returned
`status: rolled_back` and `context_restored: true`, and advanced scene generation
to `2`. The harness then verified exact equality for mode, active object,
selection, frame, camera, viewport transform, visibility, capture target state,
Unicode object state, and helper object state.

Visual post-validation on the saved evidence measured:

- before versus transformed: mean difference `16.1021`, structural difference
  `10.9014`, proving a visible preview change;
- before versus rolled back: mean difference `0.03680`, structural difference
  `0.03192`, returning to the rendered-view noise envelope.

The current smoke harness enforces both conditions: a transform must escape the
noise envelope, and rollback must return inside it.

## Defects found by live validation

The live gate rejected two incorrect intermediate implementations before this
passing run:

1. Blender Smooth View animation could continue after context restoration and
   trigger `OBSERVATION_CONTEXT_DRIFT`. Capture now disables Smooth View only
   inside its `try/finally`, restores the preference, and reports changed fields.
2. `GPUFrameBuffer.read_color()` returned a multidimensional Buffer. Reading it
   without first setting `dimensions = width * height * 4` produced colored grid
   corruption. The buffer is now explicitly flattened and size-validated before
   PNG encoding.

These failures occurred before transaction mutation and were not treated as
successful evidence.

## Evidence

Ignored local evidence is under
`artifacts/live-smoke/20260828T181524Z-12290527/`. It contains preparation data,
the foreground reference, the three obscured views, transformed and rolled-back
FRONT images, and `report.json`. The report SHA-256 is
`0ad5173354610226bad058339359f71e75ecd70ddb940893bc4f931d0658e265`.
Images, reports, and blend files remain ignored and are not committed.
