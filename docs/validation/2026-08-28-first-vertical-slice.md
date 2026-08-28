# Blender 4.2.23 first vertical-slice smoke

- Status: passed
- Date: 2026-08-28
- Branch: `codex/first-vertical-slice`
- Run ID: `20260828T150328Z-653a3ac9`
- Blender: `4.2.23 LTS` (Microsoft Store package)
- Add-on: `0.2.0`
- External MCP server: `0.2.0`
- Local protocol: `1`
- MCP protocol: `2025-11-25`

## Fixture boundary

The source fixture remained at:

~~~text
C:\Users\26687\Work\projects\blender-projects\test-model.blend
~~~

Its SHA-256 before and after the smoke was
`255e6c0a1730e80f2a57dc870dd51bbe45ea210546784f8a7af71b88d6014da3`.
The source repository status was unchanged at `main` with the pre-existing
`M test-model.blend` modification.

Only the isolated system-temporary copy was opened. During manual add-on setup,
that copy changed from the prepared source hash to
`4b87f47d60a86b2e81c367df4e73b2ede8b7afeaf85a027fae3f57ae7e395629`.
The harness recorded this deviation explicitly, accepted that current copy as
the live-run baseline, and verified it did not change during the smoke. No save
tool exists in the MCP surface.

## Results

- The MCP tool list contained all ten documented dotted tool names.
- `connection.ping` returned Blender, add-on, protocol, capabilities, and a live
  UI heartbeat.
- Microsoft Store app-data virtualization was exercised. The external client
  found only the constrained `BlenderFoundation.Blender*` package manifest and
  validated the packaged Blender PID with a query-only Win32 process handle.
- A manual **Restart Bridge** changed the instance ID from
  `77a0d7f3-1ace-40e6-b833-2091fa3eac5d` to
  `87daa3b0-5c86-42c3-a49c-0ba61c5708d3`; the existing stdio client reconnected.
- The non-ASCII object `绯雪_edit_mesh` round-tripped through `context.get` and
  `object.inspect`.
- FRONT, RIGHT, and TOP captures of
  `Portrait_ID_V13_SubjectFX_Sclera_L` were non-blank, mutually distinct PNGs
  at 1000 by 504 pixels. Visual review confirmed the requested eye object was
  selected and framed in each orthographic view.
- A transaction set only
  `Portrait_ID_V13_SubjectFX_ScleraAperture_L.scale.z`, from
  `0.9999999403953552` to the absolute value `1.08`.
- Explicit rollback restored `scale.z` to `0.9999999403953552` and reported
  `context_restored: true`. An earlier interrupted attempt also verified the
  disconnect-triggered safety rollback.
- Mode, active object, selection, viewport transform, target visibility,
  target selection, and helper object state matched their pre-operation values.
- Heartbeat advanced from 3307 to 3938. The user successfully interacted with
  the Blender panel for restart and foreground checkpoints. The measured prior
  screenshot command duration was about 1.43 seconds; no process-wide UI hang
  or listener stall occurred.

## Visual and structured evidence

Ignored local artifacts are under:

~~~text
artifacts/live-smoke/20260828T150328Z-653a3ac9/
~~~

The final `report.json` SHA-256 is
`f75c7e6b8ec26c1526b865e58c61b1fac1fe72a23ddfd2298497297f7c82c1ff`.
The directory also contains the three initial views, the transformed front
view, the rolled-back front view, preparation metadata, and the preserved
interactive-restart report. Images and `.blend` files remain ignored and are
not committed.

The harness rejects blank screenshots and duplicate orthographic captures, so
background/minimized-window black frames cannot be recorded as a pass.
