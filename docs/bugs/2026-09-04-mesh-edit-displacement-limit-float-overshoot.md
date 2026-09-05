# `mesh.edit` rejects equal inflate amount and displacement limit after tiny float overshoot

> Tracking update, 2026-09-05: archived into version control with user authorization.
> Still open as QLT-03 in the [0.17.6 plan](../roadmap/2026-09-05-rendering-and-animation-review.md).
> No fix or new runtime verification is claimed by this archive update.

- Status: observed once in a live scene; recovered without writeback; minimal isolated fixture not yet run
- Impact: a caller that sets `maximum_displacement` equal to a bounded inflate amount can receive a false-positive `DISPLACEMENT_LIMIT`, even though the requested displacement is exactly at the declared limit. This interrupts otherwise safe, transaction-guarded surface fairing.

## Environment

- Windows, Blender 4.2.23 LTS
- Loaded Blender Research MCP package/add-on 0.17.5
- Tool repository checkout `5b8910d`
- Target project: `C:\Users\26687\Work\projects\blender-projects\test-model-fill-checkpoint.blend`
- Target object: `CODEX_HeadComplete_HeadShell.001`

## Scope and checkpoint

The call operated on a revision-bound weighted selection of 1472 scalp vertices inside a normal Mesh transaction. Face, ear-root and material-boundary neighborhoods had already been excluded. The source Mesh had 3771 vertices, 10355 edges and 6582 faces. The failed call reported no writeback, and the same requested inflate amount succeeded after only increasing the safety limit by 0.00001 Blender units.

## Reproduction

1. Begin a Mesh transaction on the target object.
2. Create a weighted VERTEX selection on the scalp interior.
3. Call `mesh.edit` with an inflate operation using `amount=0.0006` and `maximum_displacement=0.0006`.
4. Observe the pre-write rejection:

```text
MESH_EDIT_FAILED
reason: DISPLACEMENT_LIMIT
maximum_cumulative_displacement_world: 0.0006000119795840484
maximum_displacement: 0.0006
writeback: false
```

5. Retry the same logical operation from the unchanged Mesh state with `amount=0.0006` and `maximum_displacement=0.00061`.
6. The operation succeeds. Subsequent topology, orientation, self-intersection, local-quality, UV and weight checks pass.

The original failed request was not replayed after success. The exact transaction and selection identifiers are session-local and are retained in the originating Codex task record rather than treated as portable fixture inputs.

## Expected vs actual

Expected: when the requested constant inflate magnitude equals the explicit displacement limit, ordinary Blender numeric representation error should not make the operation fail. A meaningful overshoot must still be rejected.

Actual: the measured displacement exceeded the request by about `1.19795840484e-8` Blender units and was compared strictly against the limit, causing `DISPLACEMENT_LIMIT`.

## Evidence

- Failed call: requested 0.0006; measured `0.0006000119795840484`; `writeback=false`.
- Recovery call: requested 0.0006; guard 0.00061; affected 1472 weighted vertices; transaction completed.
- Final saved Mesh retains 3771 vertices, 10355 edges, 6582 faces and topology fingerprint `91363d36e632864c75121350758df60e6a49cc1518eafd09ffc23169098ec3d2`.
- Final validation: self-intersection 0; consistently oriented; non-manifold count unchanged at 174; affected-region local quality passed; UV and weight content fingerprints unchanged.
- Project record: `C:\Users\26687\Work\projects\blender-projects\notes\head-neck-completion-2026-09-03.md`.

## Analysis

Confirmed: the tool rejected before writeback solely because the reported displacement was slightly greater than an equal requested limit, and a 0.00001-unit guard headroom allowed the same inflate amount to complete safely.

Hypothesis: Blender float32 coordinate storage, normalized vertex normals, transform-space conversion, or cumulative-displacement calculation may introduce the tiny positive difference. The live evidence does not isolate which layer is responsible. This report is distinct from the resolved 0.10.2 transaction-property float32 comparison issue; it concerns Mesh displacement-limit enforcement.

## Recovery

The failure made no Mesh change. The caller kept the modeling amount at 0.0006, increased only the abort limit to 0.00061, then completed orientation, intersection, boundary, UV and weight validation. The final project was saved and opened successfully by an independent background Blender audit.

## Regression and acceptance

- Add an isolated inflate fixture whose requested amount equals `maximum_displacement` on transformed and untransformed Meshes.
- Cover zero and nonzero falloff weights, normalized and slightly non-unit normals, and positive/negative inflate directions.
- Accept the equal-intent operation across Blender float32 write/read behavior without weakening rejection for a displacement that exceeds the limit by a modeling-significant amount.
- Assert both outcomes: accepted calls write exactly once; genuinely over-limit calls leave `writeback=false` and preserve the Mesh fingerprint.
- Retest the live scalp sequence on Blender 4.2.23 or a reproducible reduced Mesh.

## Retest history

- 2026-09-04, package/add-on 0.17.5: equal amount/limit failed; same amount with 0.00001 guard headroom passed. No minimal isolated fixture yet.
