# Blender 4.2.23 bounded LookDev writes smoke

- Status: passed
- Date: 2026-08-29
- Branch: `codex/bounded-lookdev-writes`
- Run ID: `20260829T051905Z-3ae57448`
- Blender: `4.2.23 LTS` (Microsoft Store package)
- Add-on and external MCP server: `0.5.1`
- Local protocol: `1`
- MCP protocol: `2025-11-25`
- Capture backend: `gpu_offscreen`

The live handshake exposed the expected 0.5 capabilities: `transport: 1`,
`context: 1`, `viewport_capture: 3`, `viewport_raycast: 1`,
`geometry_inspection: 1`, `lookdev_inspection: 1`, `transactions: 2`, and version
1 for scale, visibility, modifier-state, shape-key, and material-input writes.

## Fixture and file boundary

The source fixture remained
`C:\Users\26687\Work\projects\blender-projects\test-model.blend`. Its SHA-256
before preparation and after the passing run was
`255e6c0a1730e80f2a57dc870dd51bbe45ea210546784f8a7af71b88d6014da3`.
The source repository remained on `main` with its pre-existing
`M test-model.blend` status.

The user elected to continue with an older isolated `%TEMP%` copy that had been
saved during manual setup before the accepted run. The harness therefore recorded
its current SHA-256,
`a40d0d347171f5c2e6fef93e60e52322de882c130507f9f52ed1d6df1d8852f4`, as the
explicit live baseline. The same hash remained after the run. No MCP command or
accepted smoke interval saved either blend file.

The tested development ZIP was `blender-research-mcp-addon-0.5.1.zip`, SHA-256
`0b4996b81e641aad20f56d4233b87d1d86a16b3b9cbbb01a1594845ee639e68c`.

## Native UI and focus independence

The user confirmed the compact 3D Viewport N-panel and the full Scene Properties
panel showed the 0.5 write authority, transaction state, and delta count without
splitting or changing an Area. The foreground capture and all later preview images
were valid, non-black GPU off-screen images.

During the obscured phase, foreground PID `31848` belonged to ChatGPT/Codex while
Blender PID `36156` remained running behind it. Blender did not need focus and was
not minimized. The foreground FRONT/MATERIAL capture SHA-256 was
`3d06dc35d275f73ca51d671b59e637320f3fc89636f20ae2251f0b887adb533b`; the
obscured baseline capture SHA-256 was
`d9c4fded752e932d76e933139ee1d4e22c806d984dd7e0d843dba1846495a270`.
Their small stochastic rendered difference was accepted as normal viewport noise,
not as a focus-dependent failure: mean absolute difference was `0.01795`, structural
mean absolute difference was `0.01841`, and the maximum channel difference was `28`.
Heartbeat advanced from at least `19710` during the foreground capture to `22077` at
completion.

## Inspected targets and reversible writes

The harness discovered targets from `object.lookdev.inspect` and
`material.inspect`; it did not guess identities or writable RNA paths. Each preview
used a separate transaction and absolute value, captured evidence, rolled back, and
re-inspected the property:

- Visibility: `Portrait_ID_V13_SubjectFX_Sclera_L.hide_render` changed from `false`
  to `true`, then restored to `false`. The preview image SHA-256 was
  `1a72837e713252bcd8409734f9ca7ff8d4ff73c6f2bc4b3e3889c68288892fcd`.
- Modifier: `绯雪_edit_mesh` modifier `mmd_armature.show_viewport` changed from
  `true` to `false`, then restored to `true`. The preview image SHA-256 was
  `140c61861aef2f6c9a74bc63fcb179a6330aa665a7d4bff11504104e2b651d9b`.
- Shape key: non-Basis, undriven `绯雪_edit_mesh["真面目"]` changed from `0.0`
  to `0.1`, then restored to `0.0`. The preview image SHA-256 was
  `cb8e34135f4679529154914022ed4502528145bbe85d3a2f50516e78a9e93173`.
- Material input: the single-user material `CODEX_V10_Face_`, node
  `CODEX_V10_LAYERED_SKIN`, socket `Metallic` changed from `0.0` to `0.05`, then
  restored to `0.0`. The preview image SHA-256 was
  `99a3a4b7c3c77735c7bf0f2898c416af6ae48259bdc8748ee936eda0836c4184`.

Every rollback returned `status: rolled_back` and `context_restored: true`. The
harness compared mode, active and selected objects, frame, camera, workspace, and
viewport state after each operation. The final context remained OBJECT mode with
`绯雪_edit_mesh` active and selected, frame `49`, camera
`Portrait_ID_V13_Camera`, RENDERED shading, overlays enabled, and the original
view transform.

Changing visibility advanced the scene generation and a raycast using the prior
capture ID returned `CAPTURE_STALE`, proving that old spatial evidence was not
silently reused.

## Conflict and disconnect safety

For the manual concurrency check, the transaction wrote
`Portrait_ID_V13_SubjectFX_Sclera_L.hide_render = true`. The user changed the same
property to `false` without changing selection. Rollback returned
`PROPERTY_CONFLICT` and preserved the user value. After the user restored the
agent guard value, ordinary rollback restored the original `false` value and the
captured context.

A second visibility transaction was left active while the stdio client disconnected.
After a three-second grace period, a fresh client observed `hide_render = false`.
Starting and rolling back an empty verification transaction proved the abandoned
transaction had been cleared. Scene generation advanced from `1` to `15` across the
intentional writes and reversals; no property remained changed.

## Harness corrections found during validation

The live gate exposed three useful harness and workflow issues before the passing run:

1. Foreground focus transitions can briefly change Blender context. The harness now
   requires two consecutive identical context reads before capturing, rather than
   treating a transient transition as `OBSERVATION_CONTEXT_DRIFT`.
2. A manual conflict prompt cannot trust the operator response alone. It now reads
   `hide_render` back through the MCP and retries until the requested value is present.
3. The conflict target is a non-active object and the operator changes only its
   Outliner camera restriction icon. This isolates the property conflict from a
   selection-context conflict.

The passing interval started only after those corrections. The old temporary file's
manual save was detected by its hash guard and accepted as a new baseline only after
the user's explicit instruction; the source fixture was never substituted or changed.

## Evidence

Ignored local evidence is under
`artifacts/live-smoke/20260829T051905Z-3ae57448/`. It contains foreground and
obscured baselines, four preview images, preparation metadata, and
`report-0.5.1.json`. Visual review confirmed every image contained meaningful scene
content and no black capture. The report SHA-256 is
`a25856ab62e82a5af7c5bd49e53fbc02bad965b22a91839d8e5733731a59e441`.
Images, JSON reports, ZIPs, and blend files remain ignored.
