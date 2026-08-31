# Blender Research MCP 0.14.0 UV and skin-weight validation

## Scope

This record closes the 0.14.0 gate for exact UV-layer authority, isolated official
unwrap/pack, Vertex Group and sparse deform-weight authoring, topology/nearest
attribute transfer, attribute-aware topology migration, attribute validation, batch
composition, Shape-Key attribute writes, rollback, disconnect recovery, native-save
adoption, and persisted reload evidence.

The release remains bounded: it does not add Shape-Key value authoring beyond the
existing scalar tool, evaluated-Mesh materialization, Modifier Apply, arbitrary
BMesh/RNA/Python, custom split-normal writes, generic attributes, or retopology.

## Automated gates

The final branch state passed:

```text
uv run --no-sync pytest          345 passed
uv run --no-sync ruff check .   passed
uv run --no-sync mypy           passed (28 source files)
```

The repository skill passed `skill-creator` `quick_validate.py`; the managed-marker
installer refreshed only the managed `blender-research-workflow` copy and `--check`
passed. `uv lock` completed. `uv sync` could not replace the running
`blender-research-mcp.exe` because the current Codex process held it (`os error 32`),
so the already synchronized environment continued through `uv run --no-sync` as
planned.

## Blender 4.2.23 acceptance

Run:

```text
uv run --no-sync python scripts/live_smoke_014.py \
  --blender-executable "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe" \
  --character-project "C:\Users\26687\Work\projects\blender-projects\test-model.blend" \
  --port 9892 --timeout 120
```

Result: passed in `83,757.442 ms` with server/add-on `0.14.0`, Blender
`4.2.23 LTS`, transaction capability `9`, the seven new or upgraded Mesh attribute
capabilities, and heartbeat advancing from 6 to 137.

The deterministic fixture proved:

- official ANGLE_BASED unwrap and island packing run on isolated temporary Mesh
  state, followed by an exact UV rollback including layer roles, pins, seams, and
  coordinates;
- a deterministic checker render changed from SHA-256
  `05FAAEE1D6924A891D74177E75C150962451C39D0174F9DB64FFF6AD004B20BB`
  to `7D4A600C9665A0D049CE3E872FD89BB9F335860BBB46E4CF26BC1CE500F337C2`,
  with maximum channel difference 157 and non-zero per-channel mean difference;
- a real Armature evaluation changed its world bounds after a weight edit, and exact
  rollback restored the sparse weight fingerprint;
- exact-topology UV transfer and barycentric nearest-surface weight transfer both
  changed the target and rolled back to the original fingerprints;
- subdivide preserved/interpolated UV and weight data, then restored UV coordinates,
  Pin state, Seam state, Group schema, and sparse weights on rollback;
- an attribute-aware batch advanced generation once, and closing the bridge triggered
  disconnect rollback for both UV and weight changes;
- a Shape-Key Mesh accepted topology-stable weight writes, native save adopted the
  current transaction, and reload retained the committed Group.

The real `test-model.blend` regression used an isolated temporary copy. On
`绯雪_edit_mesh` it confirmed geometry remained topology-read-only while UV and weight
domains were writable. The Mesh contained 98,158 vertices, 118,110 faces, Shape Keys,
and 761 Vertex Groups. One exact UV corner and one exact existing `頭` Group weight
were changed and independently rolled back. A second exact weight was committed,
saved through Blender's native save handlers, reloaded, and retained fingerprint
`777A0A3F371A0DF9BD0C7919800EF88ED16DF95B36C04B8226EB5E456AFD7EDF`.

Both generated fixture source and real character source remained unchanged. The real
source SHA-256 before and after was
`E9CE53FBB7BF0AF8847EB2238DC080C55A48BCA8459ED5EE7A588D12BCF8C059`.

Report:

- `artifacts/live-smoke/20260831T135601Z-f5e666bc/report-0.14.0.json`
- report SHA-256:
  `EAEF039DB5BA6D691D6F65BCC6D33C6CFCFB45EDD2573DAB3EC959C68C4BACF1`

## Bugs found by the live gate

The Blender gate found and fixed four production defects that the fake-runtime tests
could not expose:

1. official `pack_islands` required explicit temporary UV-loop selection even when
   Mesh faces were selected;
2. deleting UV Layers through a tuple of Blender RNA wrappers retargeted later
   wrappers to hidden `.pn.*` attributes; current-last removal is now used;
3. Seam values had to be restored after `mesh.update()` rebuilt topology;
4. large character rollback had to rebuild Vertex Groups from the current last item
   and preserve explicitly assigned zero-weight sparse entries. The weight writer also
   reuses one pre-write capture instead of scanning the full sparse table repeatedly.

## Release artifacts

The release verifier confirmed that the ZIP contains every add-on Python source and
the wheel contains the same managed add-on plus bootstrap:

- `artifacts/blender-research-mcp-addon-0.14.0.zip`
  - 171,973 bytes
  - SHA-256 `AE00709B483D578BC53AAE7FB5859319DE5D3314FB38FF7D020383F42008FA63`
- `dist/blender_research_mcp-0.14.0-py3-none-any.whl`
  - 257,375 bytes
  - SHA-256 `76B7129E3571FB7E7FDE0F5DA6E19585FE2B42A390611FD21C7346B1D3CDED0F`

## Outcome

0.14.0 is accepted for its implemented boundary. UV layout, seams, pins, layer roles,
Vertex Group schema, sparse deform weights, transfer, validation, topology migration,
and batch orchestration now share the same revision-aware transaction model. Shape-Key
Mesh data is writable only in topology-stable attribute domains; Shape-Key authoring,
evaluated-Mesh materialization, and Modifier Apply remain future work.
