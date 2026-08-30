# 0012 — Revision-bound selection and evaluated-surface resources

- Status: accepted for 0.12.0 implementation
- Date: 2026-08-30

## Context

Transaction-v4 Mesh snapshots make exact base-Mesh edits reversible, but callers still
have to page components, carry raw indices, and inspect again after every mutation.
That surface cannot express a large semantic region, a weighted falloff, or a fit
against an Armature/Shape-Key/Modifier-evaluated target without excessive transport
round trips. Component indices are not persistent identities, and the bridge still
limits requests to 1 MiB.

The next production use case is fitting writable eye-white/cornea proxy geometry to a
read-only evaluated eyelid surface. It requires reusable selections, nearest-surface
evidence, bounded deformation, and quantitative validation while preserving the
transaction-v5 collaborative viewport and native-save intent semantics.

## Decision

Add session-local immutable SelectionSet and SurfaceRef resources. A SelectionSet is
bound to the exact Blender instance, object and Mesh identities, complete user set,
full Mesh fingerprint, revision ID, component domain, sorted component indices, and
optional weights. It never changes Blender's UI selection. A SurfaceRef is bound to the
exact scene, view layer, frame, object transform, base-Mesh revision, and evaluated
triangle fingerprint and owns a bounded world-space BVH.

Derive `mesh_revision_id` from session and full Mesh evidence rather than treating a
monotonic number as proof. Every semantic write returns the after-revision and a
rebound SelectionSet. Old resources are rejected when their identity, users, or
fingerprint no longer match. File load and add-on restart clear all resources.

Upgrade transactions to capability version 6. Commit retains resources bound to the
committed revision; rollback restores the baseline revision; disconnect uses the same
rollback path. Native Blender save accepts the current transaction as before and stops
the active Agent workflow without undoing the saved state. View navigation, Shading,
Overlay, object selection, and active-object changes remain collaborative UI.

Keep protocol 1 and the 1 MiB request boundary. Large semantic operations target a
SelectionSet inside Blender. Explicit per-vertex coordinate arrays remain bounded to
4,096 vertices. Full topology maps, UV/weight authority, Shape-Key writes, Modifier
Apply, and declarative multi-operation plans remain later capabilities.

## Alternatives

- **Persist selections in Mesh attributes.** Rejected because it mutates user data,
  changes fingerprints, and couples Agent evidence to Blender UI/data cleanup.
- **Treat component indices as stable IDs.** Rejected because topology operations may
  create, delete, and reorder components.
- **Raise the request limit and send whole Mesh arrays.** Rejected for 0.12 because
  semantic region operations avoid the transport cost and remain independently
  inspectable.
- **Write directly to Shape-Key meshes.** Deferred because rollback must cover every
  Key Block and its animation/driver dependencies, not only the base Mesh.
- **Implement a generic batch executor immediately.** Deferred until revision and
  ComponentMap resources have been proven by atomic operations.

## Consequences

Agents can discover and reuse large weighted regions, query Shape-Key and
Modifier-evaluated targets without editing them, apply bounded topology-preserving
surface fits, and receive numeric quality evidence. The add-on owns bounded in-memory
resource books and must verify every resource on use. A 0.12 server keeps all 0.11
tools against an older add-on; only the new tools require their individual capability
and transaction version 6.
