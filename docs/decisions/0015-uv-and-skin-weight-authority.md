# 0015 — UV and skin-weight authority

- Status: accepted for 0.14.0 implementation
- Date: 2026-08-31

## Context

Transaction-v8 can preserve supported Mesh attributes while geometry changes, but it
cannot inspect or intentionally edit UV layers or vertex-group weights. Separation
therefore rejects weighted objects, and callers cannot prove that a topology workflow
retained render mapping or deformation data.

UV coordinates belong to Mesh face corners, seams belong to Mesh edges, and vertex
group definitions belong to objects while their deform weights follow Mesh vertices.
They cannot share one untyped writer or one ownership rule. Blender's production
Angle-Based/Conformal unwrap and island packing are context operators, but applying
them directly to the user's live object would violate collaborative UI semantics.

## Decision

Add typed `mesh.uv.inspect/edit` and `mesh.weights.inspect/edit` domains plus one closed
`mesh.attribute.transfer` union. Extend `mesh.validate` and Mesh batches with UV and
weight checks. Every writer requires exact object/Mesh/layer/group identities,
fingerprints, an active transaction, current generation, and one idempotency UUID.

Run unwrap and packing only against a temporary object and Mesh under a private
operator context. Copy back only verified UV data after proving that topology stayed
identical, then delete every temporary resource. User mode, selection, active object,
workspace, and viewport never become operator inputs and must remain unchanged.

UV writes use the Mesh snapshot guard. Weight writes add a joint guard for object-local
group schema and sparse deform values. `OBJECT` scope transactionally single-users a
shared Mesh. `SHARED_DATA` weight writes require every exact Mesh user to have the same
ordered group schema and update those schemas together.

Topology and separation default to `PRESERVE_INTERPOLATE`. They may explicitly reject
present data or discard result attributes. Preservation is accepted only when layer
schemas, finite UV values, weight ranges, group schema, and coverage validate after the
operation; otherwise the current call is restored. Separation chooses source and new
branch policies independently.

Allow topology-stable UV and weight writes on Meshes with Shape Keys. Topology changes,
Shape-Key edits, evaluated-Mesh materialization, Modifier Apply, custom-normal writes,
and arbitrary attributes remain outside 0.14.

Upgrade transactions to 9, Mesh topology to 4, separation to 2, validation to 2, and
Mesh batches to 2. Add `mesh_uv`, `mesh_weights`, and `mesh_attribute_transfer`
capabilities.

## Alternatives

- **Use the user's live Edit Mode for unwrap.** Rejected because it would consume and
  overwrite collaborative mode and selection state.
- **Implement only planar projections.** Rejected because it does not provide Blender's
  expected organic-mesh ABF/LSCM result.
- **Make every old topology request specify attribute policy.** Rejected because it
  would break compatible callers; omission retains the new preserving default.
- **Include Shape Keys and Modifier Apply.** Deferred to 0.15 because both require a
  different topology-dependency and evaluated-data snapshot contract.

## Consequences

Agents can inspect, author, transfer, validate, and transactionally restore production
UV and deformation data without changing Blender UI selection. Weighted regions can be
separated and topology-edited when interpolation is provable. The authority remains
closed: no arbitrary operator, BMesh, RNA, Python, generic attribute, or retopology
surface is exposed.
