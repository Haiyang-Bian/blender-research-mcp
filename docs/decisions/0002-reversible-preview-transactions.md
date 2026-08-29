# 0002 — Reversible single-owner preview transactions

- Status: accepted
- Date: 2026-08-28

## Context

The user and bridge share mutable Blender data and UI context. Blender Undo is
not a sufficient ownership contract because user and agent edits can interleave,
and a lost socket response does not prove whether a mutation ran.

## Decision

- Allow one active preview transaction per Blender instance.
- Require the expected scene generation and an idempotency key for every
  transaction command and bounded scene mutation.
- Record exact property before/after values and roll them back in reverse order.
- Reject commit, further mutation, or rollback if the user context or an
  agent-written property no longer matches its transaction guard.
- Do not expose a force option that could overwrite user work.
- Treat transaction commit as an in-memory operation only; this command never saves a
  blend file. Explicit project lifecycle tools are a separate authority added in 0.7.
- On authenticated socket loss, allow two seconds for an idempotent reconnect.
  After that grace period, attempt rollback on Blender's main thread and
  invalidate cached success responses associated with that transaction.
- Use Blender Undo only as a future secondary mechanism, not as the protocol
  contract.

## Consequences

Long-running previews block a second transaction. A conflicted transaction
requires the user or client to restore the guarded context/property before
retrying rollback. A Blender crash cannot be repaired by the bridge, so live
acceptance must use a temporary copy of the integration scene.
