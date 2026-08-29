# Blender 4.2.23 comparative-preview smoke

- Status: passed
- Date: 2026-08-30
- Branch: `codex/unified-object-settings`
- Run ID: `20260829T155501Z-6f446f7e`
- Blender: `4.2.23 LTS`
- Add-on and external MCP server: `0.8.0`
- Protocol: `1`
- Port: `9881`

The harness began without an MCP session, launched a visible managed Blender, opened a
fresh `%TEMP%` copy of the integration fixture, and drove the public
`lookdev.compare` MCP tool. The selected target was Shape Key
`绯雪_edit_mesh["真面目"]`, with baseline `0.0` and ordered candidates `0.2`, `0.4`,
and `0.6`.

## Ordered evidence and restoration

The response order was `baseline`, `candidate-A`, `candidate-B`, `candidate-C`. Every
PNG was nonblank and its returned SHA-256 matched the decoded image:

- baseline: `2ffab454ff2a0aa0ea051a510c675620e70dbbdf9c03cc9a5ef5b344522000fb`;
- candidate A: `4974bd8ae0180188f8e7823af663c6ffa7df91a722622c2168a142440ced2171`;
- candidate B: `77b33c4829a3b05743441f2b7d9f37ce4f0707f7c513d4e9e98b9e95fc5320d3`;
- candidate C: `0fc0f8338c2dd61f40b0749cf6899d029c5aef61cc7f7f3691501460d5f1a131`.

The images differed only slightly for this frontal evidence view, but each candidate
had a nonzero maximum channel difference (`15` or `16`), and the tool correctly kept
the near-indistinguishable result as evidence rather than selecting a winner.
`context_unchanged`, `object_unchanged`, and `target_restored` were all true.

## Conflict and disconnect checks

An environment-gated private hook changed the same Shape Key to approximately `0.6`
after candidate A wrote `0.2`. Rollback stopped with `PROPERTY_CONFLICT`, preserved the
injected value, and did not execute candidate B. After restoring the expected guard
value, an explicit rollback returned the target to baseline.

A separate comparison closed the transport immediately after its writer. The add-on
automatically rolled the active transaction back during the reconnect grace period.
Reconnect observed the baseline `0.0`, the original context, and no active transaction;
a begin/rollback clearance probe then succeeded. Heartbeat advanced from `14` to `191`.

## Fixture and regression evidence

The source and temporary `.blend` SHA-256 remained
`255e6c0a1730e80f2a57dc870dd51bbe45ea210546784f8a7af71b88d6014da3`.
The source repository's pre-existing `test-model.blend` modification and untracked
`task-1.md` status were identical before and after the run.

The first attempt exposed a float32 restoration false positive: assigning Blender's
`RegionView3D.view_rotation` normalizes its quaternion and changed components by about
`6e-8`. Context evidence now canonicalizes view floats to six decimal places, after
which the same real capture restored successfully. This changes no viewport intent and
prevents exact-JSON guards from treating Blender's representational round-off as a user
edit.
