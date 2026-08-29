# Documentation handbook

This directory is the authoritative handbook for Blender Research MCP. Read documents
in this order when starting a new implementation task:

1. [Design and handoff](design.md) — architecture, safety boundaries, implemented
   phases, and open decisions.
2. [Using Blender Research MCP](usage.md) — current 0.6.0 operator workflow and error
   recovery.
3. [0.6.0 comparative preview roadmap](roadmap/0.6.0-comparative-previews.md) — the
   implemented contract and pending real Blender completion criteria.
4. [Architecture decisions](decisions/README.md) — accepted protocol and authority
   decisions.
5. [Validation records](validation/) — evidence from real Blender 4.2.23 smoke tests.

## Current release

Version 0.6.0 adds rollback-safe comparative previews on the live-validated 0.5.1
Blender authority. It provides authenticated local transport, context-safe GPU
off-screen observation, capture-bound raycasts, evaluated mesh summaries, and
reversible writes for scale, visibility, modifier enable state, shape-key values, and
guarded material inputs. The comparison implementation has automated coverage; its
real Blender acceptance remains pending.

The public repository is
[Haiyang-Bian/blender-research-mcp](https://github.com/Haiyang-Bian/blender-research-mcp).
PR #1 merged the validated 0.2–0.5.1 history into `main`; obsolete phase branches were
removed after their commits were verified reachable from `main`.

## Authority boundary

Documentation does not grant additional runtime authority. Unless a later accepted
decision explicitly changes the contract, the project still forbids arbitrary Python,
blend-file saving, external network services, asset import, node-topology edits,
object location/rotation, and force-overwriting user changes.

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
