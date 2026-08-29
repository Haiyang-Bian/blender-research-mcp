# Documentation handbook

This directory is the authoritative handbook for Blender Research MCP. Read documents
in this order when starting a new implementation task:

1. [Design and handoff](design.md) — architecture, safety boundaries, implemented
   phases, and open decisions.
2. [Using Blender Research MCP](usage.md) — current 0.8.0 operator workflow and error
   recovery.
3. [0.8.0 semantic scene authoring roadmap](roadmap/0.8.0-semantic-scene-authoring.md) —
   structural transactions, objects, materials, local images, World/Camera, and renders.
4. [0.7.0 managed lifecycle roadmap](roadmap/0.7.0-managed-lifecycle.md) — application
   launch, project switching, implementation, and completed live evidence.
5. [0.6.0 comparative preview roadmap](roadmap/0.6.0-comparative-previews.md) — the
   implemented and live-validated comparison contract.
6. [Architecture decisions](decisions/README.md) — accepted protocol and authority
   decisions.
7. [Validation records](validation/) — evidence from real Blender 4.2.23 smoke tests.

## Current release

Version 0.8.0 adds bounded semantic static-scene authoring and reviewed Eevee output on
top of the live-validated 0.7 application/project lifecycle. Structural transaction v3
supports exact create, unlink/delete, material-slot, node-link, World, and Camera
rollback. The automated suite and real Blender moonlit-water gate have both passed; see
[the 0.8 validation record](validation/2026-08-29-semantic-scene-authoring.md). The
independent 0.6 real comparison gate has also passed; see
[its validation record](validation/2026-08-30-comparative-previews.md).

The public repository is
[Haiyang-Bian/blender-research-mcp](https://github.com/Haiyang-Bian/blender-research-mcp).
PR #1 merged the validated 0.2–0.5.1 history into `main`; obsolete phase branches were
removed after their commits were verified reachable from `main`.

## Authority boundary

Documentation does not grant additional runtime authority. Unless a later accepted
decision explicitly changes the contract, the project still forbids arbitrary Python,
external network services, arbitrary node graphs, mesh-component editing, animation,
Cycles, and force-overwriting transaction conflicts. Local absolute-path image loading,
bounded object location/rotation, fixed semantic nodes, and explicit render export are
available only through their 0.8 tools. Blend-file saving remains an explicit lifecycle
operation following user save/open/reload/quit or delivery intent.

Roadmap documents distinguish implemented behavior from pending live acceptance.
User-facing instructions must not describe an automated gate as real Blender evidence.

## Development and validation

Use uv for all project commands. The required automated gate is:

~~~powershell
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync mypy
~~~

Blender-integrated changes additionally require an isolated temporary blend copy, live
version/capability evidence, context restoration checks, mutation rollback, UI
heartbeat evidence, and before/after source-file hashes. Images and blend files remain
under ignored `artifacts/` or `%TEMP%`, never in this repository.
