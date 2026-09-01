# 0017 — Revision-bound component catalogs and cross-object assembly

- Status: accepted for 0.15.1 implementation
- Date: 2026-09-01

## Context

Transaction-v10 can materialize an independent Mesh, extract a disconnected FACE
SelectionSet, and bind an exact object to an Armature. Large production selections may
contain hundreds of disconnected shells, however, and eagerly turning every shell into
a SelectionSet would exhaust the bounded session resource book. The resulting objects
also need exact Collection links and general object parenting before the existing
Mesh-only batch language can describe a complete modular assembly.

## Decision

Add a revision-bound `ComponentCatalog` resource. One catalog partitions a live FACE
SelectionSet by shared-edge connectivity and stores compact ordered face references plus
bounded metrics. Components receive session-local identities, but SelectionSets are
created only when the caller explicitly selects one or more identities. Catalogs are
invalidated by object, Mesh, users, revision, or fingerprint drift and are cleared on
file load or add-on restart.

Add typed Collection creation, object link/unlink, and object-level parenting. Every
writer requires exact identities and structure fingerprints, records a structural
delta, preserves Blender UI state, and participates in rollback, disconnect recovery,
and native-save adoption. Setting a new parent is limited to OBJECT parenting;
clearing an exactly inspected existing OBJECT or BONE parent is allowed.

Upgrade `mesh.batch.execute` rather than introduce a second batch language. Batch v3
can bind exact Object, Armature, Collection, and ComponentCatalog inputs and can compose
materialization, catalog selection, extraction, Collection organization, parenting,
and rig binding with the existing Mesh, UV, weight, transfer, and validation steps.
The response contains a generic assembly manifest and content hash. The manifest is
evidence only: it is not stored in Blender custom properties or treated as a persistent
project registry.

Upgrade transactions to capability 11, `mesh_batch` to 3, and add
`mesh_component_catalog`, `collection_authoring`, and `object_parenting` capability 1.
Protocol version 1 and existing request schemas remain compatible.

## Alternatives

- Creating one SelectionSet per connected shell was rejected because it would consume
  the 64-resource limit before the caller had chosen a useful region.
- A new `scene.batch.execute` was rejected because two overlapping declarative
  languages would duplicate alias, rollback, and compatibility semantics.
- Persistent scene custom properties were rejected because the bridge cannot own a
  project-specific module registry or silently alter saved metadata.
- `object.join`, Collection deletion, and Bone Parent creation were deferred because
  they require additional merge, ownership, and recovery contracts not needed for the
  0.15.1 assembly loop.

## Consequences

Agents can explain and choose disconnected regions without guessing face indices, then
organize and bind the resulting objects inside one reversible cross-object operation.
Catalog identities remain intentionally session-local and revision-bound. A repeated
transport request is idempotent; a fresh request with conflicting output names fails
before mutation instead of creating Blender-suffixed duplicates.

Library append and template workflows remain 0.16 work. Shape-Key structure writes,
Modifier Apply, arbitrary BMesh/RNA/Python, persistent module registries, and retopology
remain outside this authority.
