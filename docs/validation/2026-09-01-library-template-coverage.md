# Blender Research MCP 0.16.0 controlled Library and template coverage validation

## Scope

This record closes the 0.16.0 gate for SHA-bound local `.blend` Library inspection,
transactional single-root append, and Library-backed Mesh batch v4 composition. It
also records one real-character template-coverage regression. It does not claim
network asset import, Library Link or Override, script/driver execution, role-specific
fitting, reconstruction of hidden source anatomy, Shape-Key structure editing, or
Modifier Apply.

## Automated gate

The final release tree passed the uv-managed project gate:

```text
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync mypy
git diff --check
```

Results were `382 passed`, Ruff clean, mypy clean, and no whitespace errors. The
repository skill passed `skill-creator` quick validation; the managed installed copy
passed the project marker/synchronization check. `uv lock` succeeded for 0.16.0.
`uv sync` reached only the final executable replacement and then failed because the
current Codex process held `blender-research-mcp.exe` (Windows error 32). The already
synchronized dependency set was therefore used through `uv run --no-sync`; no cache,
link-mode, or package-manager workaround was introduced.

The Blender gate exposed and repaired four integration defects which isolated unit
fixtures had not demonstrated together:

- Blender local append retained a transient source `Library` ID, so the append
  closure now removes only the exact source record after proving that no supported
  imported ID still points to it;
- rollback previously read RNA names after `batch_remove`; labels are now frozen
  before datablock destruction;
- later Agent-owned parenting and rig binding now refresh earlier object-transform
  deltas instead of being mistaken for user conflicts;
- transaction Mesh snapshots and single-user working copies now refresh structural
  material guards, so their own temporary material users do not block commit or
  rollback.

## Blender 4.2.23 gate

The accepted isolated run is:

- report: `artifacts/live-smoke/20260901T064452Z-0e2ff64b/report-0.16.0.json`
- report SHA-256: `de1ff671ccc2861730b689eccadb29e9f25536afd8e6bad88dddece3bf7ca4223`
- elapsed: `26,154.91 ms`
- Blender: `4.2.23 LTS`
- server/add-on: `0.16.0`
- launch ID: `4eb80415-1360-4850-bad5-cb721a7c791a`
- PID: `42776`
- heartbeat: `6` before the gate and `157` after it
- capabilities: `transactions: 12`, `mesh_batch: 4`,
  `library_inspection: 1`, and `library_append: 1`

The generated Library and scene fixtures proved:

- `library.inspect` returned a deterministic, sorted Object/Collection/Mesh catalog
  without changing Blender data counts, scene generation, or collaborative UI state;
- exact Object append replayed one idempotency result without growing Object, Mesh,
  Material, or Armature counts, and ordinary rollback removed the complete local
  dependency closure;
- a constrained unsupported entry was rejected without residue;
- Mesh-root append committed, Collection-root append recovered after forced
  disconnect, and a native Blender save adopted the current visible append state;
- one batch v4 appended a Library collection, aligned its object through the typed
  object-setting kernel, prepared a dynamic evaluated SurfaceRef, selected and
  validated its Mesh, and performed exact rig binding;
- the batch rollback restored every appended ID, while a separate committed batch
  survived save/reload;
- the batch assembly manifest SHA-256 was
  `35fba8898447564122d86277a08496d5ad14f6a3a276dd6d42455ab78bc06a49`.

The saved/reloaded Eevee evidence is
`artifacts/live-smoke/20260901T064452Z-0e2ff64b/library-template-0.16.0.png`:

- bytes: `84,185`
- SHA-256: `ec02da5b05e2f25b9d8750d9a73ea6f63e0a4d4bc8dae21a7b39f0cb775b57c9`

The generated scene fixture remained unchanged at SHA-256
`eea265343b8b437289a7304fd3e42c1aac135e8838b50065ee8d4ed311b2ec52`.
The generated Library remained unchanged at SHA-256
`38c8b9efac84754c01333c17885437125858b845ea42a0282ebc51b91fa01d7d`.

## Real character template-coverage regression

The smoke copied `test-model.blend` into `%TEMP%`; the source remained unchanged at
SHA-256 `e9ce53fbb7bf0af8847eb2238dc080c55a48bca8459ed5ee7a588d12bcf8c059`
before and after the run.

The regression kept `绯雪_edit_mesh` as a read-only EVALUATED SurfaceRef, appended the
deterministic head cage as `MCP 0.16 Coverage Head`, and used a general local-plane
query to divide visible anchors from the hidden-prior region. It did not hard-code the
target character's component indices. The visible selection was projected to the
evaluated character surface, the `頭` weight group was transferred by nearest-surface
interpolation, and the result was bound exactly to `绯雪_edit_arm` before commit,
save, reload, and inspection.

Quantitative evidence:

- visible-anchor baseline p95 distance: `0.06213779468089342`;
- fitted p95 distance: `0.00004574834656523308`;
- fitted/baseline ratio: `0.0007362402672990277` (about `0.074%`);
- seam p95 limit: `0.000047310976377533876`;
- seam maximum limit: `0.00009462195275506775`;
- hidden-prior maximum displacement: `0.0`, below the `0.2276250412266101`
  body-diagonal-derived limit;
- new non-manifold, degenerate, self-intersection, and target-intersection counts:
  all `0`.

The evaluated character surface was not closed manifold, so
`sign_reliable=false`. The gate therefore records unsigned distance evidence and zero
target intersections; it deliberately does not invent a signed maximum-penetration
claim. Viewport evidence changed from SHA-256
`6341b9f56819f37af13ddbd65eecfdb534c9ffa838363035fc55b67ee5e253d8` to
`aac0069ce137a54f60327f31287e22a2bb677d1adaf6d9c8b01fb0d80711767f`
at `800 x 441`.

## Release artifacts

The final managed-runtime archives passed resource verification:

- `artifacts/blender-research-mcp-addon-0.16.0.zip`
  - SHA-256: `fdf44fb2b15221503e7d2299dd72c3b29d3d8c205fbd99bb9dade89831c38b81`
- `dist/blender_research_mcp-0.16.0-py3-none-any.whl`
  - SHA-256: `9444cca2da9969e47c514eb0cd68cf46d674152e58ef1f9803a363f6e58c2c1e`

The wheel and add-on ZIP contain the same managed 0.16.0 add-on sources, and the wheel
also contains the fixed session bootstrap.

## Result

0.16.0 is accepted for the implemented authority surface. Exact local Library
evidence and transactional append provide a controlled asset-ingress primitive, while
batch v4 can carry imported editable templates through typed alignment, dynamic
surface evidence, fitting, weight transfer, organization, rig binding, validation,
rollback, disconnect recovery, native-save adoption, and persistence. Hidden regions
remain explicit template priors rather than falsely reconstructed source geometry.
