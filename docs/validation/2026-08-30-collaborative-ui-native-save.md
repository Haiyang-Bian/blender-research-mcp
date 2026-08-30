# Blender 4.2.23 collaborative UI and native-save validation

- Status: passed
- Date: 2026-08-30
- Branch: `codex/semantic-mesh-editing`
- Run ID: `20260830T133730Z-94915a0d`
- Blender: `4.2.23 LTS`
- Add-on and external MCP server: `0.11.1`
- Protocol: `1`
- Transaction capability: `5`
- Port: `9887`
- Elapsed time: `13311.522 ms`

The release gate cold-launched the versioned managed add-on on an isolated port, then
opened a temporary copy of a freshly generated deterministic Mesh fixture with saved
UI and project scripts disabled. The managed process was PID `17716`, instance
`3b3ac1ec-b366-48a8-9686-952330e44723`, with launch ID
`414a17cc-b022-4c7d-b21b-81bfdc8fb355`. Heartbeat advanced from `3` to `118`.

## Collaborative UI context

An active transaction changed `Mesh Transform`, then the test-only main-thread command
changed view location/rotation/distance, perspective, viewport lens, Shading, Overlay,
selection, and active object. A second semantic write succeeded under that UI drift.
Rollback restored the exact object location while preserving every changed UI field;
the response returned `user_ui_preserved=true` and these paths:

```text
active_object
selected_objects
view.distance
view.lens
view.location
view.perspective
view.rotation
view.shading
view.show_overlays
```

A second transaction repeated the flow through commit. Both committed scene data and
the user's latest UI remained. A following managed `project.save` did not increment
`user_intent_revision` and did not create a native-save event.

## Native-save authority

One transaction combined three different deferred/snapshot domains:

- unlinked `Mesh Delete` for commit-time object deletion;
- marked `Evaluation Bevel` for commit-time Modifier deletion;
- edited vertex `0` of `Mesh Bevel`, retaining an internal Mesh baseline snapshot.

The private acceptance command invoked real `bpy.ops.wm.save_mainfile()`, so Blender's
persistent `save_pre/save_post` handlers followed the same path as Ctrl+S. Before file
serialization, adoption finalized the still-owned object and Modifier deletion and
discarded the zero-user internal Mesh snapshot. The recorded actions were
`delete_object`, `finalized_native_save`, and `discard_snapshot_native_save`.

Already queued-style write and rollback attempts for that transaction both returned
`TRANSACTION_ACCEPTED_BY_USER_SAVE`. Closing the bridge and waiting through the normal
disconnect grace period did not roll back any state. Reload confirmed the deleted
object and Modifier remained absent and the edited vertex, topology, UV/color layers,
material names, and protected attribute metadata persisted. Full Mesh fingerprint was
correctly reacquired rather than compared across reload because it contains session
material identities.

## Comparison save barrier

`lookdev.compare` began two ordered object-scale candidates. Immediately after writing
`saved-current`, the phase hook invoked the native save. Comparison returned
`COMPARISON_ACCEPTED_BY_USER_SAVE`, did not execute `must-not-run`, and skipped cleanup
rollback. Scale X `1.17999994754791` remained both in memory and after project reload.
The final `connection.ping` reported `user_intent_revision=2` and the successful native
save operation.

## Defect exposed by the real gate

The first live run showed that pending Modifier deletion was preserved instead of
finalized. The ownership helper compared Blender RNA Python proxy objects with `is`.
Blender can return distinct proxy wrappers for the same underlying Modifier owner, so
the check disagreed even though both session identities and Agent-written flags still
matched. Adoption now resolves the current object/Modifier and compares their RNA
session identities. A regression test uses a distinct object proxy with the same
underlying pointer.

An earlier harness assertion also used exact Python equality for a Blender float32
location; it now uses the same bounded tolerance as runtime evidence. The Mesh reload
check intentionally compares persistent fields rather than session-scoped material
identities.

## Evidence and release artifacts

The source fixture remained unchanged before and after with SHA-256
`630b3719304e435fa8157011a1a68cc422d9c166da46cbf391d9ddc3cc211867`.
The saved working project SHA-256 was
`9989857e0fbbd9d274659dce83dc5a7884fbff8a140cb0c7474cea6c9091a821`.
The ignored report is
`artifacts/live-smoke/20260830T133730Z-94915a0d/report-0.11.1.json`, SHA-256
`1a60b0f717f396462c4037a9c0af38e0d14262affbc879b74e73af9122090e5c`.

Final release artifacts:

- `artifacts/blender-research-mcp-addon-0.11.1.zip`, SHA-256
  `db1789984b94ab672f43483bd4c65ee6f6b86634db680e746c1a4005343e3f97`;
- `dist/blender_research_mcp-0.11.1-py3-none-any.whl`, SHA-256
  `ea83379e74c480b225b02e118533b756e8b9c7eba711d980a955c234a47a5e03`.

## Automated and skill gates

```text
uv run --no-sync pytest
245 passed

uv run --no-sync ruff check .
All checks passed!

uv run --no-sync mypy
Success: no issues found in 23 source files
```

The repository skill passed the skill-creator quick validator, and the project
installer refreshed then verified the marker-owned installed copy. `uv lock` updated
release metadata to 0.11.1. `uv sync` resolved and rebuilt the package, then stopped
only while replacing the current Codex-owned
`.venv\Scripts\blender-research-mcp.exe` (`os error 32`); dependencies were unchanged,
so all ordinary gates ran through `uv run --no-sync` as designed.
