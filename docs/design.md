# Blender Research MCP — design and handoff

- Status: 0.5.1 bounded LookDev writes implemented and live-validated
- Primary Blender target: 4.2.23 LTS
- Package and add-on version: 0.5.1
- Protocol version: 1
- Development transport port: 9877

## 1. Why this project exists

The current Blender research workflow uses the community
ahujasid/blender-mcp. Its connected tool surface is useful for scene summaries,
object information, viewport screenshots, and asset integrations, but
existing-scene editing is effectively concentrated in one unrestricted
execute_blender_code escape hatch.

That shape creates a poor long-running LookDev loop:

1. a visual symptom is described in natural language;
2. the agent guesses a technical cause;
3. it writes a relatively large bpy script;
4. Blender renders an image;
5. the human identifies a spatial or aesthetic mismatch;
6. the cycle repeats.

The rendering scripts are reproducible, but they do not provide the active
perception and small, inspectable actions that a human Blender operator uses:
selecting an object, framing it, orbiting the view, isolating geometry, changing
one property, comparing the result, and undoing it.

## 2. Problems to solve

### 2.1 Passive visual access

A final render is only one camera projection. Geometry errors often need
non-camera views, selection outlines, wireframe, material IDs, normals, and
temporary isolation. The agent must be able to choose the next observation
based on uncertainty instead of repeatedly requesting expensive full renders.

### 2.2 Missing semantic atomic operations

Raw keyboard events are context-fragile, while unrestricted Python is too broad.
The bridge needs explicit operations such as:

- read and restore context;
- select named objects;
- frame or orbit around a target;
- query evaluated geometry;
- transform one object or selected mesh elements;
- set one modifier or material input;
- create a preview transaction and roll it back.

### 2.3 Shared mutable Blender state

The user and agent share selection, mode, visibility, viewport, undo history,
and the active blend file. Observational operations must not leave the user's UI
rearranged. Mutating operations need clear ownership, before/after evidence, and
rollback tokens.

### 2.4 Weak review ergonomics

The human should provide aesthetic feedback, not diagnose local axes, material
slots, or Boolean cutters. The bridge should make A/B/C variants, focused crops,
and annotated diagnostics cheap enough that the user can select a direction
without translating the symptom into Blender implementation language.

### 2.5 Security and privacy

The bridge runs with Blender-process authority. It must be local-first:

- loopback listener only by default;
- random per-session token;
- no telemetry;
- no external asset services;
- bounded message size and timeout;
- structured allow-listed commands;
- project-root restriction for approved script execution;
- unrestricted Python disabled by default.

## 3. Design goals

1. **Observable** — every mutation returns structured before/after state and a
   cheap visual verification path.
2. **Reversible** — preview changes can be rolled back without reopening the
   file or relying on user intervention.
3. **Semantic** — tools express Blender intent rather than keyboard shortcuts.
4. **Context-aware** — mode, active object, selection, area, shading, overlays,
   visibility, and view transform are explicit state.
5. **Deterministic** — identical requests against the same scene generation
   produce the same result or a clear precondition error.
6. **Local-first** — no telemetry or third-party network dependency.
7. **Small** — implement only the capabilities needed for research; do not
   reproduce asset marketplaces or generative 3D integrations.
8. **Versioned** — protocol, schemas, capability negotiation, and migrations are
   explicit.

## 4. Non-goals

- Reimplementing Blender's complete Python API as MCP tools.
- Simulating every keyboard shortcut or mouse gesture.
- Replacing reusable project-side rendering scripts.
- Shipping PolyHaven, Sketchfab, Hyper3D, Hunyuan3D, or cloud services.
- Achieving autonomous artistic judgment equivalent to a senior character
  artist.
- Editing or migrating the existing portrait scene during bridge development.

## 5. Current architecture

~~~text
Codex / another MCP client
        |
        | MCP over stdio
        v
blender_research_mcp server (normal Python process)
        |
        | authenticated framed JSON over 127.0.0.1
        v
Blender add-on socket thread
        |
        | bounded command queue
        v
Blender main-thread timer -> semantic bpy handlers
~~~

### 5.1 External MCP server

Responsibilities:

- MCP tool registration and JSON schemas;
- validation before contacting Blender;
- request IDs, deadlines, retry classification, and structured errors;
- connection handshake and capability negotiation;
- decoding screenshots and other binary artifacts;
- no direct Blender data mutation.

### 5.2 Blender add-on

Responsibilities:

- local authenticated listener;
- reliable message framing;
- socket I/O outside Blender's main thread;
- one persistent main-thread queue drain;
- semantic command registry;
- context snapshots and restoration;
- viewport capture and ray casting;
- transaction bookkeeping;
- concise UI for server status, authority, and current operation.

### 5.3 Project scripts

Complex reproducible pipelines remain repository-owned scripts. A future
run_project_script tool may execute an existing file only when:

- its resolved path stays inside an explicitly authorized project root;
- the file already exists on disk;
- arguments are structured data;
- the operation is recorded in the response;
- arbitrary inline Python remains disabled.

## 6. Protocol principles

The first protocol should use a small versioned request envelope:

~~~json
{
  "protocol": 1,
  "request_id": "uuid",
  "session_token": "secret",
  "command": "context.get",
  "params": {},
  "deadline_ms": 5000
}
~~~

Responses must distinguish transport, precondition, validation, Blender API,
timeout, conflict, and internal errors. Each successful mutation includes a
scene-generation number so stale clients cannot unknowingly act on changed
state.

Messages require explicit framing, preferably a four-byte length prefix. A
single socket recv call must never be treated as one complete JSON message.

## 7. Tool surface

### Implemented observation

- connection.ping
- context.get
- context.snapshot
- context.restore
- object.inspect
- object.geometry.inspect
- object.lookdev.inspect
- material.inspect
- viewport.capture
- viewport.raycast
- observation.bundle

`viewport.capture` uses GPU off-screen rendering and does not depend on foreground
window pixels. `observation.bundle` composes one to three sequential captures and
requires stable scene, object, and user-context evidence. Captures can use bounded
diagnostic shading and absolute orbit parameters, and their session-local IDs ground
normalized image coordinates for evaluated-scene raycasts.

### Implemented bounded mutation

- object.transform
- object.visibility.set
- modifier.set_state
- shape_key.set_value
- material.set_input

The 0.5 implementation accepts only absolute allow-listed fields. Object writes are
limited to local `scale.x/y/z`, visibility flags, modifier viewport/render state, and
non-Basis undriven shape-key values. Material writes are limited to unlinked,
undriven Float, Int, Boolean, Vector, and Color input `default_value` properties.
Node topology, modifier structure, location, rotation, lights, asset import, arbitrary
Python, and file saving remain unavailable.

Every new writer requires an active transaction, a current scene generation, a unique
idempotency key, and exact session identities returned by inspection. Shared materials
are rejected unless the caller confirms the exact current material user count and sets
`allow_shared=true`; the bridge does not make implicit single-user copies.

### Implemented transactions

- transaction.begin
- transaction.commit
- transaction.rollback

Tool count is not a success metric. A small composable surface with precise
preconditions is preferable to dozens of overlapping convenience tools.

## 8. Context and transaction model

A context snapshot should record at least:

- active scene, view layer, workspace, window, area, and region identity;
- Blender mode;
- active object and selected objects;
- object, collection, and overlay visibility affected by the operation;
- viewport perspective, view matrix, distance, lens, shading, and overlays;
- current frame and active camera;
- scene-generation counter.

Read-only inspection may temporarily change selection or view only when it
restores the snapshot in a finally path.

Mutation transactions use typed property deltas for scale, visibility, modifier state,
shape-key value, and material input. Repeated writes guard the last agent value while
reverse rollback restores the original value. Rollback only overwrites a property when
its current value still matches the agent's last write; identity, context, or property
conflicts preserve user state. Blender Undo is not the transaction contract because
user actions and agent actions can interleave.

## 9. Development phases

### Phase 0 — transport spike

Status: completed and live-validated on 2026-08-28.

- Install a separate add-on using port 9877.
- Implement handshake, ping, request IDs, timeouts, reconnect, and shutdown.
- Keep the existing Blender MCP on port 9876 for fallback.
- Prove that restarting the add-on and reopening a blend file does not hang the
  client or leave stale references.

### Phase 1 — active observation

Status: completed and live-validated on Blender 4.2.23 in 0.4.0.

- Implement context read/snapshot/restore.
- Implement temporary selection, frame, absolute orbit, capture-bound raycast, and
  evaluated geometry summaries.
- Validate on the portrait scene without saving it.

Standalone selection, frame, and incremental orbit tools are intentionally not
exposed. Their observation use cases run inside one capture and restore the original
context, avoiding persistent viewport debt.

### Phase 2 — transactions

Status: typed reversible property transactions implemented in 0.5.1 and
live-validated on Blender 4.2.23. The original absolute object-scale path was
validated on 2026-08-28; the expanded visibility, modifier, shape-key, and material
delta types were validated on 2026-08-29.

- Implement property deltas and rollback tokens.
- Change one eye-aperture parameter, capture evidence, and roll it back.
- Verify the user's selection, mode, visibility, and viewport are unchanged.

### Phase 3 — bounded LookDev operations

Status: object visibility, modifier state, shape-key value, and material input preview
tools implemented and live-validated in 0.5.1.

- Inspect writable targets before mutation and require exact session identities.
- Keep each write absolute, typed, transaction-scoped, and reversible.
- Bound inspection output and reject unsupported, linked, driven, or stale targets.
- Keep light controls, modifier parameters, node topology, render-region controls, and
  automatic A/B/C comparison outside the 0.5.1 authority boundary.

### Phase 4 — adoption

- Run both bridges against a fixed acceptance suite.
- Switch Codex to the new MCP only after reconnect, rollback, and UI-responsivity
  tests pass.
- Keep the old bridge available until at least one accepted research milestone
  completes through the new bridge.

## 10. Acceptance criteria for the first milestone

Completed on Blender 4.2.23 LTS; see the validation record under `docs/validation`.

- Blender 4.2.23 remains responsive while commands execute.
- The client reconnects after add-on restart.
- Chinese object names round-trip without corruption.
- Selection and mode can be read and restored.
- A target can be framed and captured from at least three views.
- A bounded property mutation reports before/after values.
- Rollback restores both data and user context.
- No request leaves the local machine.
- Repeating a request with the same idempotency key does not duplicate mutation.
- Automated tests cover framing, schema validation, timeouts, reconnect, and
  transaction state transitions.

## 11. Repository boundaries

~~~text
blender_addon/            installable Blender-side package
docs/                     design and decisions
src/blender_research_mcp/ external MCP server
skills/                   versioned Codex workflow skill source
tests/                    fast server/protocol tests
tests_blender/            future live Blender smoke scripts
artifacts/                ignored local screenshots and diagnostics
~~~

Do not place character models, textures, renders, or blend files here. The
existing rendering project remains the integration fixture and source of
research scenarios.

## 12. Open decisions

- Whether a future capture backend should guarantee operation while Blender is
  minimized; 0.3 guarantees only an unfocused or obscured running window.
- Whether any future persistent viewport-control operation justifies a separate
  context lease; 0.4 keeps navigation inside restored capture operations.
- Whether the add-on should later ship as a Blender Extension in addition to the
  current traditional ZIP.
- Whether a bounded project-script capability is necessary; arbitrary inline Python
  remains out of scope.
- Blender 5.x capability policy and the project license; decide both before publishing.

## 13. Guidance for a new Codex task

At the start of a new task:

1. Read AGENTS.md and this document completely.
2. Inspect Git status and do not overwrite uncommitted IDE or user changes.
3. Use uv for all Python dependency and execution work.
4. Keep Blender 4.2.23 and Python 3.11 add-on compatibility.
5. Develop on port 9877 and require explicit capability negotiation.
6. Do not modify the portrait blend file while building transport infrastructure.
7. Prefer one vertical slice—connect, observe, mutate, rollback, verify—over a broad
   catalogue of unfinished tools. Use `observation.bundle` before adding new mutation
   authority.
