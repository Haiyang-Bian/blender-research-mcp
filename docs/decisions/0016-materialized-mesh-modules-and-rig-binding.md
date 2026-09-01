# 0016 — Materialized Mesh modules and exact rig binding

- Status: accepted for 0.15.0 implementation
- Date: 2026-09-01

## Context

Transaction-v9 can edit and transfer UVs and vertex-group weights, but a Mesh carrying
Shape Keys still cannot change topology. Copying the source object does not remove that
constraint, and applying an Armature or another Modifier in place would destroy source
dependencies. Production character scenes also group one logical module from many
disconnected face islands and require the resulting object to reuse an existing
Armature without silently generating or changing weights.

Blender exposes three materially different geometry states: the stored base Mesh,
current Shape-Key deformation without Modifiers, and the fully evaluated dependency
graph result. Treating all three as one implicit "evaluated copy" would make it easy to
bake an Armature and then bind the baked result a second time.

## Decision

Add three independent, typed operations: `mesh.materialize`, `mesh.extract`, and
`rig.bind`, plus read-only `mesh.extract.preflight` and `rig.inspect` evidence.

`mesh.materialize` always creates a new independent object and requires one explicit
evaluation mode:

- `BASE` copies the stored Mesh and ignores Shape Keys and Modifiers.
- `SHAPE_KEYS_CURRENT` bakes current Shape-Key values on a private temporary copy while
  excluding every Modifier.
- `FINAL_EVALUATED` requires a live SurfaceRef and bakes the complete current dependency
  graph result.

The source object is never replaced, hidden, or modified. The output has no Shape Keys,
Modifiers, or parent. Material, UV, and weight preservation are individually requested
and verified. Exact ComponentMap lineage is emitted only when topology is identical;
the implementation never guesses lineage from positions.

`mesh.extract` generalizes the existing separation kernel to any non-empty proper FACE
subset containing one or more connected components. It creates one object and returns
separate SOURCE and EXTRACTED ComponentMaps. Parent, Modifier, and material-slot output
policies are explicit. `mesh.separate` retains its existing one-component contract.

`rig.bind` creates or updates one exact Armature Modifier and optionally changes the
object-level Armature parent with explicit world/local semantics. It validates existing
vertex groups against exact bones but never transfers, normalizes, or writes weights.
Those remain the authority of the 0.14 weight tools.

All writes remain transaction-bound, idempotent, rollback-safe, disconnect-safe, and
subject to native-save adoption. Upgrade transaction capability to 10 and ComponentMap
capability to 3. Add materialization, extraction, and rig-binding capabilities.

## Alternatives

- **Apply Shape Keys or Modifiers on the source.** Rejected because it mutates or
  destroys the dependency graph the operation is meant to preserve.
- **Always use final evaluated geometry.** Rejected because it silently bakes Armature
  deformation and makes later rebinding unsafe by default.
- **Infer ComponentMap lineage spatially.** Rejected because proximity is not exact
  topology evidence.
- **Generate automatic weights during binding.** Rejected because binding and weight
  authoring have different validation and recovery contracts.
- **Add character-specific extract-hair or complete-body tools.** Rejected in favor of
  generic Blender object, Mesh, and Armature semantics.

## Consequences

Agents can create a topology-editable working copy from exact source evidence, extract
multi-island modules, and attach them to an existing rig without changing the source or
inventing hidden surfaces. Missing body or scalp geometry still requires an explicit
template or cage; materialization cannot recover data absent from the source. Component
catalogs, Collection organization, generic parenting, and cross-object batch execution
remain 0.15.1 work. Library append and template workflows remain 0.16 work. Shape-Key
structure editing and Modifier Apply remain separate future authorities.
