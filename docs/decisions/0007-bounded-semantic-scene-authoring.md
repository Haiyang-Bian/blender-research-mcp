# 0007 — bounded semantic scene authoring

- Status: accepted, automated-gate validated, and live validated in 0.8.0
- Date: 2026-08-29

## Context

The 0.5–0.7 bridge can inspect, preview selected existing properties, compare bounded
candidates, and manage Blender/project lifecycle. It cannot build even a simple static
scene without falling back to arbitrary Python. A useful authoring release must add
objects, materials, local textures, World/Camera setup, and reviewed output while
preserving the exact conflict and rollback semantics that make the bridge suitable for
a shared human/Agent Blender session.

## Decision

Adopt structural transaction capability version 3. It combines the existing property
deltas with typed structural deltas guarded by session identity, current users, and
supported-resource fingerprints. Creation, deferred object deletion, material slots,
fixed semantic node links, image color space, World state, and active Camera are
reversible. Commit validates every guard before finalizing destructive object removal;
rollback never overwrites a user conflict.

Expose a closed semantic authoring surface:

- a fixed set of primitives, Empty, Camera, and four light types;
- absolute location, XYZ Euler rotation, and scale;
- canonical Principled materials and exact material slots;
- absolute local image load plus seven fixed PBR texture channels;
- bounded World environment and active Camera controls;
- temporary Eevee Next preview and explicit PNG/EXR output.

A direct request to build or modify a static scene authorizes one coherent transaction,
preview, and in-memory commit. It does not implicitly authorize `.blend` saving unless
the request includes save/delivery intent. Render export is a separate explicit file
operation and may be retried after commit.

## Alternatives

- **Expose arbitrary Python or RNA.** Rejected because identities, rollback, and tool
  schemas would no longer describe the actual authority.
- **Add one opaque scene.apply batch.** Rejected because individual semantic operations
  would no longer be observable, idempotently retryable, or attributable on failure.
- **Use Blender Undo as rollback.** Rejected because user and Agent edits can interleave
  and Undo does not provide per-resource conflict guards.
- **Require confirmation for every object or material.** Rejected because the user's
  explicit scene-authoring request already states the desired mutation and would make a
  coherent build needlessly interactive.

## Consequences

0.8 can build and deliver bounded static scenes without generic code execution. The
transaction model and test surface are larger, and complex pre-existing node graphs are
rejected unless an exact supported link replacement is supplied. Mesh-component edits,
arbitrary nodes, modifiers, Geometry Nodes, animation, rigs, compositor controls,
Cycles, network downloads, and image pack/unpack/reload remain future separate
authority decisions rather than incidental extensions of this release.
