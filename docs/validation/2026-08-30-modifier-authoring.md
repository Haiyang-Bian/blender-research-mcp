# Blender 4.2.23 bounded Modifier-authoring smoke

- Status: passed
- Date: 2026-08-30
- Branch: `codex/modifier-authoring`
- Run ID: `20260829T180549Z-88f8d6fc`
- Blender: `4.2.23 LTS`
- Add-on and external MCP server: `0.10.0`
- Protocol: `1`
- Port: `9883`
- Elapsed time: `54550.089 ms`

The gate cold-launched the versioned managed add-on on an isolated port, opened a copy
of a newly generated deterministic fixture, and used only direct `BridgeClient`
semantic commands for Modifier work. The process was PID `10304`, instance
`b848aaf3-3eec-4518-8706-f5757b887118`, with launch ID
`f4198286-7039-484e-8a44-a9026e1d8a60`. It advertised `transactions: 3` and
`modifier_authoring: 1`. Heartbeat advanced from `3` to `487`; after the run, the PID,
port listener, and port-specific session manifest were absent.

## Four typed families and complete restoration

The fixture used separate deterministic Mesh objects for Bevel, Subdivision,
Solidify, and Boolean. Each Modifier was created inside its own transaction, evaluated
geometry was inspected, and rollback restored the exact initial stack fingerprint:

| Type | Evaluated geometry before | During create | Restored |
|---|---:|---:|---:|
| Bevel | 8 vertices / 12 edges / 6 polygons | 152 / 300 / 150 | yes |
| Subdivision | 8 / 12 / 6 | 98 / 192 / 96 | yes |
| Solidify | 100 / 180 / 81 | 200 / 396 / 198 | yes |
| Boolean | 8 / 12 / 6 | 72 / 112 / 40 | yes |

A baseline stack was then committed for all four targets plus an order fixture that
contained supported Bevel/Subdivision items separated by an unsupported Mirror. Typed
multi-field settings for all four families rolled back to their exact fingerprints.
Moving Subdivision across Mirror preserved the same Modifier identity and rolled back
to its original index.

Two objects shared one Mesh data-block (`mesh:269cb3a2fe0`, users `2`). Creating a
Modifier on only the first object left the second object's stack empty, and rollback
removed the first stack entry. A direct self-operand returned `BOOLEAN_OPERAND_SELF`;
an A-to-B then B-to-A chain returned `BOOLEAN_CYCLE` and rolled back.

## Comparative image evidence

`lookdev.compare` exercised one field from every family. Content order was always
baseline/A/B, every candidate rolled back, and `context_unchanged`, `object_unchanged`,
and `target_restored` were all true. The candidate maximum-channel differences were:

| Target | Candidates | Max channel differences |
|---|---|---:|
| `Bevel Main.width` | `0.0`, `0.8` | `103`, `105` |
| `Subdivision Main.levels` | `0`, `2` | `181`, `186` |
| `Solidify Main.thickness` | `-0.45`, `0.75` | `101`, `101` |
| `Boolean Main.operation` | `UNION`, `INTERSECT` | `101`, `94` |

Bevel, Solidify, and Boolean used oblique SOLID evidence; Subdivision used an oblique
WIREFRAME capture so evaluated level changes were directly visible. No comparison
ranked, committed, or saved a candidate.

## Conflict, disconnect, and persistence evidence

The private live-test hook changed `Bevel Main.width` to `0.77` after a comparison
writer. Rollback returned `MODIFIER_STACK_CONFLICT`, preserved `0.77`, and stopped the
comparison. A separate hook reordered the stack to `Subdivision Order`, `Legacy
Mirror`, `Bevel Order`; rollback again returned `MODIFIER_STACK_CONFLICT` and preserved
that user order. Both cases recovered by reloading the last saved temporary project.

Four independent connection drops occurred after Modifier create, set, move, and
pending delete. After each two-second grace period, the same Blender instance accepted
a new authenticated connection and the complete pre-operation stack fingerprint was
restored.

Finally, `Delete Probe` was disabled and reported `pending_delete=true`, commit removed
it, and `project.save` plus `project.reload` proved it absent on disk. A 480×320 Eevee
preview after reload was nonblank and had SHA-256
`a0627d78022aacd74e929fd10d4fbf3d96244c65e26c5c44596189093b5ff4e5`.
Selection, active object, mode, workspace, and scene identity matched before and after
the semantic operations.

## Files and hashes

All `.blend` files and PNG evidence stayed under `%TEMP%` or ignored `artifacts/`.
The source fixture remained unchanged:

- source before/after SHA-256:
  `dd4f95990bb0556d4de089f35481f20f5fcbda074074b984ea66cd55764bd439`;
- saved working project SHA-256:
  `602f294b46ebb5e19f1a16261b3eebee0f99516c058e7a7ea70f2741fb7eccff`;
- report SHA-256:
  `e2a2b10736d089a2051601fe74da3d814432c158edce37f01d8fb70720cb3f46`.

The ignored report and thirteen PNGs are under
`artifacts/live-smoke/20260829T180549Z-88f8d6fc/`. Before the live run, `uv lock`
synchronized the release metadata. `uv sync` built the project successfully but could
not replace `.venv\Scripts\blender-research-mcp.exe` because the current Codex process
held it (`os error 32`); the unchanged dependency environment then ran all gates via
`uv run --no-sync` as specified by the release plan.

During early live iterations, Blender exposed two fake-client gaps that were fixed and
regression-tested: Modifier RNA does not support IDProperties, so pending-delete state
is held in a file-scoped session registry; and two Python RNA wrappers for the same
Modifier cannot be compared with `is`, so all exact checks use session identity.
