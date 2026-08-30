# Blender 4.2.23 semantic Mesh editing validation

- Status: passed
- Date: 2026-08-30
- Branch: `codex/semantic-mesh-editing`
- Run ID: `20260830T121138Z-29818086`
- Blender: `4.2.23 LTS`
- Add-on and external MCP server: `0.11.0`
- Protocol: `1`
- Port: `9886`
- Elapsed time: `24461.008 ms`

The release gate cold-launched the versioned managed add-on on an isolated port and
opened a copy of a freshly generated deterministic Mesh fixture. The managed process
was PID `28444`, instance `aaaea48e-f89b-419c-a17e-921a1f11dec7`, with launch ID
`24ad1349-7779-4715-a9fc-3b2bfc4a4cce`. It advertised `transactions: 4` and
`mesh_topology: 1`. Heartbeat advanced from `3` to `214`, and the test reconnected to
the same instance after a forced bridge disconnect.

## Inspection and semantic operations

`mesh.inspect` returned two bounded vertex pages under one complete fingerprint. A
typed Bevel on the fixture made `object.geometry.inspect` report a different evaluated
vertex count from the base Mesh, proving the two inspection domains were not conflated.
A zero translation returned `changed=false`, delta count `0`, and unchanged generation.

Each public operation ran once with rollback and once with commit on an independent
UV/color/material fixture object:

- component transform;
- face region extrusion;
- face inset;
- edge bevel;
- delete;
- dissolve;
- vertex merge;
- face material/smooth settings;
- face-normal flip.

Every rollback reproduced its complete pre-write Mesh fingerprint. Every committed
operation produced and retained a distinct after-fingerprint. The edit responses also
recorded exact requested/created/deleted component pages and one generation step per
changed call.

## Sharing, conflicts, and disconnect recovery

Two objects initially shared one Mesh. `OBJECT` scope created a transaction-only
single-user Mesh for the target; rollback restored the original common data identity
and removed the copy. `SHARED_DATA` changed the common Mesh for both users, then
rollback restored their exact shared fingerprint, UV layers, color attribute, material
slots, selection/hide/sharp state, face state, and user set.

The private test hook independently changed a coordinate, added topology, and added a
new Mesh user after an Agent edit. Each rollback returned `MESH_DATA_CONFLICT` and
preserved the injected user state. The temporary project was reloaded between cases.

A separate connection drop occurred after an `OBJECT`-scope face extrusion on shared
data. After the rollback grace period, the client reconnected to the same Blender
instance. The target and peer again referenced the original shared Mesh, and the full
fingerprint, UV/color layers, material slots, protected attributes, active object,
selection, OBJECT mode, Layout workspace, and Scene context matched the pre-write
evidence.

## Persistence and render evidence

The nine committed models were saved to the temporary project, reloaded with scripts
and saved UI disabled, and re-inspected. Session identities and the full fingerprint
were deliberately reacquired after reload because material-slot identities make the
full fingerprint session-scoped. Topology fingerprints, user names, UV/color layers,
material names, and protected attribute metadata persisted.

A post-reload 512×384, 24-sample Eevee Next render restored its temporary settings and
produced 229,055 PNG bytes in 332.219 ms. Its SHA-256 was
`1c730373dbb775d396c6a40f7ef369a6379c479f2611eb9af92d6eba572d4561`.

The source fixture remained unchanged before/after with SHA-256
`bbda2cc6142feef9d2640aa2990fd028f11027545823270f103cf5140e99431f`.
The saved working project SHA-256 was
`7d4e32c9439419a96f7ce4f907ef86cce559ac761f18ca1d5bdff9ff2761bf99`.
The ignored report is
`artifacts/live-smoke/20260830T121138Z-29818086/report-0.11.0.json`, SHA-256
`bde9d37a922e17a9c826c927c02513f380b2f4db800a9cbe043d28c5bd348a10`.

## Defects exposed by the real gate

Two issues absent from fake-client and direct-function tests were corrected before the
passing run:

1. Blender primitive Meshes with native UV data could make an identity transform look
   changed after an unnecessary BMesh roundtrip. Identity transforms now validate
   exact target indices and return before snapshot creation or writeback.
2. Restoring a `Mesh.copy()` through BMesh did not reproduce native UV-era Mesh state
   exactly. Restore now rebuilds vertices, edges, loops, polygons, materials, and
   supported protected attributes from the snapshot into the same Mesh identity, then
   writes selection/hide/sharp/material/smooth flags after Blender's final topology
   update. The production fingerprint verifies the result.

## Automated and package gates

```text
uv run --no-sync pytest
230 passed

uv run --no-sync ruff check .
All checks passed!

uv run --no-sync mypy
Success: no issues found in 22 source files
```

The repository skill passed `skill-creator` validation; its managed installed copy
passed the project installer check. Release resource verification passed for:

- `artifacts/blender-research-mcp-addon-0.11.0.zip`, SHA-256
  `d46f899057b2bc12a15bec99716bc10927a63e6dbe8ea73dd79c83015498e729`;
- `dist/blender_research_mcp-0.11.0-py3-none-any.whl`, SHA-256
  `8ec43f38f19f6ee68003fd0c03659b4a5e00582218d6d0ba440ae6001060ae95`.

`uv lock` advanced release metadata to 0.11.0. `uv sync` resolved and built the
package, then stopped only while replacing the currently executing
`.venv\Scripts\blender-research-mcp.exe` (`os error 32`). Dependencies were unchanged,
so the synchronized environment ran every ordinary gate through `uv run --no-sync`.
