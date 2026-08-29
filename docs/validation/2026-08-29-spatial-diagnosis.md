# Blender 4.2.23 spatial diagnosis smoke

- Status: passed
- Date: 2026-08-29
- Branch: `codex/spatial-diagnosis`
- Run ID: `20260828T190647Z-ad328ce8`
- Blender: `4.2.23 LTS` (Microsoft Store package)
- Add-on and external MCP server: `0.4.0`
- Local protocol: `1`
- MCP protocol: `2025-11-25`
- Capture backend: `gpu_offscreen`

The live handshake advertised the required capability versions:
`transport: 1`, `context: 1`, `viewport_capture: 3`,
`viewport_raycast: 1`, `geometry_inspection: 1`, `transactions: 1`, and
`object_transform_scale: 1`.

## Fixture and file boundary

The source fixture remained at
`C:\Users\26687\Work\projects\blender-projects\test-model.blend`. Its SHA-256
before and after the run was
`255e6c0a1730e80f2a57dc870dd51bbe45ea210546784f8a7af71b88d6014da3`.
The source repository remained on `main` with its pre-existing
`M test-model.blend` state.

The tested file was an isolated `%TEMP%` copy. Blender changed that copy during
manual setup, before the automated interval, so the harness explicitly recorded
the current pre-test hash
`ab410901a877d2724f5b0ee60f4ee477ab1429338caec1632832d3739a30e1e2` as its
baseline. The same hash remained after the smoke. No MCP tool or test step saved
a blend file.

The development ZIP was `blender-research-mcp-addon-0.4.0.zip`, SHA-256
`ebba2e5d440ee0442cf6906b640dbce502c892e6ce76eca7b6966be7b3728306`.

## Native UI and focus independence

The user confirmed that the 3D Viewport N-panel was compact, the Scene page of
the Properties Editor contained the full control panel, and the add-on had not
split or changed the Area layout.

Blender PID `35308` owned the foreground baseline. Codex PID `21112` owned the
foreground before and after the automated diagnostic, so Blender remained fully
obscured without being minimized. The foreground and obscured SOLID captures
were pixel-identical. Heartbeat advanced from `6113` to `6919` while the UI
remained responsive.

## Diagnostic captures

The target `Portrait_ID_V13_SubjectFX_Sclera_L` was captured at 1000 by 504
pixels with overlays disabled:

- RENDERED SHA-256: `03e6b37253f55743f1ec60f2caf5d6e826b54fbddca24f79b4d1c59466ac5b76`
- SOLID SHA-256: `e157f23fdff75fe7f30770d05d454dc42c1028867ef2ab1c65a52fa551ef3935`
- WIREFRAME SHA-256: `dc3725c6c31e86f067952f00eb49c87e2b6d2f1bcb6764660a703e5d7fd2b61d`

Visual review confirmed meaningful eye geometry in all three images. Relative to
SOLID, RENDERED had mean pixel difference `83.5898` and WIREFRAME `35.9550`.
An absolute FRONT orbit of yaw `30` degrees and pitch `15` degrees had mean
difference `30.2791`, proving that orbit started from the semantic base instead
of silently reusing the current view.

The obscured FRONT/RIGHT/TOP SOLID bundle returned three distinct ordered images,
`context_unchanged: true`, `object_unchanged: true`, and stable scene generation
`4`. The active non-ASCII object `绯雪_edit_mesh` round-tripped without changing
mode, selection, frame, camera, shading, overlays, or viewport transform.

## Capture-bound raycasts and geometry

The normalized top-left coordinate `(0.5, 0.5)` produced valid hits from both:

- FRONT/ORTHO: face `992`, distance `499.9846`;
- CURRENT/PERSP: face `998`, distance `0.042529`.

Both rays had finite origins, unit directions, finite hit locations, unit normals,
and hit `Portrait_ID_V13_SubjectFX_Cornea_L`. This is the expected first evaluated
geometric surface: transparent cornea geometry precedes the sclera target and
raycasting does not emulate rendered transparency.

`object.geometry.inspect` on that hit returned 1986 vertices, 4032 edges,
2048 polygons, and 3968 loop triangles. All 4032 edges were manifold, with no
loose, boundary, or non-manifold edges. The sole material
`CODEX_V13_Portrait_Cornea` covered all polygons. Repeated inspection returned
stable counts and bounds and did not advance scene generation (`4` before and
after).

## Stale evidence and rollback

The bundle generation `4` directly opened a transaction. The only modified field
was `Portrait_ID_V13_SubjectFX_ScleraAperture_L.scale.z`, from
`0.9999999403953552` to absolute `1.08`, advancing generation to `5`. Raycasting
with the old capture ID returned `CAPTURE_STALE` rather than reusing outdated
matrices.

The preview changed the SOLID image by mean `6.8800` and structural mean `6.1774`.
Rollback restored scale z to `0.9999999403953552`, returned
`status: rolled_back` and `context_restored: true`, and advanced generation to
`6`. A new capture then raycast successfully. The before and rollback images were
pixel-identical, and the full context and inspected object identities matched
their pre-transaction values apart from the intentionally advanced generation.

## Harness corrections found during validation

The live gate rejected two smoke-harness assumptions before the passing run:

1. Capture metadata intentionally reports overlays as the stable strings `ON` or
   `OFF`; the first harness assertion incorrectly expected Boolean `false`.
2. The harness originally recorded its restoration baseline before asking the
   user to bring Blender forward and select Perspective view. It now records the
   baseline after all manual setup, so user preparation cannot be misreported as
   add-on context drift.

Neither failure occurred inside a transaction. The later runs rolled back their
single supported mutation before reporting the context mismatch.

## Evidence

Ignored local evidence is under
`artifacts/live-smoke/20260828T190647Z-ad328ce8/`. It contains the three display
modes, orbit and perspective captures, FRONT/RIGHT/TOP bundle, transformed and
rolled-back images, preparation data, and `report.json`. The report SHA-256 is
`3feb9d84db941fb0c1dc448c6dc6ec1a470a031b337a2d70ef7c83a376537c47`.
Images, reports, ZIPs, and blend files remain ignored.
