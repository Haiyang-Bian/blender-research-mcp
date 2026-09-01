# Blender Research MCP — design and handoff

- Status: 0.17.0 cross-object Mesh composition implemented; live release gate pending
- Current milestone: 0.17.0 transaction-v13 Mesh join, explicit weld, and batch v5
- Next milestone: 0.18.0 bounded Shape-Key structure authoring
- Primary Blender target: 4.2.23 LTS
- Package and add-on version: 0.17.0
- Protocol version: 1
- Development transport port: 9877

## 1. Why this project exists

The workflow originally used the community ahujasid/blender-mcp. Its connected tool
surface was useful for scene summaries, object information, viewport screenshots, and
asset integrations, but existing-scene editing was effectively concentrated in one
unrestricted execute_blender_code escape hatch. Blender Research MCP 0.17.0 now covers
the validated observation/lifecycle/static-authoring path, unified typed object,
Light, and Camera settings, four bounded non-destructive Modifier families, and exact
base-Mesh component editing with transaction snapshots; the older bridge is no longer
the primary interface for this repository.

The 0.16 direction adds exact local Library inspection and guarded append as the final
general asset-ingress primitive needed by template-based coverage. A template supplies
geometry which is absent from the evaluated source; it is not evidence that hidden
original anatomy was reconstructed. Fitting remains an explicit composition of
SelectionSets, SurfaceRefs, bounded deformation, attribute transfer, rig binding, and
validation rather than a role-specific character command.

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
4. **Context-aware** — Scene, View Layer, mode, frame, active Camera, and data evidence
   are hard guards; navigation, display, selection, and active object are explicit but
   user-collaborative UI state.
5. **Deterministic** — identical requests against the same scene generation
   produce the same result or a clear precondition error.
6. **Local-first** — no telemetry or third-party network dependency.
7. **Small** — implement only the capabilities needed for research; do not
   reproduce asset marketplaces or generative 3D integrations.
8. **Versioned** — protocol, schemas, capability negotiation, and migrations are
   explicit.
9. **Intent-directed lifecycle** — starting Blender and opening a `.blend` remain
   separate operations, while an explicit user request to save, switch, reload, or
   quit directly authorizes that complete lifecycle action.

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
        | managed launch: fixed bootstrap + versioned add-on resources
        v
visible Blender application (when application.launch is requested)
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
- configured Blender process launch, manifest association, reconnect, and lifecycle
  completion verification;
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
- next-tick project open/reload/quit scheduling through semantic WM operations.

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
- mesh.inspect
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

- object.set
- object.transform
- object.visibility.set
- modifier.set_state
- modifier.create
- modifier.set
- modifier.move
- modifier.delete
- mesh.edit
- shape_key.set_value
- material.set_input

The original 0.5 surface accepts absolute allow-listed visibility, Modifier, Shape Key,
and material input fields. Version 0.9 adds `object.set`, a closed union for complete
local TRS, visibility, typed Light data, and typed Camera data. It is one public
object-level operation with internal typed dispatch, not a generic RNA writer.
Structural object changes, active-Camera selection, materials, World, images, Modifier
parameters, rendering, and project saving remain separate tools.

Version 0.10 adds a separate typed Modifier-stack domain for Mesh objects. Bevel,
Subdivision, Solidify, and Boolean can be created, configured, reordered, and marked
for commit-time deletion. Every mutation carries exact object/Modifier identities,
index, type, and full stack fingerprint. Apply, arbitrary Modifier types/RNA, and direct
mesh topology remain outside the Modifier surface.

Version 0.11 adds a separate exact base-Mesh domain. `mesh.inspect` pages vertices,
edges, or faces and binds their ephemeral indices to topology/full SHA-256
fingerprints. `mesh.edit` routes one closed transform/extrude/inset/bevel/delete/
dissolve/merge/face-setting/normals request to typed internal BMesh handlers. It is not
an arbitrary BMesh or RNA writer.

Version 0.10.1 repairs transaction-owned data-user evidence for linked object
duplicates. A successful linked duplicate refreshes any already guarded Mesh,
Camera, or Light data-block plus an existing typed Light/Camera data delta. The
operation still rejects a later external users change. Newly linked duplicates are
explicitly unselected so selection remains stable after save/reload.

Every new writer requires an active transaction, a current scene generation, a unique
idempotency key, and exact session identities returned by inspection. Shared materials
are rejected unless the caller confirms the exact current material user count and sets
`allow_shared=true`; the bridge does not make implicit single-user copies.

### Implemented transactions

- transaction.begin
- transaction.commit
- transaction.rollback

### Implemented comparison composition

Version 0.6.0 adds one external MCP orchestration tool, `lookdev.compare`, on top
of the existing inspected writers, capture backend, and transaction guards. It will
compare the current baseline with one to three absolute candidates for exactly one
allow-listed property. Each candidate gets its own begin, write, capture, rollback,
and restoration verification cycle.

Comparison is transient mutation, not a read-only operation. It will never commit,
save a blend file, rank candidates, or widen the Blender command surface. A selected
candidate must be applied later through the existing explicit transaction workflow.
In 0.9 the closed target union also accepts one typed `object_setting` locator and uses
`object.set`. Version 0.10 adds `modifier_setting`, routed through `modifier.set`, for a
single comparable field. Older comparison targets remain compatible.
The complete contract and acceptance gate are recorded in
`docs/roadmap/0.6.0-comparative-previews.md`.

### Implemented application and project lifecycle

Version 0.7.0 adds an external managed launcher and a thin Blender-side project command
surface:

- `application.status`, `application.launch`, and `application.quit`;
- `project.status`, `project.save`, `project.open`, and `project.reload`.

`application.launch` never accepts a project path. It resolves Blender from CLI,
environment, then `PATH`; starts it with a fixed packaged bootstrap; and associates the
session through a launch ID in the authenticated manifest. The bootstrap enables the
version-matched add-on for that Blender session without saving user preferences.

Project tools require an existing 0.7-capable session and never launch Blender
implicitly. Absolute `.blend` paths may be anywhere the user can access. Opening and
quitting save the current dirty project by default; reloading discards unsaved changes
by default. Explicit user intent is the authority gate—there is no second confirmation
or project-root allowlist. The fixed bootstrap is not an arbitrary Python MCP surface.
See `docs/roadmap/0.7.0-managed-lifecycle.md` for the detailed contract.

### Implemented semantic scene authoring

Version 0.8.0 adds exact scene/resource discovery and a structural transaction v3
surface:

- create, duplicate, transform, and delete bounded objects;
- create canonical Principled materials and assign exact material slots;
- load absolute local images and bind/clear fixed semantic PBR channels;
- create or modify the current World and assign the active Camera;
- render reviewed Eevee Next PNG evidence and explicit PNG/EXR outputs.

Structural writes require an active transaction, current generation, per-operation
idempotency UUID, exact identities, and the domain capability. A direct static-scene
creation/modification request authorizes a coherent multi-step transaction and commit
after successful preview. Any property, hard-context, identity, structure, link, users,
or preview failure stops the batch and rolls it back. User navigation, display, and
selection are not hard context. Commit remains memory-only; project
save and render export are separate explicit deliverable operations. See
`docs/roadmap/0.8.0-semantic-scene-authoring.md`.

### Implemented unified object settings

Version 0.9.0 adds exact Light/Camera data evidence to `object.inspect` and one typed
`object.set` writer. A request validates all patches and reserves transaction capacity
before changing Blender, applies transform then visibility then object data, and
advances generation once. No-op requests create no delta. Shared Light/Camera data
requires exact identity/users and explicit shared scope.

The transaction model guards numeric, Boolean, controlled enum, and linear RGB values.
Apply failures restore and verify this call's partial writes before returning. Legacy
`object.transform` and `object.visibility.set` retain their schemas while using the same
kernel. See `docs/roadmap/0.9.0-unified-object-settings.md` and decision 0008.

### Implemented bounded Modifier authoring

Version 0.10.0 adds `modifier.inspect` and typed create/set/move/delete operations for
Bevel, Subdivision, Solidify, and Boolean on Mesh objects. Inspection returns the full
ordered stack and a SHA-256 fingerprint. Transaction-level stack guards advance after
each Agent mutation and protect identity, order, public state, typed settings, Boolean
operand, and pending-delete state during commit, rollback, and disconnect recovery.

Delete is deferred: the Modifier is disabled and marked in the transaction, rollback
restores the same identity, and commit removes it only after all guards pass. Boolean
operands are exact Mesh identities with direct/transitive cycle rejection; Subdivision
and Boolean enforce bounded geometry budgets. Legacy `modifier.set_state` retains its
schema and works for unsupported Modifier types. See
`docs/roadmap/0.10.0-modifier-authoring.md` and decision 0009.

### Implemented semantic base-Mesh editing

Version 0.11.0 upgrades transactions to capability version 4 and adds one complete
Mesh snapshot guard per edited working data-block. The guard preserves Mesh identity,
object users, topology, coordinates, material/smooth state, UV/color data, and
supported attributes. Agent writes refresh the expected fingerprint; commit discards
the baseline, while rollback and disconnect restore it only when the full guard still
matches.

`OBJECT` scope transactionally single-users shared data for only the target object;
`SHARED_DATA` edits the exact data-block for its complete inspected user set. Each call
also keeps a temporary immediate snapshot so a partial Blender write can be locally
restored and verified. Library links, Edit Mode, Shape Keys, pending deletion,
unsupported attributes, and fixed geometry budgets are rejected before mutation. See
`docs/roadmap/0.11.0-semantic-mesh-editing.md` and decision 0010. UV values remain
read-only until the later 0.14 contract.

Version 0.11.1 upgrades transaction capability to 5. It separates hard transaction
context, user-collaborative UI context, and capture evidence. User orbit/pan/zoom,
projection/lens, Shading, Overlay, selection, and active-object changes neither reject a
data write nor block commit/rollback. Rollback restores transaction data only and
reports the preserved UI paths. Scene, View Layer, mode, frame, active Camera, identity,
users, properties, and structural fingerprints remain hard conflicts.

Persistent Blender save handlers form an intent barrier. A native Ctrl+S, Save As, or
Save Copy adopts the current transaction before serialization, finalizes only
Agent-owned deferred work that still matches its guard, and disables later rollback.
The terminal transaction record lets already queued requests return
`TRANSACTION_ACCEPTED_BY_USER_SAVE`; comparison maps the same event to
`COMPARISON_ACCEPTED_BY_USER_SAVE` and stops without cleanup rollback. Managed MCP
project saves are marked internally and retain their existing commit-before-save flow.

Version 0.12 implements revision-bound selection and evaluated-surface fitting rather
than UV-first authoring. Session-local SelectionSets bind semantic regions to exact Mesh
revisions without modifying Blender UI selection. Read-only SurfaceRefs bind BASE or
evaluated geometry, including Shape-Key/Armature/Modifier results, to exact
scene/frame/object evidence. Topology-preserving project, shrinkwrap, smooth, relax,
inflate, flatten, and per-vertex position operations reuse transaction Mesh snapshots
and return rebound selections. Transaction capability 6 makes resource validity follow
the actual before/after Mesh fingerprint across writes and rollback. UV authority moves
to 0.14 after topology maps. The recorded 0.12 Blender gate is complete; the real open
target correctly returned unreliable signed penetration rather than inventing a depth.
See `docs/roadmap/0.12.0-selection-surface-fitting.md`, decision 0012, and
`docs/validation/2026-08-31-selection-surface-fitting.md`.

Version 0.13 adds exact one-revision ComponentMap resources and SelectionSet remapping
for topology changes. Typed handlers record surviving, split, merged, derived, created,
and deleted components without persistent Mesh attributes or coordinate guessing. The
closed Mesh union gains subdivide, edge-ring loop cut, plane bisect, in-Mesh split,
bridge, fill, and grid fill. Transaction capability 7 binds map validity to commit,
rollback, disconnect, native save, and file-load lifecycle. See
`docs/roadmap/0.13.0-topology-component-maps.md` and decision 0013.

Version 0.13.1 composes strictly continuous ComponentMaps and adds two bounded
authorities on top of that evidence. `mesh.separate` splits one connected proper-subset
FACE SelectionSet into a new guarded object while returning independent source and
separated branch maps. `mesh.batch.execute` is a closed Mesh-only sequence of selection,
edit, separation, and validation steps with named resources, automatic remapping, one
global generation update, and whole-transaction rollback after any runtime failure.
It is not a general scene script or arbitrary BMesh surface. See
`docs/roadmap/0.13.1-mesh-separation-batches.md` and decision 0014.

Version 0.14 adds separate typed ownership domains for Mesh-corner UV data and
object-schema/Mesh-deform weights. UV unwrap and packing execute only on temporary
objects under a private operator context; the verified UV result is copied back without
using the user's mode, selection, Workspace, or viewport. Weight writes use exact Group
schema plus sparse deform-value guards, including explicit shared-data rules.

`mesh.attribute.transfer` supports exact topology lineage, nearest vertex, and
barycentric nearest surface mappings. Topology and separation expose explicit
`PRESERVE_INTERPOLATE`, `ERROR_IF_PRESENT`, or `DISCARD` policy, and batch v2 composes
UV, weight, transfer, and validation steps with automatic same-topology SelectionSet
rebind. Transaction capability 9 makes both attribute domains participate in commit,
rollback, disconnect recovery, and native-save adoption. See
`docs/roadmap/0.14.0-uv-and-skin-weights.md` and decision 0015.

The implemented 0.15 authority closes a different gap: create a new editable Mesh from
BASE, current Shape-Key-only, or final evaluated geometry; extract a disconnected FACE
SelectionSet as one object; and bind an exact weighted Mesh to an Armature. Materialize
creates a new resource and never applies or removes a source Modifier or Shape Key.
The Shape-Key-only mode excludes Modifiers so a result can be rigged without silently
baking and then repeating Armature deformation. Final evaluated output records that
all current deformation is baked and is not assumed to be a reusable rest mesh.

Character-specific completion remains a workflow over generic tools. Missing surface
under hair or clothing is underdetermined and requires an explicit template or cage;
the bridge does not claim to reconstruct undisclosed source geometry. Component
catalogs, Collection organization, and cross-object batches are the implemented 0.15.1
authority. Catalogs are session-local revision evidence, and batch manifests are
returned to callers rather than stored as project custom properties. Bounded
controlled Library append and template workflows are implemented in 0.16. Shape-Key
structure writes and Modifier Apply remain separate later authorities. See
`docs/requirements/modular-character-surface.md`. The next authority is not another
role-specific workflow: 0.17 composes exact Mesh-object inputs into one independent
base Mesh, returns one lineage branch per source, and welds only explicitly selected
boundaries. See `docs/requirements/model-editing-completeness.md`.

Tool count is not a success metric. A small composable surface with precise
preconditions is preferable to dozens of overlapping convenience tools.

## 8. Context and transaction model

A complete UI/capture snapshot should record at least:

- active scene, view layer, workspace, window, area, and region identity;
- Blender mode;
- active object and selected objects;
- object, collection, and overlay visibility affected by the operation;
- viewport perspective, view matrix, distance, lens, shading, and overlays;
- current frame and active camera;
- scene-generation counter.

Read-only inspection may temporarily change selection or view only when it restores
the call-local snapshot in a finally path. A transaction hard-context projection keeps
only Scene, View Layer, mode, frame, and active Camera. Workspace/viewport identity,
view transform, lens/projection, Shading, Overlay, selection, and active object are a
separate user-UI projection and may change while the transaction remains valid.

Mutation transactions combine typed property and structural deltas, with at most 256
deltas. Repeated property writes guard the last Agent value. Structural writes guard
identity, users, and fingerprints for supported objects/data, slots, nodes, links,
images, World, and active Camera. Reverse rollback restores original state; object
deletion is finalized only after all commit guards pass. Rollback only overwrites state
that still matches the last Agent write. Blender Undo is not the transaction contract
because user and Agent actions can interleave.

Transaction capability v4 adds `MeshEditDelta` and `MeshSnapshotGuard`. The first edit
of one working Mesh owns one baseline `Mesh.copy()`; subsequent edits reuse it and
advance the expected fingerprint. Component indices are never treated as persistent
identities and must be reacquired after topology changes.

Transaction capability v5 adds collaborative context projection and native-save
terminal adoption. Rollback no longer restores a transaction-opening UI snapshot.
Blender native save handlers run on the same main-thread sequence as queued semantic
commands, so the operation that actually executes first determines whether the write
precedes the user-save barrier or is rejected by its terminal record.

Transaction capability v9 extends Mesh snapshot evidence with UV roles, coordinates,
pins, seams, object Group schemas, and deform values. Shape-Key Meshes remain topology
immutable but may receive topology-stable UV or weight changes. Attribute writes never
make Blender or UV Editor selection part of the hard transaction state.

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

### Phase 4 — reversible comparative evidence

Status: implemented and live-validated on Blender 4.2.23; see
`docs/validation/2026-08-30-comparative-previews.md`.

- Add a typed, closed-world `lookdev.compare` orchestration tool.
- Accept one inspected target and one to three unique absolute candidate values.
- Capture a baseline and one image per candidate with bounded response size.
- Roll back and verify the original property and user context after every candidate.
- Return image hashes and deterministic difference statistics without aesthetic
  ranking or automatic acceptance.
- Stop on any hard-context, generation, identity, property, or rollback conflict. User
  navigation, display, selection, and active-object changes are collaborative UI.

This stage deliberately reuses the 0.5.1 Blender authority. Light controls, arbitrary
modifier parameters, node topology, object location/rotation, and file saving remain
out of scope. See `docs/roadmap/0.6.0-comparative-previews.md` for the implementation
and acceptance checkpoints.

### Phase 5 — managed application and project lifecycle

Status: implemented and live-validated on Blender 4.2.23 in 0.7.0; see
`docs/validation/2026-08-29-managed-lifecycle.md`.

- Launch a visible configured Blender without requiring a preinstalled add-on.
- Keep application launch separate from project opening.
- Save, Save As, switch, reload, and quit through typed semantic tools.
- Commit active preview transactions before default save/open/quit workflows.
- Execute file-switching and quit operations on the tick after the acceptance response.
- Reconnect and verify the actual absolute project path before reporting success.

### Phase 6 — semantic static-scene authoring

Status: implemented and validated on Blender 4.2.23 in 0.8.0. See
`docs/validation/2026-08-29-semantic-scene-authoring.md`.

- Upgrade transactions to structural delta capability version 3.
- Add bounded primitives, complete object TRS, duplicate, and deferred delete.
- Add canonical Principled materials, exact slots, local images, and semantic textures.
- Add World/Camera controls and temporary Eevee preview plus explicit PNG/EXR export.
- Validate a deterministic fixture and a complete moonlit-water scene in temporary files.

### Phase 7 — unified typed object settings

Status: implemented and Blender 4.2.23 live-validated in 0.9.0.

- Add a closed typed `object.set` surface for object, Light, and Camera settings.
- Keep one public object-level entry while dispatching to typed internal handlers.
- Guard shared object data by exact identity, type, users, and explicit shared scope.
- Route typed object-setting comparisons through the same writer and rollback checks.
- Preserve all 0.8 tools when connected to an older add-on; reject only new capability
  use.

### Phase 8 — bounded typed Modifier authoring

Status: implemented and Blender 4.2.23 live-validated in 0.10.0; linked-data guard and
duplicate-selection regressions live-validated in 0.10.1.

- Inspect exact full ordered Modifier stacks and guard them with one fingerprint.
- Create, configure, reorder, and defer deletion for four bounded Modifier families.
- Preserve `modifier.set_state` compatibility and reject only the new surface when
  `modifier_authoring: 1` is absent.
- Compare one typed Modifier field through independent rollback-safe candidates.

### Phase 9 — semantic Mesh topology, selection resources, UV, and weights

Status: 0.11 through 0.13 implementation, automated gates, and Blender 4.2.23 release
gates complete. Version 0.12 adds SelectionSet and evaluated-surface fitting on the
snapshot model. Version 0.13 adds exact one-revision ComponentMaps plus seven bounded
topology handlers. Version 0.14 adds bounded UV and deform-weight authoring without
hiding either responsibility in Modifier tools.

- Page exact base-Mesh components and bind indices to full fingerprints.
- Edit one closed semantic operation through transaction-v4 snapshots.
- Preserve explicit object-only or complete shared-data scope.
- Keep material surface detail, Modifier effects, Mesh structure, UV, and weight
  authority as separate decisions.

### Phase 10 — materialized Mesh modules and rig assembly

Status: implemented and validated in 0.15.0 on the merged 0.14 baseline.

- Materialize BASE, current Shape-Key-only, or final evaluated geometry into a new
  independent object with explicit material/UV/weight copy policy.
- Extract one or more disconnected face components into one exact object branch.
- Inspect and bind existing deform groups to an exact Armature without generating or
  rewriting weights implicitly.
- Keep the source object, Shape Keys, Modifier stack, and binding unchanged.
- Validate the complete materialize → extract → bind chain with rollback, native-save
  adoption, save/reload, and a real modular-character fixture.

### Phase 11 — component catalogs and cross-object assembly

Status: implemented and validated in 0.15.1; see
`docs/validation/2026-09-01-component-catalog-assembly.md`.

- Partition a FACE SelectionSet into a compact revision-bound ComponentCatalog without
  eagerly consuming SelectionSet resources.
- Create and organize exact Collections and object-level parent relationships through
  reversible structural deltas.
- Extend the existing declarative Mesh batch with materialize, extract, organization,
  and rig-binding aliases while preserving v1/v2 requests.
- Return a hashed assembly manifest as response evidence without writing persistent
  project-specific metadata.

### Phase 12 — controlled Library and template coverage

- Inspect exact absolute `.blend` catalogs under streamed SHA/size evidence.
- Append one bounded Object, Collection, or Mesh root as local editable data.
- Reject scripted, animated, linked, override, constraint and Geometry-Nodes closures.
- Compose append, typed object alignment, dynamic SurfaceRefs, fitting, attribute
  transfer, organization, rig binding and validation through batch v4.
- Retain hidden-region template/cage priors instead of claiming reconstruction of
  geometry that the evaluated source does not contain.

Completed on Blender 4.2.23 LTS with deterministic Library fixtures and a temporary
`test-model.blend` copy; see
`docs/validation/2026-09-01-library-template-coverage.md`.

### Phase 13 — cross-object Mesh composition

Status: implemented for 0.17.0; isolated Blender 4.2.23 release evidence pending.

- Preflight and join 2–32 exact BASE Mesh-object inputs into one independent output.
- Reconcile material, UV, color and weight schemas only through explicit policies.
- Return one partial JOIN_BRANCH ComponentMap per source instead of inventing a
  synthetic source Mesh.
- Weld revision-bound boundary SelectionSets as a separate deterministic topology
  operation.
- Compose join and weld through batch v5 with guarded source retention or deletion.
- Keep up to 192 SelectionSets and 128 ComponentMaps so one 32-source join can return
  all promised branch/domain/boundary evidence without self-evicting; aggregate component
  and relation budgets remain unchanged.

See `docs/roadmap/0.17.0-cross-object-mesh-composition.md` and decision 0019.

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
- Whether a bounded repository script tool is necessary beyond project-owned Blender
  drivers/startup scripts; arbitrary inline Python remains out of scope.
- Which bounded retopology and custom-normal operations can preserve the 0.14 attribute
  evidence without exposing arbitrary Mesh arrays.
- How a later Shape-Key migration authority should preserve relative-key graphs,
  drivers, masks, and animation without being conflated with 0.15 materialization.
- Which exact rest-pose and coordinate contracts 0.19 bone authoring should use before
  pose or animation authority is considered.
- How 0.20 Modifier Apply should report lineage when a supported Modifier changes
  topology, and when it must explicitly return lineage unavailable.
- Blender 5.x capability policy and the project license; decide both before publishing.

## 13. Guidance for a new Codex task

At the start of a new task:

1. Read AGENTS.md and this document completely.
2. Inspect Git status and do not overwrite uncommitted IDE or user changes.
3. Use uv for all Python dependency and execution work.
4. Keep Blender 4.2.23 and Python 3.11 add-on compatibility.
5. Develop on port 9877 and require explicit capability negotiation.
6. Use temporary `.blend` copies for lifecycle validation; never switch to or save over
   the source integration fixture.
7. Prefer one vertical slice—connect, observe, mutate, rollback, verify—over a broad
   catalogue of unfinished tools. Use `observation.bundle` before adding new mutation
   authority.
8. Use `docs/validation/2026-08-29-semantic-scene-authoring.md` as the 0.8 live baseline;
   report the older 0.6 comparative live gate separately rather than implying it passed.
