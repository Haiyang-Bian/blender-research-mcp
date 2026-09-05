# 0.17.2 Mesh recovery, edge lineage and UV/Pin Join hotfix

## Scope and result

This patch addresses the September 3 topology-error/weight-guard deadlock report
and the September 2 empty-material/native-Join-crash report. The earlier Collection
guard repair remains in place. Protocol 1 and all capability versions are unchanged.

Tests use generated fixtures and isolated Blender 4.2.23 processes. No current user
Blender session, character asset, or original modelling project was changed. This is
not a claim to have recovered an already-conflicted transaction in an old process,
or to have completed the separate real-character head/body-cage acceptance task.

## Reproduced causes and repairs

### Recovery changed identities that had not been edited

The error path rebuilt every Vertex Group even when the failed fill/grid-fill had
not changed a Group. Names, ordering and locks looked identical, but identities no
longer matched the guard from an earlier successful edit. Subsequent writes and
rollback therefore reported `MESH_WEIGHT_DATA_CONFLICT`.

Restoration now leaves identical Group schemas intact, writes weights only when
different, and verifies the restored values. A second reproduction found that
`Mesh.clear_geometry()` also removes Group definitions. Geometry snapshot recovery
now uses a verified BMesh write into the same Mesh, retaining Group identities.
Tests cover both rejection before geometry write and injected failure after write.
Genuine external Group edits still conflict; guards are not blindly refreshed.

### Valid fingerprints did not make incorrect lineage valid

The former pipeline constructed a Map in BMesh edge order, then called
`mesh.update(calc_edges=True)`. On a 65-by-65 grid, a local probe observed 9,345 of
9,348 edge endpoint records change index after that update; the smaller probe did
not expose it. The recorded after-fingerprint consequently described the live Mesh
but did not prove that the Map's indices described the intended descendants.

Completed BMesh writes now retain their edge table and check actual edge endpoints
and face order before publishing evidence. This applies to topology, legacy edits,
separation, Weld, and snapshot restoration. Join retains its once-built edge table
and sorts the branch EDGE SelectionSet without changing source-to-output Map order.

CustomData changes also invalidate Python BMesh wrappers while their underlying
elements survive. Lineage and component counts no longer use Python `id()` or old
wrapper `is_valid` as proof of native deletion. Native element keys are captured
before the operation and paired with explicit tags. Interpolated vertex integer
tags are not treated as exact ancestry: edge/face-derived new vertices are CREATED
in the vertex domain. No spatial guess is introduced.

### Join's missing material and unsafe UV write paths

Mixed material-bearing and slotless sources previously looked up a missing `None`
material identity. Preserve-by-identity now adds one empty output slot when required,
so unassigned faces remain unassigned. Explicit empty slots are deduplicated; fully
slotless or DROP output remains slotless.

A deterministic Join fixture with three UV layers and 761 Groups reproduced
`EXCEPTION_ACCESS_VIOLATION` on dependency-graph update. Removing UV copying made
the reproduction pass; removing weights alone did not. The former legacy UV-loop
write path retained wrappers across optional Pin/CustomData allocation. Join, Weld
UV restoration and snapshot UV restoration now use typed attribute bulk access and
fresh wrappers. Pin, coordinates, roles and seams are checked in real Blender.
The native allocation diagnosis is supported by these isolation runs, not by a
symbolized Blender crash dump or a claim about every possible native crash.

## Automated gate

The final gate reported **410 passed**, Ruff passed, mypy passed for 33 source files,
and `git diff --check` passed. Eleven focused Python regression cases execute the
actual recovery/lineage functions with controlled stand-ins; they do not pretend to
prove Blender native memory safety. Real-RNA tests supply that additional evidence.

```text
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync mypy
git diff --check
```

The first sandboxed pytest run encountered the known host temporary-directory
`WinError 5`; the same uv command passed through the authorized host path.
No cache relocation, direct virtualenv interpreter, or alternate environment was used.

`uv lock` resolved the unchanged 44-package set and updated the project to 0.17.2.
`uv sync` built the editable package but hit Windows error 32 while replacing the
MCP executable held by the active client. Ordinary execution continued with
`uv run --no-sync`; completing synchronization still requires that executable lock
to be released. The new add-on version was verified in the isolated process.

## Real Blender regression evidence

Primary command:

```powershell
uv run --no-sync python scripts/live_smoke_0172.py --blender-executable 'C:\Program Files\Blender Foundation\Blender 4.2\blender.exe' --port 9917
```

- Report: `artifacts/live-smoke/20260903T030110-7fe985a7/report.json`
- Report SHA-256: `53fd4372c4b45a0c23d372e26b16ca977c3cc5351684c46fcba0f69d2593e102`
- Real-RNA detail: `artifacts/live-smoke/20260903T030110-7fe985a7/rna.json`
- RNA SHA-256: `e1ec30dabbaf8f6019d16fb9daab0d03b608a18165b4d90e9783a8fc2ff914f6`
- Blender: `4.2.23 LTS`; server/add-on: `0.17.2`; transaction capability: `13`.
- PID: `25392`; launch ID: `c3df6bf5-5f60-4456-bcd1-1c5d499497f8`.
- Heartbeat advanced from `6` to `314`; captured mode, selection, active object,
  Workspace and viewport state remained equal before/after.
- Temporary source `.blend` SHA before/after:
  `cf359ac85f32220cdf57de00afc7cd7929c7f6075ffeca6bbcf3e191fae1df4e`.

The generated fixture includes three pinned UV layers, corner colors, seams/sharp
edges, and 761 Groups. For single-user, OBJECT single-userization and SHARED_DATA,
each run performs two TARGET merges and two boundary subdivisions, rejects invalid
fill/grid-fill, injects an error after writeback, continues editing and restores the
full baseline. Each subdivision verifies every mapped old edge as an endpoint-
connected chain: 8,579 and 9,344 relations per scope. Both Mesh and Group identity
evidence returns to baseline, shared links are restored, and peer state is protected.

Twelve Join construction/update/removal cycles cover slotless and explicit-empty-slot
sources with UV coordinates and pins checked per loop, every output edge checked
against source endpoints, and source fingerprints unchanged. Public socket tests
add sorted branch SelectionSets, idempotent replay, explicit rollback, disconnect
rollback, commit/save/reload, and native-save adoption. A queued rollback after
native save returns `TRANSACTION_ACCEPTED_BY_USER_SAVE`. Persisted topology, UV
fingerprints and weights were equal after reload.

Additional existing workflows rerun against this add-on:

| Workflow | Current-run evidence | Result |
| --- | --- | --- |
| Full 0.17 direct Join/Weld and batch-v5, including user conflict, native save, commit/reload | `artifacts/live-smoke/20260902T185857Z-f7404862/report-0.17.0.json` | Passed in one process; 16,880.358 ms |
| 0.14 official unwrap/pack, weight editing, attribute transfer, topology/batch disconnect and Shape-Key attribute/native-save fixture | `artifacts/live-smoke/20260902T185939Z-a1f78ddf/report-0.17.2.json` | Passed |

These are current runs of historical harnesses; their directory timestamps use UTC
whereas the primary harness uses local time. Their report SHA-256 values are,
respectively, `5e5856596ec77a5086ba715b4e6973c68eeae8821b5e44401c550141556b2a12`
and `c2cbe575f745039752fe939fac1dc16c61cd46fc2a3e27dd7a8e0f3c0ae9535b`.
The original real-character cage scenario remains separate and unclaimed here.

## Delivery and existing-session boundary

- `artifacts/blender-research-mcp-addon-0.17.2.zip`
  SHA-256 `61935cf5a127332638afc50a8d331dd5724d466e4b9c5bd849483f18c93368e4`.
- `dist/blender_research_mcp-0.17.2-py3-none-any.whl`
  SHA-256 `bcf54b8b938d0570eee05776241f6e062c49e634d3d9fce03078dc8ff126d097`.

Both archives passed `scripts/verify_release_artifacts.py`, including complete managed
add-on and bootstrap resources. Source, tests and documentation are committed locally;
no push, merge, branch cleanup, or modification of the user-supplied bug reports occurs.

An existing Blender process still running 0.17.1 does not hot-update when only Codex
restarts. Load the new add-on and confirm `connection.ping.addon_version` before
retrying. The patch does not rewrite old Map resources or force-resolve an already
conflicted transaction. Whether to keep visible user changes or reload a saved file
remains a user intent decision, not an automatic recovery step.
