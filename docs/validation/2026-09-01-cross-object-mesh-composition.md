# Blender Research MCP 0.17.0 cross-object Mesh composition validation

## Scope and result

This record accepts the deterministic 0.17.0 commit path for exact base-Mesh Join,
explicit boundary Weld, JOIN_BRANCH composition, batch v5, save, and reload. It does
not claim that the extended same-process stress sequence or the real-character
head/body-cage composition gate is closed. Those two items remain recorded below
rather than being inferred from the deterministic fixture or from the unchanged
character source hash.

## Automated gate

The final local release tree passed:

```text
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync mypy
git diff --check
```

Results were `394 passed`, Ruff clean, mypy clean, and no whitespace errors. Add-on
sources still parse as Python 3.11. The repository skill passed `skill-creator`
quick validation with an uv-provided transient `PyYAML`, and the project installer
refreshed and then verified the managed installed copy.

`uv lock` resolved the unchanged 44-package set. `uv sync` again reached only the
final replacement of `blender-research-mcp.exe`; the active Codex process held that
file and Windows returned error 32. The already synchronized dependencies were used
through `uv run --no-sync`; no alternate cache, link mode, direct interpreter, pip,
or environment workaround was introduced.

## Blender 4.2.23 deterministic gate

The accepted run is:

- report: `artifacts/live-smoke/20260901T110558Z-0466632d/report-0.17.0.json`
- report SHA-256: `daa359aaff6b3cf89423309501ba6f08b3fce4d743f04f9940a18dafb8200029`
- elapsed: `3,442.585 ms`
- Blender: `4.2.23 LTS`
- server/add-on: `0.17.0`
- PID: `42320`
- launch ID: `7b571c93-8e6c-43d0-9bf2-5e3ed691494c`
- heartbeat: `6` before the gate and `21` after it
- capabilities: `transactions: 13`, `mesh_batch: 5`,
  `mesh_component_map: 4`, `mesh_topology: 5`, and `mesh_join: 1`

The batch performed exact Join, boundary Weld, non-manifold and degenerate
validation, commit, project save, project reload, and post-reload Mesh inspection.
It merged four accepted cross-source pairs, reduced the joined result from 16 to 12
vertices, and persisted `12` vertices, `20` edges, `10` faces, and `40` loops with:

- material `Join Shared Material`;
- UV layer `JoinUV`;
- Vertex Group/weights schema `Root`;
- Corner Color Attribute `ModuleTint`;
- `0` non-manifold and `0` degenerate components.

The left and right JOIN_BRANCH-to-Weld composed Map hashes were respectively
`4c8af8f30c80087f8fa6ab9da13bec3e7380009d7e75200611fc25db4284dfad` and
`95cfb38ae5402e0783efa09ebb71322e1cbce1cea303b1ac0278eaccb29bf48b`.
The final assembly manifest SHA-256 was
`cd41b327bf969a6ae47580d8dfad3ebbf91096c713f205ae8c8549a1d3a31242`.

The generated source fixture remained unchanged at SHA-256
`af53c137ab89ef4de2f9c536c58bea2b01481afb9ce0789b67361f392ae99fea`.
The user-supplied `test-model.blend` was read only for source-integrity evidence and
remained unchanged at SHA-256
`e9ce53fbb7bf0af8847eb2238dc080c55a48bca8459ed5ee7a588d12bcf8c059`.
That hash check is not presented as the planned character cage composition.

## Reversible-path evidence and repaired defects

An earlier candidate run recorded the complete reversible sequence at
`artifacts/live-smoke/20260901T101712Z-81c2446b/report-0.17.0.json` (SHA-256
`d2eb7cb95fd5bf37dd71713e0401d9f769f8c19ab167dcea22e1694dcc536d98`).
It proved idempotent WORLD Join and rollback, SOURCE_OBJECT Join and four-pair Weld,
two composed branch Maps, explicit rollback, disconnect rollback, stable
`MESH_JOIN_DATA_CONFLICT`, native-save adoption through
`TRANSACTION_ACCEPTED_BY_USER_SAVE`, and reload. This report predates the final
CustomData stabilization changes, so it is retained as supplementary evidence, not
described as the final-tree release run.

The multi-angle images from that sequence are distinct:

- `joined-front.png`: 18,614 bytes, SHA-256
  `bec5185fde10a7efa349a833bb7f9e994fed76bf3a3ef6d238c3a2606c781d36`;
- `joined-right.png`: 8,268 bytes, SHA-256
  `9dcbb52b5457e0573ec2f46f5485daf17f57b9098f6858e4239d2868496e806c`.

Repeated live probes exposed and repaired integration defects not represented by
schema-only tests:

- Join no longer supplies face edges twice to `Mesh.from_pydata`; it passes only
  genuine loose edges and derives exact source edge lineage from the resulting edge
  table;
- Weld no longer asks BMesh to merge deform, UV, or Color Attribute CustomData in
  place. It reconstructs those domains from exact component lineage;
- Weld of a transaction-created Join output is built on an unlinked Mesh copy and
  published once, so the scene never observes its intermediate CustomData state;
- the stable post-refresh revision is captured before ComponentMap creation;
- a redundant commit-time dependency-graph refresh is deferred for already-published
  Join transactions;
- expected Windows Proactor resets during project reload are normalized, and project
  lifecycle polling treats the brief no-session interval as transient.

## Open live limitation

The aggregate direct smoke intentionally stresses one Blender process with several
Join rollbacks, immediate recreation, Weld, viewport capture, disconnect recovery,
an injected user edit, native save, and reload. Blender 4.2.23 still intermittently
closes the listener or process at different dependency-graph boundaries in that
high-frequency sequence. The latest focused run reached Weld in the final tree; a
preceding final-code candidate reached native-save adoption and then timed out during
reload. No newer crash file was emitted, so the evidence does not justify attributing
every exit to one reproducible Blender call.

Accordingly, 0.17.0 is accepted for the deterministic commit/save/reload path and its
automated authority surface, while the aggregate same-session stress gate and the
real-character head/body-cage composition remain pending. They must not be relabeled
as passing unless rerun on the release tree with a complete report.

## Release artifacts

- `artifacts/blender-research-mcp-addon-0.17.0.zip`
  - bytes: `225,970`
  - SHA-256: `4f96817c3537ab47696ee560d797b02284ebc1ac5f66350454f03d25b27cf9a6`
- `dist/blender_research_mcp-0.17.0-py3-none-any.whl`
  - bytes: `327,449`
  - SHA-256: `ed6e51f6e1388492be26f640b4ff1c8244a5f3b1e652cd7ebe83e721275c56e6`

Both archives passed managed add-on resource verification; the wheel also contains
the fixed session bootstrap.
