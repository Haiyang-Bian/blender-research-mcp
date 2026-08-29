# 0006 — Separate managed application and project lifecycle

- Status: accepted and automated-gate validated in 0.7.0
- Date: 2026-08-29

## Context

Requiring the user to preinstall the add-on, start Blender, and manually select every
project prevents the semantic MCP from operating as an independent agent tool. Starting
an application and replacing its current document are different user intents and
should not be hidden inside one convenience command.

The existing preview transaction boundary also made all saving unavailable, even after
the user directly asked to save, switch, reload, or close. That protected an action the
user had already authorized and prevented normal project lifecycle automation.

## Decision

- Expose `application.status/launch/quit` separately from
  `project.status/save/open/reload`.
- Let the Agent compose status, launch, and open according to user intent; project tools
  never launch Blender implicitly.
- Treat an explicit user request to save, switch, reload, or close as authorization for
  that operation without another confirmation.
- Launch only a configured Blender executable with a fixed, versioned, session-level
  bootstrap and packaged add-on. Do not accept user Python or arbitrary launch flags.
- Accept absolute `.blend` paths anywhere accessible to the user; do not add a project
  root allowlist.
- Default open and quit to commit and save current dirty state. Default reload to
  discard unsaved changes. Keep explicit `save_current` overrides.
- Execute file open/reload/quit on the main-thread tick after the acceptance response,
  then reconnect and verify final process/project state externally.
- Negotiate `project_lifecycle: 1` and `application_lifecycle: 1` per tool so old 0.6
  sessions remain usable for their existing tool surface.

## Consequences

The MCP can now intentionally write `.blend` files and close Blender when the user's
request calls for it. Preview `transaction.commit` remains memory-only; lifecycle tools
are the only semantic save path. Project scripts and saved UI load by default, matching
ordinary Blender open behavior, with explicit opt-out parameters.

The MCP server exiting does not close Blender. An accepted file operation can briefly
disconnect the transport; external orchestration must distinguish that expected phase
from a failure and must not report success until the absolute path or process exit is
verified.
