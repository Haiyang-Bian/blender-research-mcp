# 0008 — unified typed object settings

- Status: accepted and automated/live-gate validated in 0.9.0
- Date: 2026-08-30

## Context

Version 0.8 can create Camera and Light objects and set complete object transforms, but
later edits are split across legacy object writers and creation-only data parameters.
Adding separate `light.set` and `camera.set` tools would make the public surface follow
Blender data-block classes rather than the user's intent to configure one scene object.
A generic RNA setter would avoid tool growth but would erase the closed schema,
type-specific ranges, shared-data scope, and transaction guards.

## Decision

Expose one public `object.set` tool with a closed union of transform, visibility, Light,
and Camera patches. A request validates every patch before applying any value, reserves
all transaction capacity, applies transform then visibility then object data, and
advances scene generation once. The implementation still dispatches to small typed
handlers internally; “unified” describes the public object-level operation, not an
untyped monolithic Blender handler.

Light and Camera patches require exact inspected data identity, type, and user count.
Shared data is rejected unless the caller supplies the unchanged scope and
`allow_shared_data=true`. Transaction deltas guard both the object and its data and
restore only the last Agent-written value. Controlled string enums and linear RGB
tuples are property values, but arbitrary RNA paths remain unavailable.

Keep lifecycle and structural responsibilities separate:

- `object.create`, `object.duplicate`, and `object.delete` own object structure;
- `scene.camera.set` owns the scene's active-Camera reference;
- material, World, Modifier, image, and render tools retain their domain contracts.

Legacy `object.transform` and `object.visibility.set` remain schema-compatible and use
the same setting kernel. `lookdev.compare` gains an `object_setting` target that routes
one typed locator through `object.set`; comparison still rolls every candidate back and
never commits, ranks, or saves it.

## Alternatives

- **Add `light.set` and `camera.set`.** Rejected because transform plus data changes for
  one object would require needless public-tool choreography and generation steps.
- **Put Camera and Light fields on `object.transform`.** Rejected because transform is
  an established compatibility schema and object-data sharing needs different guards.
- **Expose a generic RNA property setter.** Rejected because it bypasses closed types,
  capability negotiation, semantic ranges, and reviewable authority.
- **Merge creation, deletion, active Camera, materials, and World into `object.set`.**
  Rejected because those are structural or scene-level operations with distinct
  rollback and identity models.

## Consequences

Agents can coherently configure a Camera or Light position and its optical/lighting
data in one atomic request. The add-on transaction model now supports guarded strings
and RGB tuples as well as numbers and booleans. A 0.9 server keeps all 0.8 tools against
an older add-on, while `object.set` and `object_setting` comparison require
`object_settings: 1`.

Projection-type changes, Camera depth of field, arbitrary Light/RNA fields, Modifier
parameters, animation, mesh-component editing, and arbitrary Python remain outside the
0.9 authority boundary.
