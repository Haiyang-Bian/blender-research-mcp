# Linked-data duplication invalidates a transaction's own structural guard

- Status: resolved
- Severity: high; blocks both commit and rollback
- First observed: 2026-08-30
- Affected package/add-on version: 0.10.0
- Fixed package/add-on version: 0.10.1
- Protocol: 1
- Blender: 4.2.23 LTS
- Platform: Windows
- Capability: structural transactions v3 / semantic scene authoring

## Summary

Within one structural transaction, a successful `object.duplicate` call with
`linked_data=true` can change the `users` count of a Mesh that was already guarded by
an earlier operation. The duplicate operation does not refresh that Mesh guard. The
next structural operation therefore treats the transaction's own legitimate change as
an external conflict.

The transaction then becomes stuck: both `transaction.commit` and
`transaction.rollback` validate the same stale guard before doing any finalization or
restoration, so neither operation can close the transaction.

This is an internal transaction-consistency defect, not a Blender failure and not a
real concurrent user edit.

## Impact

- A coherent static-scene build can stop after the first linked duplicate.
- The caller cannot retain the already validated work through commit.
- The caller cannot safely restore the transaction through rollback.
- Project save also cannot recover the work because lifecycle save commits the active
  transaction first.
- Recovery requires an explicitly authorized project reload or application close that
  discards unsaved work.

The issue is not limited to stars or a particular scene. Any in-transaction operation
that legitimately changes the user count of an already guarded Blender data-block can
produce the same class of failure if it does not refresh the transaction guard.

## Live reproduction

The defect was reproduced while building a new moon-over-water scene using only the
public semantic MCP tools. No arbitrary Python or direct Blender RNA mutation was used.

### Preconditions

1. Start Blender 4.2.23 LTS with Blender Research MCP add-on 0.10.0.
2. Open or save a project.
3. Create a Mesh object named `Star_Master` whose Mesh data has one user.
4. Create a material and assign it to `Star_Master` within the active structural
   transaction. This establishes a Mesh structural guard with `users = 1`.

### Steps

1. Begin a structural transaction.
2. Call `material.assign` for `Star_Master`.
3. Call `object.duplicate` with:
   - `source_name = "Star_Master"`
   - `linked_data = true`
   - `name = "Star_01"`
4. Observe that the first duplicate succeeds and `Star_Master Mesh.users` becomes 2.
5. Call `object.duplicate` again with the same source and a unique name.
6. Attempt `transaction.commit`.
7. Attempt `transaction.rollback`.

### Observed result

The second duplicate fails:

```text
STRUCTURE_CONFLICT: Structural resource users changed: mesh Star_Master Mesh
```

Commit fails with the same error:

```text
STRUCTURE_CONFLICT: Structural resource users changed: mesh Star_Master Mesh
```

Rollback also fails with the same error:

```text
STRUCTURE_CONFLICT: Structural resource users changed: mesh Star_Master Mesh
```

At the failure point, live inspection reported:

- transaction status: `active`
- scene generation: `28`
- transaction delta count: `29`
- `Star_Master Mesh.users`: `2`
- `Star_Master` and `Star_01` shared the same Mesh session identity

No user operation or out-of-band Blender mutation occurred between these calls.

## Expected result

- The first linked duplicate should update the transaction's expected structural state
  for the shared Mesh.
- Additional linked duplicates in the same transaction should succeed when no external
  edit occurred.
- Commit should retain all requested duplicates.
- Rollback should remove all created duplicates and restore the original Mesh user
  count.
- A genuine out-of-transaction change to the Mesh or its user count should still be
  detected as a conflict.

## Root-cause hypothesis

`object.duplicate` leaves `duplicate.data` linked to `source.data` when
`linked_data=true`. Linking or retaining that object legitimately increments the
shared Mesh's Blender `users` count.

The duplicate path records a structural delta and guard for the newly created Object,
but it does not refresh a pre-existing guard for `source.data`. An earlier material
assignment has already guarded that Mesh with `users = 1`.

Later, `validate_structure_guard` compares the current resource user count with the
stale value and raises `STRUCTURE_CONFLICT`. Commit and rollback both validate all
transaction guards before finalizing or reversing structural deltas, which turns the
stale guard into a transaction deadlock.

Relevant implementation areas:

- `blender_addon/blender_research_mcp_addon/authoring_ops.py`
  - linked versus independent object duplication
  - creation of the `object_duplicate` structural delta
- `blender_addon/blender_research_mcp_addon/structural_ops.py`
  - `make_structure_guard`
  - `validate_structure_guard`
  - `refresh_structure_guard_if_present`
- `blender_addon/blender_research_mcp_addon/state.py`
  - validation performed by commit, rollback, and lifecycle commit

## Suggested repair

After a successful linked-data duplication reaches its final Blender state, refresh the
transaction guard for the shared data-block when that guard is already present. The
refresh must happen inside the semantic operation and only after all writes succeed.

More generally, every structural operation that legitimately changes a guarded
resource's `users` count should either:

1. refresh that resource's guard to the exact post-operation state; or
2. record and validate a transaction-owned expected user-count delta.

The repair must preserve conflict detection for real external changes. Removing
`users` from every structural guard would be too broad and would weaken the current
shared-state safety contract.

Rollback should validate against the most recent Agent-owned state, remove the created
linked Objects in reverse order, and finish with the original data-block user count.

## Required regression tests

1. Assign a material, create two `linked_data=true` duplicates in one transaction, and
   commit successfully.
2. Repeat the same sequence and roll back; verify both duplicates are gone and the
   source Mesh user count is restored.
3. Create three linked duplicates without an intervening material operation and verify
   commit and rollback.
4. Verify `linked_data=false` duplication remains unaffected.
5. Introduce a real external user-count change after the latest Agent write and verify
   that commit and rollback still report a structural conflict.
6. Verify lifecycle save can commit a valid active transaction containing linked
   duplicates.
7. Verify context, selection, active object, mode, and UI responsiveness remain
   unchanged across the successful commit and rollback cases.

## Acceptance criteria

- Multiple linked duplicates can be created in one structural transaction.
- Commit and rollback always remain available after valid Agent-owned operations.
- Rollback restores exact Object existence, Mesh identity, Mesh users, and user context.
- Genuine concurrent structural changes are still rejected.
- The full automated quality gate passes.
- A Blender 4.2.23 live smoke test records successful linked-duplicate commit and
  rollback with a responsive UI.

## Resolution

The 0.10.1 patch refreshes any existing shared data-block structural guard after a
successful linked duplicate reaches its final state. It also refreshes the exact
Light/Camera object-data user-count guard when present. No new data guard is invented,
and an external user-count change after the latest Agent write still returns
`STRUCTURE_CONFLICT` from both commit and rollback.

Real Blender testing also exposed a related selection persistence defect: an Object
copied from a selected source could become selected after save/reload. The duplicate
path now explicitly deselects the newly linked object.

All required automated and Blender 4.2.23 cases passed. See
[the 0.10.1 validation record](../validation/2026-08-30-linked-data-guard-hotfix.md).

## Historical incident recovery

The original 0.10.0 transaction could not be closed by commit or rollback. After the
Codex restart there was no compatible Blender session running, so no incident
transaction remained. The defect was reproduced again in a disposable untitled
session through the public MCP tools and that test session was explicitly closed with
`save_current=false` before the fixed acceptance runs.
