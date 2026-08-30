# Decision 0011 — collaborative UI and native-save authority

- Status: accepted for 0.11.1
- Protocol: 1
- Transaction capability: 5

## Context

The user and Agent operate the same visible Blender process. View navigation, Shading,
Overlay, selection, and active-object changes help the user inspect Agent work but do
not mutate scene data. Treating those changes as transaction-wide context conflicts
caused otherwise valid writes and rollback to stop. Conversely, a native Blender save
is a deliberate user action that serializes the exact visible state and must outrank an
Agent's prior expectation that its transaction remains provisional.

MCP requests arrive through the add-on queue while Blender UI operators originate from
the application event loop. Both execute their data work on Blender's main thread. The
contract therefore follows actual execution order, not request creation time.

## Decision

Transaction context is split into three projections:

- hard context: Scene, View Layer, mode, frame, and active Camera;
- user UI: workspace/viewport identity, navigation matrices, distance, perspective,
  viewport lens, Shading, Overlay, selection, and active object;
- capture evidence: the matrices, projection, size, Shading, and Overlay used to make a
  specific image.

Only hard context and exact data evidence participate in transaction guards. Rollback
restores transaction data and Agent-created call-local UI debt, but never rewinds the
user UI projection. Comparison privately reuses baseline capture evidence for every
candidate, so user navigation cannot change its image basis.

Persistent `save_pre`, `save_post`, and `save_post_fail` handlers define a native-save
barrier. Before serialization, `save_pre` adopts the current transaction without normal
guard rejection or property rollback. It finalizes only deferred Agent deletion still
proven to match the Agent's last state, discards only unused internal snapshots, clears
rollback/idempotency ownership, and stores a bounded terminal transaction record.
Already queued writes, commit, or rollback then receive
`TRANSACTION_ACCEPTED_BY_USER_SAVE`. Comparison returns
`COMPARISON_ACCEPTED_BY_USER_SAVE`, skips cleanup rollback, and stops candidates.

MCP project lifecycle saves set an internal managed-save marker; they retain the
existing commit-before-save workflow and do not become native-save events. Native save
failure is reported but never followed by rollback or an automatic retry.

## Consequences

- Users can freely inspect an Agent's work without causing false transaction conflicts.
- True hard-context/data conflicts remain protected and report exact paths.
- A native save persists the current mixed user/Agent state, including user data edits;
  responsibility for that explicit action belongs to the user.
- Deferred deletion and internal snapshots require ownership-aware cleanup rather than
  unconditional finalization.
- Transaction capability 5 is required before a client promises these semantics; older
  add-ons continue to expose their earlier tools and stricter context behavior.

## Rejected alternatives

- Restoring the entire transaction-opening Blender UI on rollback: this erases useful
  user navigation and selection.
- Treating saves as ordinary data drift: serialization would already have made that
  state authoritative while a later rollback could silently diverge memory from disk.
- Canceling or repeating the user's save: this contradicts the user's direct intent and
  adds a second source of file-lifecycle actions.
