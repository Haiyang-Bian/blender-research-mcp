# 0014 — Separated Mesh branches and declarative batches

- Status: accepted for 0.13.1 implementation
- Date: 2026-08-31

## Context

ComponentMaps in 0.13 prove lineage across one topology revision, but a useful
character-authoring sequence often spans several revisions. Callers currently have to
retain every intermediate map and manually remap every SelectionSet. They also cannot
split a semantic face region into an independently editable object without falling
back to Blender operators or unrestricted Python.

Longer modeling workflows additionally suffer from transport churn: every selection,
topology write, remap, fit, and validation is a separate main-thread request. The
individual tools remain useful for review, but a known deterministic sequence needs a
bounded way to execute atomically without exposing a generic script language.

## Decision

Add exact ComponentMap composition for two to eight strictly continuous maps. The
composition follows recorded lineage only; it never guesses correspondence from
position, normal, or distance. The composed map is an ordinary session resource and
can be inspected, released, and used to remap a SelectionSet.

Add `mesh.separate` for one connected, non-empty, proper-subset FACE SelectionSet. The
operation always has object-local scope: a shared source Mesh is transactionally made
single-user for the target object, peers remain unchanged, and the selected faces
become an independently owned Mesh object. Two branch maps share one separation ID:
one maps the original revision to the remaining source, and one maps it to the new
object. Boundary components may have an exact descendant on both branches.

Add `mesh.batch.execute` as a closed Mesh-only plan with typed selection query,
selection derive, Mesh edit, Mesh separation, and validation steps. A call-local symbol
table names exact targets and resources. Topology writes automatically remap every
affected SelectionSet alias, while each target branch retains its map chain and emits
a composed map after two or more steps.

Batch preflight validates all static schemas, aliases, target evidence, capabilities,
and worst-case transaction capacity before the first write. A preflight failure does
not touch the scene or the caller's existing transaction. Once execution starts, any
write, resource, budget, or assertion failure rolls back the entire active transaction,
including Agent writes made before the batch. Successful execution advances the scene
generation once even though Mesh revisions and ComponentMaps remain stepwise.

Upgrade transactions to capability 8, ComponentMaps to capability 2, and topology to
capability 3. Add separate capabilities for separation and batching. Native save and
user UI collaboration retain the transaction-v5 ordering contract.

## Alternatives

- **Let callers concatenate map pages.** Rejected because relation classification,
  created/deleted components, budgets, and final live-state validation belong in one
  authoritative implementation.
- **Expose Blender's Separate operator.** Rejected because it depends on Edit Mode and
  true selection, and cannot provide exact transaction guards or branch lineage.
- **Add arbitrary JSON command scripts.** Rejected because it would bypass closed
  schemas, capability negotiation, capacity preflight, and per-step evidence.
- **Roll back only writes performed inside the batch.** Rejected because a runtime
  failure would leave the active transaction in a partially accepted state contrary to
  the declared all-or-nothing workflow.

## Consequences

Agents can carry named semantic regions through a bounded multi-revision workflow and
continue independently on a separated object without guessing indices. Separation
must preserve supported Mesh attributes and object-shell state, and it must maintain
two guarded Mesh branches through commit, rollback, disconnect, and native save.

The batch runtime is intentionally not a general scene authoring language. Materials,
objects other than a separation result, Cameras, World, rendering, UV/weight writes,
Shape Keys, evaluated-mesh materialization, and Modifier Apply remain separate or later
authorities.
