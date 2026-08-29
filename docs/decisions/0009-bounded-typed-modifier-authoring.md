# 0009 — bounded typed Modifier-stack authoring

- Status: accepted and automated-gate validated in 0.10.0
- Date: 2026-08-30

## Context

Version 0.9 can create and configure scene objects but exposes only Modifier
viewport/render state. Useful static-scene work needs common non-destructive modeling
operations, yet a generic Modifier/RNA setter would erase type constraints, identity
guards, performance limits, and meaningful recovery. Applying a Modifier would also
require a destructive Mesh topology snapshot, which the transaction model does not yet
own.

## Decision

Add a separate `modifier.inspect/create/set/move/delete` domain for Bevel,
Subdivision, Solidify, and Boolean on Mesh objects. The public schema follows the
Modifier stack rather than merging these structural responsibilities into `object.set`.
Each write uses exact object and Modifier identities, expected type/index, and a SHA-256
fingerprint of the complete ordered stack.

Maintain one evolving transaction-level stack guard for each affected object. Agent
writes refresh its latest expected fingerprint. Commit, rollback, and disconnect
recovery proceed only while identity, ordering, protected public state, typed settings,
Boolean operand, and pending-delete state remain at the expected Agent value.

Deletion is deferred: disable and mark the Modifier during the transaction, restore
the same identity on rollback, and physically remove it at commit after all guards
pass. Creation rollback removes only the expected identity/structure. Movement restores
that identity to its original index. Setting is an atomic typed patch with verified
local restoration on partial failure.

Enforce deterministic geometry budgets and semantic dependencies. Boolean operands are
exact other Mesh identities and direct/transitive Boolean cycles are rejected. Keep
legacy `modifier.set_state` schema-compatible for any Modifier type. Add a
`modifier_setting` comparison target for one supported field, routed through
`modifier.set` and the existing per-candidate rollback protocol.

## Alternatives

- **Expose all Modifier RNA.** Rejected because it creates an effectively unbounded
  mutation surface with unstable constraints and recovery semantics.
- **Add fields to `object.set`.** Rejected because ordered stack structure, operand
  references, deferred deletion, and stack fingerprints are a distinct domain.
- **Include Modifier apply.** Deferred until a bounded Mesh topology snapshot and
  conflict model exist in 0.11.
- **Reject movement across unsupported items.** Rejected because exact ordered-stack
  guards allow safe movement without changing unsupported Modifier properties.
- **Copy the Mesh for every preview.** Rejected because it changes identity and sharing
  semantics and hides conflicts instead of guarding the live stack.

## Consequences

Agents can construct and review common non-destructive modeling stacks while each
operation remains observable, typed, retryable, and reversible. Unsupported Modifier
types remain inspectable and protected as ordered stack members but cannot be created
or parameterized. A 0.10 server preserves the 0.9 surface against older add-ons; only
the new domain requires `modifier_authoring: 1`.

The bridge still cannot apply Modifiers, edit mesh components, unwrap/transform UVs,
expose arbitrary RNA, or run arbitrary Python. Version 0.11 must design semantic Mesh
topology snapshots before destructive component editing; UV authority remains 0.12.
