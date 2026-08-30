# Blender 4.2.23 topology revision and ComponentMap validation

- Status: passed, with signed penetration unavailable for the open real target
- Date: 2026-08-31
- Branch: `codex/topology-component-maps`
- Run ID: `20260830T181348Z-01dc0896`
- Blender: `4.2.23 LTS`
- Add-on and external MCP server: `0.13.0`
- Protocol: `1`
- Port: `9890`
- Elapsed time: `32687.601 ms`

The release gate cold-launched the managed add-on, generated a deterministic topology
fixture, and opened only temporary project copies. Blender ran as PID `30240`, instance
`c80e8c55-4b6e-438a-a35f-05ea4cc34d01`, with launch ID
`8b512772-5939-40d6-83f8-89d73ca339a0`. It advertised `transactions: 7`,
`mesh_topology: 2`, and `mesh_component_map: 1`. Heartbeat advanced from `3` to `248`.

## Bounded topology and exact lineage

The deterministic fixture exercised subdivide, quad-ring loop cut, plane bisect, face
split, two-loop bridge, ngon fill, and two-chain grid fill. Each operation returned a
one-revision ComponentMap, forward and reverse relation pages, an automatically rebound
SelectionSet, created-domain selections, protected-attribute evidence, and an exact
rollback. The older extrude and merge operations also returned maps. Merge proved a
real many-to-one lineage relation rather than a nearest-point reconstruction.

A two-step subdivision chain used the first response's rebound EDGE SelectionSet as the
second input. The first map remained inspectable as historical evidence but refused a
new remap after the second revision. The committed topology fingerprint changed from
`073fa6e57b3855b294b1281ce6d3c6415ffbd8fa0c0d5c3f72408fb7cda6dd68` to
`e941c3859941bf2016aadfc112759af248d5199447cf017299df9551cbd9467e` and survived
save/reload. A private UI hook changed active object, Shading, Overlay, view rotation,
projection, and distance between the two writes without invalidating the data transaction.

## Rollback, disconnect, conflict, and native save

Disconnect during an active subdivision restored the original Mesh and made remapping
through the after-map stale. A separate transaction then subdivided a Mesh, injected a
user coordinate edit, and correctly returned `MESH_DATA_CONFLICT` rather than replacing
the user's current state. Blender's native save adopted that mixed visible state;
subsequent rollback returned `TRANSACTION_ACCEPTED_BY_USER_SAVE`, reconnect did not
undo it, and reload reproduced the saved topology and coordinates.

No internal lineage layer was present in the returned protected-attribute evidence.
UV and color schemas remained present across edits and restoration. The fixture's
source `.blend` stayed byte-identical at SHA-256
`1b325ebbbc6715fd9dbff0727e4eaa13d25a5e4c9439b37fda2581c709ae8ec1`.

## Real evaluated eye-proxy workflow

A temporary copy of `test-model.blend` kept `绯雪_edit_mesh` as a read-only EVALUATED
SurfaceRef with 118,110 triangles. Generic inspection found the 1,986-vertex sclera
proxy; `object.duplicate(linked_data=false)` created `MCP 0.13 Eye Proxy` with an
independent Mesh. A query-built EDGE SelectionSet drove subdivision to 6,018 vertices.
The original VERTEX SelectionSet was remapped through that exact ComponentMap and
unioned with the map's created-vertex SelectionSet before deformation, so no new index
was guessed.

The 0.12 shrinkwrap/relax workflow then fitted the refined proxy to the evaluated body.
P95 distance fell from `0.0161492658779025` to `0.00403936125803739`, an after/before
ratio of `0.250126618050456` (74.99% lower), and remained identical after save/reload.
Non-manifold and degenerate evidence both remained `0 -> 0`. FRONT and RIGHT captures
both changed SHA-256:

- FRONT: `7f7ef2f5e2a6b9fd8c3bb1ed57df331f2b3dfa84aec915daf84842a080306bce`
  to `920c8186c2fba5c0c658a06fb6e434202752ab47ca373f2ec4cade59311d5d52`;
- RIGHT: `87af43d535ea87d5222cbd7628c65f497f1e1910070c3a198f33d6fb0ba50339`
  to `458bbe315c05217c0880ebd74a016457a36997ea7f15d39be2a8f40dc981e3bf`.

The evaluated target is consistently oriented but open, so the contract returned
`sign_reliable=false`; a numeric maximum-penetration assertion is intentionally not
made. The source `test-model.blend` remained byte-identical at SHA-256
`e9ce53fbb7bf0af8847eb2238dc080c55a48bca8459ed5ee7a588d12bcf8c059`.

## Automated, skill, and package gates

```text
uv run --no-sync pytest
298 passed

uv run --no-sync ruff check .
All checks passed!

uv run --no-sync mypy
Success: no issues found in 25 source files
```

The repository skill passed the `skill-creator` validator in an isolated uv environment.
The project-managed installed copy was refreshed only after its marker matched
`blender-research-mcp/blender-research-workflow`, and the installer `--check` passed.
Release resource verification passed for:

- `artifacts/blender-research-mcp-addon-0.13.0.zip`, SHA-256
  `f666a37ea7940c7684b7eeb0cd09c217d64a79d21f1819d6f5851c9c4f2fc8b9`;
- `dist/blender_research_mcp-0.13.0-py3-none-any.whl`, SHA-256
  `3c9ee65fb17c60505685d00855f296f7b14bfad628d1a392b98cb2d7ea93b118`.

`uv lock` resolved the unchanged dependency graph. `uv sync` stopped only while
replacing the currently running `.venv\\Scripts\\blender-research-mcp.exe` (`os error
32`); the dependency set was already synchronized, so all ordinary gates used
`uv run --no-sync` as required.

The ignored structured live report is
`artifacts/live-smoke/20260830T181348Z-01dc0896/report-0.13.0.json`, SHA-256
`730c93ef6945e44baf14a7aff3e446045055a759896b2ee2e35bda36ea4e483b`.
