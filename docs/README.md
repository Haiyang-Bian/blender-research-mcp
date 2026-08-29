# Documentation handbook

This directory is the authoritative handbook for Blender Research MCP. Read documents
in this order when starting a new implementation task:

1. [Design and handoff](design.md) — architecture, safety boundaries, implemented
   phases, and open decisions.
2. [Using Blender Research MCP](usage.md) — current 0.7.0 operator workflow and error
   recovery.
3. [0.7.0 managed lifecycle roadmap](roadmap/0.7.0-managed-lifecycle.md) — application
   launch, project switching, implementation, and pending live completion criteria.
4. [0.6.0 comparative preview roadmap](roadmap/0.6.0-comparative-previews.md) — the
   implemented comparison contract and its separately pending real Blender gate.
5. [Architecture decisions](decisions/README.md) — accepted protocol and authority
   decisions.
6. [Validation records](validation/) — evidence from real Blender 4.2.23 smoke tests.

## Current release

Version 0.7.0 adds managed visible Blender launch and explicit project save, open,
reload, and quit tools on top of the 0.6 comparison surface. Application launch and
project opening remain separate. The current implementation has automated coverage;
its real Blender lifecycle acceptance remains pending, as does the older 0.6 real
comparison gate.

The public repository is
[Haiyang-Bian/blender-research-mcp](https://github.com/Haiyang-Bian/blender-research-mcp).
PR #1 merged the validated 0.2–0.5.1 history into `main`; obsolete phase branches were
removed after their commits were verified reachable from `main`.

## Authority boundary

Documentation does not grant additional runtime authority. Unless a later accepted
decision explicitly changes the contract, the project still forbids arbitrary Python,
external network services, asset import, node-topology edits, object location/rotation,
and force-overwriting transaction conflicts. Blend-file saving is available only
through explicit lifecycle tools following user save/open/reload/quit intent.

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
