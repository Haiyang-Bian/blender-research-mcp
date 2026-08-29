# 0005 — Bounded transactional LookDev writes

- Status: accepted for 0.5.1; live validation pending
- Date: 2026-08-29

## Context

The 0.4 bridge can identify an image point in evaluated geometry but can only preview
local object scale. Useful LookDev diagnosis also needs small changes to visibility,
modifier enable state, shape-key values, and material inputs. Exposing `bpy` or generic
RNA paths would remove the authority boundary that makes those previews reviewable and
safe to roll back.

Blender targets are mutable. Names alone do not prove that the object, modifier,
shape key, material, node, or socket is still the inspected item. Materials can also be
shared, so a visually local edit may affect several objects.

## Decision

Expose two bounded inspectors:

- `object.lookdev.inspect` lists object identity, visibility, up to 256 modifiers,
  up to 256 non-Basis shape keys, and up to 64 material slots;
- `material.inspect` lists one exact slot's material and node-tree identity, user and
  affected-object scope, and up to 256 input sockets with value, range, link, driver,
  read-only, type, and write-eligibility metadata.

Expose four new writers, all inside the existing single-owner transaction:

- `object.visibility.set`: absolute `hide_viewport` / `hide_render`;
- `modifier.set_state`: absolute `show_viewport` / `show_render`;
- `shape_key.set_value`: absolute finite value for a non-Basis, undriven MESH shape key
  within its existing slider range;
- `material.set_input`: absolute `default_value` for an unlinked, undriven, writable
  Float, Int, Boolean, Vector, or Color socket.

Every writer requires a current scene generation, a transaction ID, a request-specific
idempotency key, and all relevant session identities from inspection. The bridge does
not silently clamp values or convert JSON types. Linked-library targets are rejected.

A material with more than one user is rejected by default. The caller may proceed only
by supplying its exact current `expected_material_users` and `allow_shared=true`. The
result includes the discoverable affected object names. The bridge never creates an
implicit single-user material.

Transactions capability version advances to 2. The new capabilities are
`lookdev_inspection: 1`, `object_visibility: 1`, `modifier_state: 1`,
`shape_key_value: 1`, and `material_input: 1`; protocol 1 and framed transport remain
unchanged. A 0.5 server rejects older add-ons instead of silently omitting safeguards.

## Rollback and conflict behavior

The transaction book records typed Scale, Visibility, Modifier, ShapeKey, and
MaterialInput deltas. Its property guard tracks the last value written by the agent.
Rollback walks deltas in reverse, reaching the original value even when one property
was written several times.

Before commit, rollback, or another write, current context, target identities, and
agent-written property values must still match. `PROPERTY_CONFLICT` preserves a value
changed by the user; `TARGET_IDENTITY_CONFLICT` preserves a replacement target. There
is no force option. Disconnect rollback uses the same guards. Commit clears rollback
state but only retains changes in Blender memory; it never saves a `.blend` file.

## Alternatives considered

- Generic RNA-path assignment: rejected because it effectively recreates arbitrary
  Blender authority and makes validation dependent on caller-supplied paths.
- Automatic material single-user copy: rejected because it changes material topology,
  object bindings, and future maintenance semantics beyond the requested preview.
- Silent numeric conversion or clamping: rejected because structured before/after
  evidence would no longer represent the caller's exact request.
- Blender Undo as the only rollback mechanism: rejected because user and agent actions
  can interleave and Undo has broader scope than one allow-listed property.

## Consequences

The first write surface is deliberately narrower than Blender's UI. Agents must inspect
before writing and should change one research variable at a time. Shared material edits
are possible but conspicuous. Outputs remain bounded, and unsupported LookDev changes
require a future reviewed capability rather than a Python fallback.
