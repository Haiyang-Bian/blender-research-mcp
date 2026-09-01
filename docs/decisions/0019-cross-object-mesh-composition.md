# Decision 0019: exact cross-object Mesh composition

- Status: accepted for 0.17.0 planning
- Date: 2026-09-01

## Context

By 0.16 the bridge can create, materialize, extract, separate, append, align, fit,
organize, weight, and bind multiple Mesh objects. It cannot combine those objects into
one continuous editable Mesh. Object parenting and Collections express assembly, but
they do not create shared topology, a welded seam, one UV/weight schema, or a stable
base for later Shape Keys.

Mirroring Blender's context-dependent Object Join operator would reintroduce active
object, selection, mode, and silent name-suffix behavior. Treating the operation as an
object setting would also hide the actual authority: every input Mesh is transformed
into one output coordinate frame, attribute schemas are reconciled, and topology
lineage changes.

## Decision

0.17 uses the Mesh domain:

- `mesh.join.preflight` proves that a set of exact Mesh-object inputs can be composed
  under explicit coordinate, material, UV, weight, attribute, and source-disposition
  policies without changing Blender.
- `mesh.join` creates one independent output Mesh object and returns one exact
  `JOIN_BRANCH` ComponentMap per input. It never depends on Blender selection or an
  active object.
- `mesh.edit` gains one bounded `weld_vertices` operation over revision-bound
  SelectionSets. Join preserves shells; weld is the separate, explicit topology step
  that makes selected coincident boundaries continuous.

The caller explicitly chooses an output coordinate frame and whether input objects are
kept or deleted at commit. Deletion is deferred and guarded. The output name and
destination Collection are exact; Blender may not synthesize `.001` names.

Material slots are reconciled by data-block identity, UV Layers and Vertex Groups by
exact name/schema policy, and every source-to-output slot/group mapping is reported.
Unsupported custom normals, Shape Keys, linked data, driven schemas, or generic
attributes are rejected unless a request explicitly chooses a supported discard or
recalculation policy. No implicit best-effort merge is allowed.

Each source receives a separate lineage map into the same output revision. This avoids
pretending that the existing single-input ComponentMap has one synthetic source. A
subsequent weld records MERGED vertex relations in the ordinary output revision map;
batch composition may compose each JOIN_BRANCH with that weld map.

## Transaction and user intent

Join records all output IDs and any deferred source deletions under transaction v13.
Rollback or disconnect deletes the unmodified Agent-owned output and restores source
visibility/link state. Commit finalizes requested source deletion only after every
source and output guard passes. User changes produce a structured conflict and are not
overwritten. Native Blender save continues to adopt the visible joined or welded state
as final user intent.

## Consequences

- Protocol remains 1.
- Planned capabilities are `mesh_join: 1`, `mesh_topology: 5`,
  `mesh_component_map: 4`, `mesh_batch: 5`, and `transactions: 13`.
- The public name is `mesh.join`, not `object.join`, because the operation owns Mesh
  data, attribute schemas, coordinate conversion, and lineage.
- Join and weld remain separate so callers can inspect shell positions and attribute
  mappings before destroying a boundary.
- Shape-Key structure editing, bone authoring, Modifier Apply, arbitrary BMesh/RNA,
  Sculpt, and retopology remain outside 0.17.

## Alternatives considered

- **Expose Blender's Object Join operator.** Rejected because it is selection- and
  active-object-dependent and hides attribute reconciliation.
- **Always modify one input object in place.** Rejected because it broadens rollback
  over user-owned inputs and makes source preservation ambiguous.
- **Always weld during join.** Rejected because spatial coincidence is not semantic
  proof that two boundaries should be merged.
- **Implement Shape Key writing first.** Deferred because Shape Keys need a stable
  composed base Mesh and explicit source-to-output lineage.
