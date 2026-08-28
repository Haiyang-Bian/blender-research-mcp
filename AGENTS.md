# AGENTS.md

## Start here

Read docs/design.md before changing architecture, protocol, tool schemas, safety
boundaries, or Blender-version support. Treat repository documentation and tests
as authoritative; cross-task memory is only supplemental context.

## Scope

This repository builds a local-first semantic MCP bridge for Blender research.
It is separate from the character-rendering repository and must not copy scene
assets, generated renders, or blend files into this repository.

## Development rules

- Prefer incremental, reviewable changes over broad refactors.
- Keep the Blender add-on thin: transport, main-thread dispatch, context capture,
  viewport operations, and registered semantic commands.
- Keep general MCP schemas and transport outside Blender under
  src/blender_research_mcp/.
- Keep project-specific portrait automation in its original rendering project.
- Do not expose unrestricted Python execution by default. If an escape hatch is
  added, require an explicit opt-in and document its authority.
- Preserve user selection, mode, viewport, visibility, and undo state around
  observational or preview operations.
- Listen on loopback by default, require a session token, disable telemetry, and
  do not add third-party network integrations without explicit approval.

## Python and uv

- Use uv as the authoritative environment and dependency interface.
- After dependency or interpreter changes, run uv sync.
- For ordinary execution use uv run --no-sync COMMAND.
- Do not activate .venv, invoke its executables directly, or use pip.
- Keep source syntax compatible with Python 3.11 because Blender 4.2 embeds that
  Python generation, even when the external server is developed on Python 3.13.

## Validation

Before committing implementation changes, run:

~~~powershell
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync mypy
~~~

Blender-integrated changes additionally require a live smoke test that records:

- Blender and add-on versions;
- connection and reconnect behavior;
- active mode, object, selection, and viewport before and after;
- mutation result and transaction rollback;
- proof that the Blender UI remained responsive.

Commit at meaningful checkpoints and keep commits focused.
