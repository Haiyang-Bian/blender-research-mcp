# Documentation handbook

This directory is the authoritative handbook for Blender Research MCP. Read documents
in this order when starting a new implementation task:

1. [Design and handoff](design.md) — architecture, safety boundaries, implemented
   phases, and open decisions.
   - [Post-0.16 model-editing completeness direction](requirements/model-editing-completeness.md)
     — accepted 0.17–0.20 capability sequence.
   - [0.17.0 cross-object Mesh composition roadmap](roadmap/0.17.0-cross-object-mesh-composition.md)
     — implemented exact join, lineage branches, seam welding, and bounded live gate.
   - [0.17.0 cross-object Mesh composition validation](validation/2026-09-01-cross-object-mesh-composition.md)
     — deterministic pass plus explicit aggregate-stress and character-cage gaps.
   - [0.17.1 Collection rollback and bounded UV inspection validation](validation/2026-09-01-transaction-rollback-collection-guard.md)
     — guarded nested-Collection recovery, disconnect rollback, and real-character UV paging.
   - [0.17.2 Mesh recovery and Join hotfix](validation/2026-09-03-mesh-recovery-and-join-hotfix.md)
     — Group identity preservation, verified edge lineage, and native UV/Pin regression evidence.
   - [Cross-object Mesh composition decision](decisions/0019-cross-object-mesh-composition.md)
     — implemented transaction-v13 Mesh-domain authority.
2. [0.16.0 controlled Library and template coverage roadmap](roadmap/0.16.0-library-template-coverage.md)
   — accepted local Library evidence, transactional append, and batch-v4 direction.
3. [Controlled Library and template coverage decision](decisions/0018-controlled-library-template-coverage.md)
   — transaction-v12 asset-ingress and template-prior boundary.
4. [0.15.1 ComponentCatalog and cross-object assembly roadmap](roadmap/0.15.1-component-catalog-assembly.md)
   — accepted compact component, scene organization, and batch-v3 authority.
5. [ComponentCatalog and cross-object assembly decision](decisions/0017-component-catalogs-and-cross-object-assembly.md)
   — transaction-v11 resource, organization, and response-manifest boundary.
6. [0.15.0 modular character materialization roadmap](roadmap/0.15.0-modular-character-materialization.md)
   — accepted materialize, extract, and rig-binding authority.
7. [Materialized Mesh modules and rig binding decision](decisions/0016-materialized-mesh-modules-and-rig-binding.md)
   — explicit evaluation modes and transaction-v10 assembly boundary.
8. [0.14.0 UV and skin-weight roadmap](roadmap/0.14.0-uv-and-skin-weights.md)
   — validated attribute ownership, migration, and validation baseline.
9. [UV and skin-weight decision](decisions/0015-uv-and-skin-weight-authority.md)
   — isolated unwrap, deform-data ownership, and transaction-v9 semantics.
10. [0.13.1 separation and Mesh-batch roadmap](roadmap/0.13.1-mesh-separation-batches.md)
   — validated branch lineage and declarative execution boundary.
11. [Separated-branch and batch decision](decisions/0014-separated-mesh-branches-and-declarative-batches.md)
   — composed maps, transaction-v8 separation, and all-or-nothing batch semantics.
12. [0.13.0 topology and ComponentMap roadmap](roadmap/0.13.0-topology-component-maps.md)
   — validated one-revision topology lineage and acceptance gate.
13. [0.12.0 SelectionSet and surface-fitting roadmap](roadmap/0.12.0-selection-surface-fitting.md)
   — validated selection, surface resource, and deformation baseline.
14. [Modular character surface requirements](requirements/modular-character-surface.md)
   — accepted materialize, disconnected extraction, rig binding, and template direction.
15. [General Mesh authoring requirements](requirements/general-mesh-authoring.md) —
   long-term selection, topology, attribute, Shape-Key, and validation direction.
16. [Using Blender Research MCP](usage.md) — current operator workflow and error
   recovery.
17. [Topology lineage decision](decisions/0013-revision-aware-topology-lineage.md) —
   one-revision ComponentMap and transaction-v7 boundary.
18. [Selection and evaluated-surface decision](decisions/0012-revision-bound-selection-and-surface-resources.md)
   — revision resource and transaction-v6 boundary.
19. [Collaborative UI and native-save authority](decisions/0011-collaborative-ui-and-native-save-authority.md)
   — transaction-v5 user intent and main-thread ordering.
20. [0.11.0 semantic Mesh editing roadmap](roadmap/0.11.0-semantic-mesh-editing.md) —
   exact base-Mesh pages, snapshot guards, shared scopes, and bounded topology edits.
21. [0.10.0 Modifier authoring roadmap](roadmap/0.10.0-modifier-authoring.md) — typed
   inspection, stack guards, four Modifier families, and comparison.
22. [0.9.0 unified object settings roadmap](roadmap/0.9.0-unified-object-settings.md) —
   typed object, Light, Camera, and comparison settings.
23. [0.8.0 semantic scene authoring roadmap](roadmap/0.8.0-semantic-scene-authoring.md) —
   structural transactions, objects, materials, local images, World/Camera, and renders.
24. [0.7.0 managed lifecycle roadmap](roadmap/0.7.0-managed-lifecycle.md) — application
   launch, project switching, implementation, and completed live evidence.
25. [0.6.0 comparative preview roadmap](roadmap/0.6.0-comparative-previews.md) — the
   implemented and live-validated comparison contract.
26. [Architecture decisions](decisions/README.md) — accepted protocol and authority
   decisions.
27. [Validation records](validation/) — evidence from real Blender 4.2.23 smoke tests.

## Current release

Version 0.13.0 is the previous validated milestone. It adds one-revision ComponentMaps,
SelectionSet remapping, and bounded subdivide, loop-cut, bisect, split, bridge, fill,
and grid-fill operations on top of the 0.12 selection/surface foundation. Its automated
and Blender 4.2.23 gates have passed; see
[the 0.13 validation record](validation/2026-08-31-topology-component-maps.md).

Version 0.13.1 is the previous validated milestone. It composes exact map chains,
separates one connected face region into a guarded object branch, and executes bounded
Mesh-only declarative batches with named resources, automatic remapping, validation
assertions, one-generation success semantics, and whole-transaction runtime recovery.
Its automated, focused Blender, and full `test-model.blend` regression gates passed;
see [the 0.13.1 validation record](validation/2026-08-31-mesh-separation-batches.md).

Version 0.14.0 is the previous validated milestone. It adds exact UV layers,
isolated official unwrap/pack, Vertex Group weights, topology/nearest attribute
transfer, attribute-aware topology migration, validation, and batch composition under
transaction capability 9. Its automated and Blender 4.2.23 gates have passed. See the
[0.14 roadmap](roadmap/0.14.0-uv-and-skin-weights.md) and
[decision 0015](decisions/0015-uv-and-skin-weight-authority.md), plus the
[0.14 validation record](validation/2026-08-31-uv-and-skin-weights.md).

Version 0.15.0 is the previous implementation milestone. It creates independent Mesh
outputs from explicit BASE, SHAPE_KEYS_CURRENT, or FINAL_EVALUATED evidence, extracts
one or more disconnected FACE regions with exact branch lineage, and binds existing
weights to an exact Armature under transaction capability 10. The deterministic and
full `test-model.blend` gates passed; see the
[0.15 validation record](validation/2026-09-01-modular-character-materialization.md).
It does not authorize Shape-Key structure editing or Modifier Apply. See the
[modular character surface requirements](requirements/modular-character-surface.md).

Version 0.15.1 is the previous validated milestone. It adds compact revision-bound
ComponentCatalogs, exact Collection/object-parent organization, and cross-object
`mesh.batch.execute` v3 without adding persistent project metadata. See the
[0.15.1 roadmap](roadmap/0.15.1-component-catalog-assembly.md) and
[decision 0017](decisions/0017-component-catalogs-and-cross-object-assembly.md). The
automated and Blender 4.2.23 gates are recorded in
[the 0.15.1 validation record](validation/2026-09-01-component-catalog-assembly.md).

Version 0.16.0 is the latest fully live-validated milestone. It adds SHA-bound local
Library inspection, transactionally guarded local-data append, and batch-v4 template
coverage composition. See the
[0.16 roadmap](roadmap/0.16.0-library-template-coverage.md) and
[decision 0018](decisions/0018-controlled-library-template-coverage.md). The automated,
deterministic Blender, and `test-model.blend` coverage gates are recorded in
[the 0.16 validation record](validation/2026-09-01-library-template-coverage.md).

Version 0.17.0 is implemented with automated acceptance and its deterministic Blender
commit/save/reload gate complete. The aggregate same-process rollback stress and the
real-character cage-composition gate remain pending. It adds read-only join preflight,
transactional independent Mesh composition, one exact JOIN_BRANCH map per source,
explicit SelectionSet-based seam welding, and batch v5. It deliberately precedes
Shape-Key, bone-authoring, and Modifier-Apply authority. See the
[0.17 roadmap](roadmap/0.17.0-cross-object-mesh-composition.md),
[decision 0019](decisions/0019-cross-object-mesh-composition.md), and the
[post-0.16 completeness direction](requirements/model-editing-completeness.md). Exact
evidence and the remaining limitation are recorded in
[the 0.17 validation record](validation/2026-09-01-cross-object-mesh-composition.md).

Version 0.17.1 is the current validated patch. It refreshes transaction-owned
Collection structure guards whenever a later Agent operation places or removes an
object, while continuing to reject real external Collection edits. It also bounds
non-summary UV inspection work, gives UV inspection the existing 30-second protocol
deadline, and preserves `REQUEST_TIMEOUT` instead of masking it as a reconnect failure.
The automated and Blender 4.2.23 evidence is recorded in
[the 0.17.1 validation record](validation/2026-09-01-transaction-rollback-collection-guard.md).

Version 0.12.0 adds session-local revision-bound SelectionSets, read-only evaluated
SurfaceRefs, topology-preserving surface deformation, and quantitative Mesh validation;
see [the 0.12 validation record](validation/2026-08-31-selection-surface-fitting.md).

Version 0.11.1 upgrades transactions to capability 5: UI navigation, display, selection,
and active-object changes are collaborative, while Blender native save accepts current
visible state as an intent barrier and prevents later automatic rollback. Version
0.11.0 adds paged exact base-Mesh inspection and one transaction-v4 semantic
component writer with explicit object-only/shared-data scope and reversible Mesh
snapshots. Its automated and Blender 4.2.23 release gates have passed; see
[the 0.11 validation record](validation/2026-08-30-semantic-mesh-editing.md).
The transaction-v5 patch is independently recorded in
[the 0.11.1 validation record](validation/2026-08-30-collaborative-ui-native-save.md).
Version 0.10.2 retains the 0.10.0 exact ordered Modifier-stack surface, the 0.10.1
linked-data guard repair, and compares numeric transaction evidence at Blender's
actual float32 RNA storage precision.
Version 0.10.0 added exact ordered Modifier-stack inspection plus create, typed set,
reorder, deferred delete, and candidate comparison for Bevel, Subdivision, Solidify,
and Boolean. It builds on the live-validated 0.9 unified object-setting surface.
Structural transaction v4 retains v3 exact create, unlink/delete, material-slot,
node-link, World, and Camera rollback and adds bounded base-Mesh snapshots. The 0.8
automated suite and real Blender moonlit-water gate have both passed; see
[the 0.8 validation record](validation/2026-08-29-semantic-scene-authoring.md). The 0.9
automated and real Blender gates have both passed; see
[the 0.9 validation record](validation/2026-08-30-unified-object-settings.md). The
independent 0.6 real comparison gate has also passed; see
[its validation record](validation/2026-08-30-comparative-previews.md).
The 0.10 automated and real Blender gates have both passed; see
[the 0.10 validation record](validation/2026-08-30-modifier-authoring.md).
The linked-data guard hotfix and full 0.10.1 regression are recorded in
[the 0.10.1 validation record](validation/2026-08-30-linked-data-guard-hotfix.md).
The float32 guard repair and independent material-wave moon-water scene are recorded in
[the 0.10.2 validation record](validation/2026-08-30-float32-guard-and-moon-water.md).

The public repository is
[Haiyang-Bian/blender-research-mcp](https://github.com/Haiyang-Bian/blender-research-mcp).
PR #1 merged the validated 0.2–0.5.1 history into `main`; obsolete phase branches were
removed after their commits were verified reachable from `main`.

## Authority boundary

Documentation does not grant additional runtime authority. Unless a later accepted
decision explicitly changes the contract, the project still forbids arbitrary Python,
external network services, arbitrary node graphs, arbitrary Mesh/BMesh operations or
  arrays, generic custom attributes, animation, Cycles, and force-overwriting
  transaction conflicts. UV and weight writes are limited to their typed 0.14 tools;
  materialization, extraction, and rig assembly use only their typed 0.15 tools.
Bounded semantic base-Mesh component edits are available only through `mesh.edit`.
Local absolute-path image loading,
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
