# Blender Research MCP 0.13.1 separation and batch validation

## Scope

This record closes the 0.13.1 gate for exact ComponentMap composition, connected-face
object separation, declarative Mesh batches, automatic SelectionSet remapping,
whole-transaction failure recovery, disconnect rollback, native-save adoption, and
0.13 evaluated-surface regression coverage.

The new authorities remain bounded: no UV or weight writes, Shape-Key writes,
evaluated-Mesh materialization, Modifier Apply, arbitrary BMesh/RNA, operators, or
Python execution were added.

## Automated gates

The final branch state passed:

```text
uv run --no-sync pytest          311 passed
uv run --no-sync ruff check .   passed
uv run --no-sync mypy           passed (27 source files)
```

The repository skill passed `skill-creator` `quick_validate.py`; the managed-marker
installer refreshed the existing managed copy and `--check` passed. `uv lock` resolved
44 packages. `uv sync` built the 0.13.1 editable wheel but could not replace the running
`blender-research-mcp.exe` because the current Codex process held it (`os error 32`), so
ordinary gates continued through the already synchronized environment with
`uv run --no-sync` as planned.

## Blender 4.2.23 focused 0.13.1 acceptance

Run:

```text
uv run --no-sync python scripts/live_smoke_0131.py \
  --blender-executable "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe" \
  --port 9891
```

Result: passed with external server/add-on `0.13.1`, Blender `4.2.23 LTS`, and heartbeat
advancing from 6 to 81.

Evidence proved:

- a connected proper FACE subset split into SOURCE and SEPARATED maps with one shared
  separation ID, and explicit rollback restored the source fingerprint and removed the
  new object;
- a five-step declarative batch queried a face, separated a new target, queried its
  edges, subdivided them, automatically remapped current selections, composed the new
  branch Map chain, and passed degenerate validation while advancing generation once;
- closing the bridge after the successful batch triggered the normal disconnect grace
  rollback, restoring the source and removing the separated object;
- a validation assertion failed at step 1 after a Mesh write made before the batch;
  the returned underlying `MESH_BATCH_ASSERTION_FAILED` included `rolled_back` evidence
  for the complete transaction and the pre-batch fingerprint returned to its begin
  baseline;
- separating one object that shared Mesh data made only that target single-user; the
  peer retained its exact Mesh identity and fingerprint, and rollback restored sharing;
- two ComponentMaps created in two committed transactions composed into one
  `COMPOSED` Map with `step_count=2` and two ordered transaction IDs;
- a successful separation batch was accepted by the real Blender save handlers,
  reloaded from disk, and retained the one-face `Batch Saved Patch` object;
- the generated source fixture SHA-256 remained unchanged.

Report:

- `artifacts/live-smoke/20260830T193620Z-c391ad92/report-0.13.1.json`
- report SHA-256:
  `C8E202CB9EDA238B0FD2721633BE4FDF5B5C5B370928E0735C7F1E9F097F5B3B`

## Full 0.13 regression on `test-model.blend`

The current 0.13 regression harness was rerun against 0.13.1 rather than treating its
historical record as current evidence. It used an isolated `%TEMP%` copy of
`C:\Users\26687\Work\projects\blender-projects\test-model.blend` on port 9890.

Result: passed. It revalidated all seven bounded topology operators, legacy topology
lineage, continuous remapping, collaborative UI, conflict/native-save behavior,
disconnect rollback, and the independent eye proxy against the Shape-Key evaluated
`绯雪_edit_mesh` SurfaceRef. The proxy p95 distance ratio was
`0.250126618050456`; FRONT and RIGHT image hashes both changed; commit, save, reload,
and rebuilt distance evidence passed. Both the deterministic fixture and the real
source file remained unchanged.

The real source SHA-256 before and after was:
`E9CE53FBB7BF0AF8847EB2238DC080C55A48BCA8459ED5EE7A588D12BCF8C059`.

Report:

- `artifacts/live-smoke/20260830T193701Z-670a76ee/report-0.13.1.json`
- report SHA-256:
  `8A790527823F8972EC97FDA5B86A48F689B12AE73B2DD32F03230DDBFD212E69`

## Release artifacts

The release verifier confirmed that the ZIP contains every add-on Python source and
the wheel contains the same managed add-on plus the fixed bootstrap:

- `artifacts/blender-research-mcp-addon-0.13.1.zip`
  - SHA-256 `F35ECEFD8079D0D8836B31402A1F4D414FE8DF4CC026E44EB06117A86E61B8B3`
- `dist/blender_research_mcp-0.13.1-py3-none-any.whl`
  - SHA-256 `FF9CCFF6E60BD3E1961EA5A2804EC7341685C9B549DF51902EF0DA679BFC0DCF`

## Outcome

0.13.1 is accepted for the implemented boundary. ComponentMap composition never
guesses lineage, separation keeps branch authority explicit, and declarative batches
remain a Mesh-only orchestration surface with static preflight and whole-transaction
runtime recovery. UV/weight authority remains scheduled for 0.14; Shape-Key writes,
evaluated-Mesh materialization, and Modifier Apply remain scheduled for 0.15.
