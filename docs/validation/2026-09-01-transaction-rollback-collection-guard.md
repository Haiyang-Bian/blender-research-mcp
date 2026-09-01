# Blender Research MCP 0.17.1 Collection rollback and UV inspection validation

## Scope and result

This patch closes the reported failure where a transaction created a nested
Collection, materialized an object into it, and then rejected its own rollback as a
Collection structure conflict. It also removes the adjacent failure mode in which a
large `mesh.uv.inspect` request exceeded the ordinary five-second request deadline,
the client retried or disconnected, and the automatic rollback exposed the stale
Collection guard.

The repair is accepted for transaction-owned Collection placement, explicit rollback,
disconnect rollback, and bounded real-character UV inspection. It does not weaken
external structure-conflict protection and does not claim recovery of the user's
already-conflicted 0.17.0 Blender process.

## Root cause and repair

`collection.create` recorded the new Collection's structure fingerprint. Later
same-transaction writers linked objects into that Collection but did not advance the
existing transaction guard. Rollback validates all structure guards before restoring
deltas, so it correctly observed a fingerprint difference but incorrectly classified
Agent-owned progress as an external edit.

All typed object-placement paths now refresh an existing Collection guard after their
own successful link or unlink: object create/duplicate/delete, materialize, separation,
join, and Library append. Tests prove both halves of the contract: later Agent-owned
placement rolls back, while a subsequent external object link still returns a
structure conflict and preserves that external state.

`mesh.uv.inspect` now computes global island and degenerate-face aggregates only for
`SUMMARY`. Paged `LOOPS`, `FACES`, and `SEAMS` requests do only page-bounded component
work and return `MESH_UV_GLOBAL_METRICS_DEFERRED` when those aggregates were omitted.
The Mesh fingerprint is computed once per request, the MCP route uses the existing
30-second maximum deadline, and client-side `REQUEST_TIMEOUT` is no longer retried or
masked as `CONNECTION_LOST`.

## Automated gate

The final local tree passed:

```text
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync mypy
git diff --check
```

Pytest reported `399 passed`. The first sandboxed run reported 38 setup errors because
the sandbox could not scan the host pytest temporary directory and returned Windows
`WinError 5`; rerunning the same uv command through the authorized host path passed.
No project-local cache, direct interpreter, pip, or alternate environment was used.

`uv lock --check` resolved the unchanged 44-package set. `uv sync` reached the final
replacement of `.venv\Scripts\blender-research-mcp.exe`, which the active Codex process
still holds, and Windows returned error 32. The already synchronized dependency set was
therefore used through `uv run --no-sync`; no alternate cache or package workflow was
introduced.

## Blender 4.2.23 live gate

The accepted isolated run is:

- report: `artifacts/live-smoke/20260901T123426Z-c4c272a2/report-0.17.1.json`
- report SHA-256: `61e953995b00eca063970b9c74c4180d720e8e0886e969436ee9a01f43c2ee83`
- report bytes: `41,858`
- elapsed: `29,907.791 ms`
- Blender: `4.2.23 LTS`
- server/add-on: `0.17.1`
- PID: `42880`
- launch ID: `c0259f15-162c-4a58-add0-91b7a0cd211f`
- initial heartbeat: `6`

The deterministic fixture created nested Collections, materialized into the child, and
completed an explicit rollback with status `rolled_back`. A second transaction changed
Collection contents and then disconnected; reconnect reported
`rolled_back_disconnect`.

The run then opened a temporary copy of
`C:\Users\26687\Work\projects\blender-projects\test-model.blend`, created nested
Collections, materialized `绯雪_edit_mesh` with current Shape Keys, and inspected its
four UV layers. `SUMMARY` completed in `5,164.788 ms` and found 3,866 islands over the
full Mesh. A `LOOPS` page returned 32 of 354,330 loops in `2,096.310 ms`, reported
truncation, and explicitly deferred island and degenerate-face aggregates. The complete
character transaction rolled back with status `rolled_back`, and the source Mesh
fingerprint returned to baseline.

The source character file remained unchanged before and after the run at SHA-256
`e76d9df774e525bd790b956c5572d90f2e0441aded6a6a8bb5a1ff34368cbc29`.

## Recovery note

The patch prevents future occurrences after the 0.17.1 add-on is installed or used by
managed launch. It cannot safely rewrite an already-conflicted 0.17.0 transaction:
that process may contain a mixture of Agent and user state that the old guard can no
longer prove. If that original session is still open, do not save it merely to repair
the transaction; reload the intended project state without saving, then retry under
0.17.1.

## Release artifacts

- `artifacts/blender-research-mcp-addon-0.17.1.zip`
  - bytes: `226,387`
  - SHA-256: `44935cb10dc37dc2cb909da42428b0beeda2805358729b558a94b8c71bda6f28`
- `dist/blender_research_mcp-0.17.1-py3-none-any.whl`
  - bytes: `328,069`
  - SHA-256: `0b206a7d962ce8a87369a8e377b4b38711d0197af37ae636e52f797e377e685b`

`scripts/verify_release_artifacts.py` accepted both archives and confirmed that the
wheel contains the fixed managed-launch bootstrap and complete add-on source set.
