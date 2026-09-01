# Decision 0018: controlled Library append and template coverage

- Status: accepted for 0.16.0
- Date: 2026-09-01

## Context

The 0.15 modular workflow can materialize evaluated geometry, extract disconnected
regions, organize objects, and bind exact weights to an Armature. It cannot yet bring
an external, reusable head or body cage into the current project without a human
performing Blender's Append operation. That gap prevents a general workflow for
surfaces which are hidden by hair or clothing: the evaluated character contains no
evidence from which an original covered body can be reconstructed.

An unrestricted asset importer would weaken the semantic boundary. A whole-file or
wildcard append can introduce scripts, drivers, linked libraries, unsupported object
types, and unnamed dependencies which cannot be proven safe to roll back. A
character-specific fitting tool would instead duplicate the existing SelectionSet,
SurfaceRef, deformation, weight-transfer, rig-binding, and validation contracts.

## Decision

0.16 adds two general Library tools:

- `library.inspect` reads the Object, Collection, and Mesh catalog of one exact local
  `.blend` file and returns SHA-256-bound entry identities without changing Blender
  data.
- `library.append` appends one inspected root as local data inside an active
  transaction. The caller supplies the exact output name and destination. The result
  includes every newly created dependency and its session identity.

Library input is not restricted to a project root, but it is bounded by type and
content. 0.16 accepts static Mesh/Armature/Empty template graphs with supported
materials, images, UVs, weights, parents, and modifiers. It rejects Text blocks,
drivers, actions/NLA, constraints, Geometry Nodes, nested Library links, overrides,
and dependency kinds for which rollback evidence is not implemented. It never links
data and never appends an entire file by wildcard.

Each source entry identity is derived from the file SHA-256, data-block kind, and
source name. Output root names are explicit and may not fall back to Blender's numeric
suffixing. Append records a guarded dependency closure; commit retains it,
rollback/disconnect removes it only while it still matches the Agent's expected state,
and native save accepts the visible result as the user's final intent.

`mesh.batch.execute` v4 adds Library input, Library append, typed object settings, and
dynamic SurfaceRef preparation. The existing selection, projection, relaxation,
attribute transfer, organization, rig binding, and validation steps remain the
building blocks for template coverage. No public character-, head-, hair-, or
clothing-specific fitting command is added.

## Template authority

A template or cage is an explicit prior, not recovered source geometry. Visible,
high-confidence regions may be fitted to evaluated character surfaces. Hidden regions
remain governed by the template and bounded smoothing. Responses and validation must
distinguish measured surface evidence from template-derived coverage and must not
claim that hidden original anatomy was reconstructed.

Test and validation libraries are generated in a temporary directory. Character
assets, generated `.blend` files, and renders remain outside this repository.

## Consequences

- Protocol version remains 1.
- Capabilities become `library_inspection: 1`, `library_append: 1`,
  `mesh_batch: 4`, and `transactions: 12`.
- A 0.16 server retains the 0.15.1 tool surface against an older add-on and rejects
  only Library tools and batch-v4 steps.
- Root append is deliberately singular. Multi-root atomic workflows use batch v4.
- Library Link/Override, arbitrary asset download, scripted dependencies, Shape-Key
  structure writes, Modifier Apply, persistent manifests, and retopology remain
  outside 0.16.

## Alternatives considered

- **Expose Blender's whole-file Append operator.** Rejected because it is
  context-sensitive, admits an unbounded dependency graph, and cannot provide exact
  transactional evidence.
- **Ship a standard character `.blend` in this repository.** Rejected because the
  repository is the tool implementation, not an asset distribution project.
- **Add `mesh.template.fit`.** Rejected because it would hide and duplicate the
  existing general-purpose resource and deformation pipeline.
- **Infer covered anatomy only from clothing surfaces.** Rejected because the source
  data does not contain that information.
