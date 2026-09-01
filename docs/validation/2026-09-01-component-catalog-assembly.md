# Blender Research MCP 0.15.1 ComponentCatalog and assembly validation

## Scope

This record closes the 0.15.1 gate for revision-bound ComponentCatalog resources,
exact Collection links, general object parenting, and cross-object Mesh batch v3
assembly. It does not claim object join, Collection deletion, Bone Parent creation,
Library append, persistent manifests, Shape-Key editing, or Modifier Apply.

## Automated gate

The final release tree passed the uv-managed project gate:

```text
uv run --no-sync pytest -p no:cacheprovider
uv run --no-sync ruff check .
uv run --no-sync mypy
```

Results were `371 passed in 16.90s`, Ruff clean, and mypy clean. The repository skill
passed `skill-creator` quick validation, while the installed copy passed the project
managed-marker synchronization check. `uv lock` succeeded for 0.15.1. `uv sync` could not replace
the executable held by the current Codex process (Windows error 32), so ordinary
validation continued against the already-synchronized dependency set with
`uv run --no-sync`.

The implementation gate also found and repaired three integration defects that unit
fixtures alone did not expose:

- batch v3 separation policy normalization was attached to the wrong step branch;
- object inspection omitted the structure and Collection fingerprints required by
  the new parenting tools;
- batch preflight treated every source as topology-writable and computed Shape-Key
  evidence from the Mesh rather than the object, preventing the required
  `SHAPE_KEYS_CURRENT` materialization chain.

The final rule now validates initial batch targets as exact read evidence; each typed
write step still applies its own domain-specific write guards.

## Blender 4.2.23 gate

The accepted isolated run is:

- report: `artifacts/live-smoke/20260901T033436Z-9126b94d/report-0.15.1.json`
- report SHA-256: `d8853699ca8fe11e71b545b882bec759704eecbb6bd667534d887e065168e945`
- elapsed: `60,741.215 ms`
- Blender: `4.2.23 LTS`
- server/add-on: `0.15.1`
- launch ID: `15784dfa-25d9-4886-a0d2-c7379b7c970b`
- PID: `12044`
- heartbeat: `6` before the gate and `152` after it
- capabilities: `transactions: 11`, `mesh_batch: 3`,
  `mesh_component_catalog: 1`, `collection_authoring: 1`, and
  `object_parenting: 1`

The deterministic fixture proved:

- three disconnected shells were cataloged in stable order, paged, and selected by
  exact component identity; a Mesh revision change returned
  `MESH_COMPONENT_CATALOG_STALE`;
- nested Collections, link-before-unlink movement, last-link protection, and
  `KEEP_WORLD` / `KEEP_LOCAL` parenting restored exactly on rollback;
- one batch performed materialize, Catalog selection remap, multi-shell extract,
  Collection organization, parent set/clear, exact rig binding, and validation;
- the batch advanced scene generation once, and replaying the same payload and
  idempotency key retained identical resource counts and manifest SHA-256
  `a69848faf014e5a6a97a3bada88119559a0820ceeb258ca1aae3dee22bd22ff5`;
- an intentional `MESH_BATCH_ASSERTION_FAILED` restored the complete transaction
  baseline, while a forced disconnect restored newly created Collection structure;
- a native Blender save adopted the current visible transaction, and a separate
  committed batch survived save/reload with its Collection hierarchy, Mesh objects,
  parent, Armature Modifier, UVs, and weights intact.

The saved/reloaded Eevee evidence is
`artifacts/live-smoke/20260901T033436Z-9126b94d/assembly-0.15.1.png`:

- bytes: `86,414`
- SHA-256: `24556723f4d53de11ca8f93c501b0d765e718f525a788faec518c29442cf82e1`

The generated fixture source remained unchanged at SHA-256
`fab6d44f6403733c2a0adb7d58f51c882abc442399e24b5d5b289ad32d7d8589`.

## Real modular character regression

The smoke copied `test-model.blend` into `%TEMP%`; the source remained unchanged at
SHA-256 `e9ce53fbb7bf0af8847eb2238dc080c55a48bca8459ed5ee7a588d12bcf8c059`
before and after the run.

Material-name discovery selected the hair faces on `绯雪_edit_mesh`. ComponentCatalog
explained all `644` disconnected shells with content SHA-256
`bdb94b8f54ac18a382ca95f6c164ecccf2293551b42e012c852cfbee42c0fe83`.
The bounded batch then selected eight exact component identities, materialized the
current Shape-Key result, extracted those components, organized the new objects,
created one exact deform-bone group named `頭`, bound the module to `绯雪_edit_arm`,
and validated the group/bone match. It deliberately omitted copying the source's 761
unrelated deform groups so this cross-object call stayed within the fixed 30-second
protocol deadline.

The character assembly manifest SHA-256 was
`f8bd4319054fb7060064417b8e16f5065380d9cc820f17e8d88874885d579db1`.
The transaction rolled back successfully, both generated Mesh objects and Collections
disappeared, and the original Shape-Key Mesh fingerprint returned to baseline.

## Result

0.15.1 is accepted for the implemented authority surface. ComponentCatalog provides a
compact explanation layer for fragmented Mesh semantics; Collection and parent tools
provide exact reversible organization; and batch v3 can carry materialized/extracted
objects through organization, binding, validation, idempotent replay, rollback,
disconnect, native save, and persistence without storing a private manifest in the
`.blend` file.
