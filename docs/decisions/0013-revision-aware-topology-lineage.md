# 0013 — Revision-aware topology lineage and component maps

- Status: accepted for 0.13.0 implementation
- Date: 2026-08-31

## Context

SelectionSets introduced in 0.12 are intentionally stale after a topology change.
Existing 0.11 topology writers return created/deleted counts, but a caller cannot
prove which old components survived, split, merged, or produced a useful after-state
selection. Re-inspecting and guessing new indices is not a valid character-authoring
workflow.

## Decision

Add session-local immutable ComponentMap resources. A map binds one exact before Mesh
revision to one exact after revision and records same-domain `SURVIVED`, `SPLIT`,
`MERGED`, and `DERIVED` relations plus created and deleted components. Lineage is
produced by the typed BMesh handler from transient component tags and explicit operator
results; it is never inferred later from geometric proximity.

Add paged inspection, explicit release, and forward SelectionSet remapping. Remapping
may retain all mapped descendants, exact survivors only, or require complete coverage.
Weights copy across one-to-many relations and use an explicit maximum or average when
many sources converge.

Upgrade transactions to capability 7. A topology-changing `mesh.edit` creates one map,
one after-revision, and bounded after-state SelectionSets. Rollback restores the
baseline revision and invalidates the map's after-state; commit and native save retain
it while the live Mesh still matches. File load and add-on restart clear all maps.

Expose bounded subdivide, edge-ring loop cut, plane bisect, in-Mesh split, bridge, hole
fill, and grid fill operations through the existing closed `mesh.edit` union. Keep
object separation and multi-operation declarative plans for 0.13.1.

## Alternatives

- **Re-identify components by coordinates.** Rejected because coincident geometry,
  symmetry, merges, and floating-point changes make it ambiguous.
- **Persist IDs as Mesh attributes.** Rejected because internal evidence would mutate
  user data, alter fingerprints, and require cleanup after save.
- **Return the complete map inline.** Rejected because a valid bounded Mesh can exceed
  the 32 MiB response boundary; maps are compact resources with paged evidence.
- **Add object separation and a generic batch executor now.** Deferred until atomic
  topology lineage and rollback are independently proven.

## Consequences

Agents can carry semantic regions across one exact topology revision without guessing
indices. Every topology handler must account for lineage, resource budgets, protected
attributes, no-op behavior, and failure restoration. ComponentMap composition, object
separation, UV/weight migration, Shape-Key writes, and Modifier Apply remain later
capabilities.
