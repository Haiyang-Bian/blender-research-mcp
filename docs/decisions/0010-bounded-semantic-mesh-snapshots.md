# 0010 — bounded semantic base-Mesh snapshots and editing

- Status: accepted and automated/live-gate validated in 0.11.0
- Date: 2026-08-30

## Context

The 0.10 Modifier surface covers common non-destructive whole-object modeling but
cannot express intentional component changes. Exposing arbitrary BMesh operators,
coordinate arrays, or RNA would remove semantic constraints and make safe conflict
handling unbounded. Applying Modifiers would likewise conflate an evaluated result with
the original base Mesh.

Component indices are not stable identities. Blender topology operations can create,
delete, and reorder elements, so an index without exact full Mesh evidence is stale
immediately after a successful topology edit.

## Decision

Add `mesh.inspect` for paged exact base-Mesh evidence and one closed `mesh.edit` writer
with internally typed BMesh handlers. Keep evaluated geometry inspection separate.
Pair every component index with the complete `mesh_fingerprint`; require a new
inspection after topology changes.

Upgrade transaction capability to version 4 and keep one `Mesh.copy()` baseline guard
per edited working Mesh. The guard tracks identity, complete object-user set, current
user count, protected data, and the Agent's latest expected fingerprint. Commit removes
the snapshot only after every guard passes. Rollback and disconnect recovery write the
snapshot back into the same Mesh identity only while that evidence still matches.

Require an explicit sharing scope. `SHARED_DATA` mutates the exact common data-block
for all inspected users. `OBJECT` creates a reversible single-user copy only when the
target Mesh is shared. Preserve UV, color, material, smooth, and supported generic
attribute data through editing and restoration, but do not grant permission to edit UV
or attribute values in this version.

Limit the public operation union to transform, face extrude/inset, edge bevel,
delete/dissolve, vertex merge, face settings, and normals. Validate both outside and
inside Blender, compute in BMesh, perform one writeback, and retain a per-call snapshot
for verified local restoration if writeback fails.

## Alternatives

- **Expose arbitrary BMesh operators.** Rejected because operator-specific contexts,
  parameters, and result semantics cannot share a meaningful closed recovery contract.
- **Use Blender Undo as the transaction.** Rejected because user and Agent actions can
  interleave, and Undo does not prove identity, sharing, or protected-data restoration.
- **Replace the Mesh data-block on every edit.** Rejected because it changes identity,
  sharing, Modifier references, and downstream evidence even for single-user data.
- **Treat indices as persistent identities.** Rejected because topology mutation makes
  that claim false. Fingerprint-coupled ephemeral indices are explicit and testable.
- **Include UV editing.** Deferred to 0.14 so topology revision and ComponentMap can be
  independently proven before adding unwrap/island/transform authority.
- **Model visual water waves as geometry.** Rejected as a general rule: surface detail
  belongs in the material unless the user explicitly needs geometric silhouette or
  displacement-like structure.

## Consequences

Agents can perform common exact component modeling while preserving shared-data intent
and reversible transaction semantics. The cost is one complete in-memory snapshot per
edited Mesh per transaction plus a temporary per-call snapshot, bounded by the fixed
Mesh budgets. A conflict deliberately blocks forced rollback and preserves user state.

A 0.11 server can still use the 0.10 surface with an older add-on; only the Mesh domain
requires `mesh_topology: 1` and transaction version 4. Modifier Apply, UV editing,
arbitrary component arrays/operators, Shape-Key topology, generic RNA, and arbitrary
Python remain unavailable.

The Blender 4.2.23 gate also established that restore must rebuild exact Mesh arrays
and protected attribute values rather than round-tripping the saved Mesh through
BMesh. Full fingerprints are session evidence because material slots contain session
identities; after project reload, callers must re-inspect rather than compare or reuse
the old fingerprint.
