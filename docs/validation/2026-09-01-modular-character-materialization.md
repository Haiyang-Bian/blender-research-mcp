# Blender Research MCP 0.15.0 modular character validation

## Scope

This record closes the 0.15.0 gate for independent Mesh materialization,
multi-component extraction, and exact Armature binding. It does not claim Shape-Key
structure editing, Modifier Apply, hidden-surface reconstruction, Library append,
ComponentCatalog, generic parenting, or cross-object batches.

## Automated gate

The release tree passed the project gate with the uv-managed environment:

```text
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync mypy
```

The add-on source also passed the existing Python 3.11 AST/package checks. `uv lock`
succeeded for version 0.15.0. `uv sync` rebuilt the package but could not replace the
running `blender-research-mcp.exe` because the current Codex process held it open
(Windows error 32); ordinary validation therefore continued with the already-synced
dependencies through `uv run --no-sync`.

Final results were `358 passed in 13.97s`, Ruff clean, mypy clean, `git diff --check`
clean, skill quick-validation clean, and the managed installed skill tree identical to
the repository source.

## Blender 4.2.23 gate

The recorded run is:

- report: `artifacts/live-smoke/20260901T014957Z-cf0defbe/report-0.15.0.json`
- report SHA-256: `8b2035eca34a448d99eec21413c94ed0c5acc150c8f098b7301a6dde09955eff`
- elapsed: `77,243.592 ms`
- Blender: `4.2.23 LTS`
- server/add-on: `0.15.0`
- transaction capability: `10`
- ComponentMap capability: `3`
- launch ID: `991bb249-6363-4f50-88af-b41d628b3a28`
- managed resource hash:
  `7671cc985a312a8379aed4e7b40a0d7fb4e68f9af0474140238add447555e005`
- heartbeat: `6` before the gate and `145` after it

The deterministic fixture proved:

- BASE and SHAPE_KEYS_CURRENT produced topology-identical independent Meshes with
  exact MATERIALIZATION ComponentMaps;
- FINAL_EVALUATED baked the Armature and topology-changing Modifier and correctly
  returned no guessed lineage;
- materialize -> disconnected extract -> rig.bind rolled back as one transaction;
- the complete three-step chain also passed disconnect rollback, native-save adoption,
  commit, project save, reload, and exact post-reload rig inspection;
- source Shape Keys, Modifiers, parent, geometry, UVs, and weights remained unchanged;
- the private materialization path discarded unsupported custom split normals and
  generic attribute domains rather than leaking or claiming authority over them.

A final focused deterministic rerun additionally repeated each exact `rig.bind` before
disconnect, native save, and commit. All repeats returned `changed=false` and created no
additional Modifier:

- report: `artifacts/live-smoke/20260901T015833Z-b5ca34d1/report-0.15.0.json`
- report SHA-256: `876353c49a9fe82ae686158e99c0db439a1e21296a4fd7342d37cfc93447c309`
- elapsed: `14,179.473 ms`
- final heartbeat: `124`

The saved/reloaded Eevee evidence is
`artifacts/live-smoke/20260901T014957Z-cf0defbe/modular-0.15.png`:

- PNG bytes: `86,412`
- render time: `208.784 ms`
- SHA-256: `dc8cae89080f2bdb52390ab2814414476424d1560a397a10ea79d2a6e4057ca0`

## Real modular character regression

The run copied `test-model.blend` into the temporary directory and operated only on
that copy. Its source SHA-256 remained
`e9ce53fbb7bf0af8847eb2238dc080c55a48bca8459ed5ee7a588d12bcf8c059`
before and after the run.

`绯雪_edit_mesh` contained `98,158` vertices and `118,110` faces. The test:

1. materialized its current Shape Keys while excluding every source Modifier;
2. discovered hair slots from semantic material names (`12`, `14`, and `48`) rather
   than passing raw face indices;
3. selected `644` disconnected components containing `25,146` faces;
4. extracted them into one independent object, leaving `92,964` source faces;
5. preserved the extracted UV and deform-weight domains;
6. matched `759` existing groups to deform bones and created one exact Armature
   Modifier targeting `绯雪_edit_arm`;
7. rolled back the entire chain and verified both generated objects disappeared while
   the source Mesh and Shape-Key fingerprints returned exactly to baseline.

This is evidence that `mesh.extract` expresses a logical multi-shell module that the
single-connected-region `mesh.separate` contract intentionally cannot represent.

## Result

0.15.0 is accepted for the implemented P0 chain. The tools can create an independent
editable copy of stored, current-Shape-Key, or final evaluated geometry; extract a
logical disconnected module; and assemble existing weights against an exact Armature
without modifying or applying the source. Missing hidden body surface still requires a
separate template or cage workflow planned for later authority.

Release artifacts:

- `artifacts/blender-research-mcp-addon-0.15.0.zip` — SHA-256
  `5ee347f55ce2565388fb74b349315554b96b801f21f3696b6fa89681b74e0f85`
- `dist/blender_research_mcp-0.15.0-py3-none-any.whl` — SHA-256
  `de2d98a08bc533edeb09149c84d3f24aa505b7233e8dd3d4e467509ff86ad857`

The release verifier confirmed that both archives contain every managed add-on source;
the wheel also contains the fixed managed-launch bootstrap.
