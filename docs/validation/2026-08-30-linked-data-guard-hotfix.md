# Blender 4.2.23 linked-data transaction guard hotfix

- Status: passed
- Date: 2026-08-30
- Package and add-on: `0.10.1`
- Protocol: `1`
- Blender: `4.2.23 LTS`
- Dedicated run: `20260830T051633Z-b8019c21`, port `9884`
- Full Modifier regression: `20260830T051654Z-60707eb5`, port `9883`

## Defects closed

`object.duplicate(linked_data=true)` now advances both kinds of transaction-owned
data-user evidence after Blender increments the shared data-block's `users` count:

- an existing structural guard is refreshed to the exact post-operation fingerprint
  and user count for Mesh, Camera, or Light data;
- an existing Light/Camera `ObjectDataDelta` receives the same exact post-operation
  user count.

The operation does not create a guard that was not already present. A genuine
out-of-transaction user-count change therefore remains distinguishable from an
Agent-owned duplicate. Newly linked duplicates are also explicitly deselected. This
prevents a selected source's copied selection bit from making its duplicates selected
after save and reload.

## Dedicated real-Blender evidence

The gate cold-launched PID `37928`, instance
`eb09630b-e939-4171-afcb-e21a367877e9`, launch ID
`07ada3e8-56de-4366-851a-aaef0bacf255`. Heartbeat advanced from `4` to `61`.

- After a material assignment guarded a one-user Mesh, two linked duplicates advanced
  the same Mesh identity from users `1` to `3`; rollback removed both duplicates and
  restored users `1`.
- Three linked duplicates without an earlier Mesh guard advanced users `1` to `4` and
  rolled back to `1`.
- An independent duplicate used a different Mesh identity with one user and rolled
  back without changing the source data.
- `project.save` committed an active transaction containing material assignment and
  two linked duplicates. Reload retained one shared Mesh with users `3` across all
  three objects.
- After reload, only the original `Cube` remained selected. Both linked duplicates
  were explicitly unselected.
- The private test hook then created one external linked user after the latest Agent
  write. Both commit and rollback returned `STRUCTURE_CONFLICT` and did not overwrite
  that state.
- Mode, active object, selection, workspace, scene, view layer, frame, and heartbeat
  remained stable throughout successful operations and the conflict probe.

The source fixture SHA-256 remained
`3ecb7b0b7715bcdbfe8cdab919e302fb696cffaa4572fa07d2df4fbe70e2755f`.
The dedicated report SHA-256 is
`dd1071cec8407f7d0a4b3bdcb8de68931c31d2bc2cb5021aded46f89fcca1872`.

## Restarted MCP schema evidence

After Codex restarted, the registered public MCP surface exposed
`modifier.inspect/create/set/move/delete` and `modifier_setting`. On port `9877`, a
public-tool-only round trip inspected the factory `Cube`, created `Schema Bevel`, set
width `0.2 → 0.35` and segments `3 → 5`, then rolled back. The final stack was empty and
its fingerprint exactly matched the initial fingerprint. A separate disposable public
MCP transaction reproduced the original linked-data deadlock before the fix, including
the second-duplicate, commit, and rollback `STRUCTURE_CONFLICT` results.

## Full 0.10 surface regression

The complete Modifier gate then cold-launched PID `21844`, instance
`0ea2526b-00b7-4f56-a90f-f0d7fc5fc523`, launch ID
`2ad61f94-79db-42ac-b9ed-3cfb24b5f4ec`. Heartbeat advanced from `3` to `487`.
All four typed Modifier families, settings, ordering, shared-Mesh stack independence,
Boolean cycle rejection, four comparisons, same-property/order conflicts, four
disconnect rollbacks, deferred delete, commit, save, reload, inspect, and final Eevee
render passed unchanged.

Its source fixture SHA-256 remained
`f305f28c96b50e10f3abecbee2c332b8688beeddf08824547116469e7bff4b23`.
The full report SHA-256 is
`d2b1a613316541b84b7b4b0d6e670a256c8201150699e33cb3a693148ea74fda`.

Both managed Blender processes, port listeners, and port-specific manifests were gone
after their runs. Ignored raw reports remain under
`artifacts/live-smoke/20260830T051633Z-b8019c21/` and
`artifacts/live-smoke/20260830T051654Z-60707eb5/`.

## Automated and release gates

- `uv run --no-sync pytest`: `204 passed`
- `uv run --no-sync ruff check .`: passed
- `uv run --no-sync mypy`: passed for 21 source files
- wheel and add-on ZIP managed-resource verification: passed
- add-on ZIP SHA-256:
  `8cd3b87f9e9aee4ab3a4ce039c9acaced8a92c158b8833925aa649acfdb868bf`
- wheel SHA-256:
  `b4eae9a2ba82f55711a369c7b3d615cd8af76913d6b9f352458156159d8e9ff2`

`uv lock` advanced the project metadata from 0.10.0 to 0.10.1. `uv sync` built the
new package, then could not replace `.venv\Scripts\blender-research-mcp.exe` because
the current Codex process held it (`os error 32`). Dependencies were unchanged and the
already synchronized environment completed every gate with `uv run --no-sync`.
