# Blender 4.2.23 SelectionSet and evaluated-surface fitting validation

- Status: passed, with signed penetration unavailable for the open real target
- Date: 2026-08-31
- Branch: `codex/selection-surface-fitting`
- Run ID: `20260830T165334Z-e50ed1a0`
- Blender: `4.2.23 LTS`
- Add-on and external MCP server: `0.12.0`
- Protocol: `1`
- Port: `9888`
- Elapsed time: `43964.463 ms`

The release gate cold-launched the managed 0.12 add-on, generated a deterministic
fixture, and opened only temporary copies. The managed Blender process was PID `3784`,
instance `57b01c8c-74dc-4b4f-b51b-00d822835c0d`, with launch ID
`8fcfbbb8-e475-417e-8d3f-209879048062`. It advertised `transactions: 6` and version 1
for `mesh_selection`, `mesh_surface_query`, `mesh_deformation`, and `mesh_validation`.
Heartbeat advanced from `3` to `358`.

## Selection and surface resources

The deterministic fixture exercised explicit indices, all components, local/world
sphere and box, plane, normal, material, measure, boundary, connected, set-combine,
domain-convert, expand, geodesic falloff, pagination, explicit release, and stale
resource errors. Capture-bound BOX selection ran in both `VISIBLE_ONLY` and `THROUGH`
mode without changing Blender's real selection. The visible query used the fixed
capture projection and an unprojected view ray, so orthographic views did not depend on
a fictitious perspective camera position.

BASE and EVALUATED SurfaceRefs contained 960 and 3,968 triangles respectively, proving
that the Subdivision/Shape-Key evaluated result was not confused with the base Mesh.
The gate queried closest-point distance, invalidated stale evaluated evidence after a
write, rolled the transaction back, and reused the restored baseline resource.

## Deformation, sharing, and user intent

Each topology-preserving operation ran with rollback and commit on the deterministic
UV/color/material fixture:

- `set_positions`;
- `smooth` and `relax`;
- closest-point `project` and bounded `shrinkwrap`;
- `inflate`;
- best-fit `flatten`.

Every changed call retained its topology fingerprint, advanced one scene generation,
and returned a SelectionSet rebound to the after-revision. Rollback revalidated the
baseline SelectionSet; commit retained the rebound set. Project reloads reacquired
session identities and revisions.

The gate also proved `OBJECT` single-user isolation, `SHARED_DATA` propagation, and
disconnect rollback. During an active deformation, the private UI hook changed active
object, Shading, and Overlay; data rollback succeeded while that collaborative UI state
remained. A native save adopted an active inflate transaction, returned
`TRANSACTION_ACCEPTED_BY_USER_SAVE` to a queued terminal request, survived reconnect,
and rebuilt SelectionSet evidence after reload.

## Real evaluated eye-proxy fit

A temporary copy of `test-model.blend` prepared `绯雪_edit_mesh` as a read-only
EVALUATED SurfaceRef with 118,110 triangles. Generic inspection found the writable
`Portrait_ID_V13_SubjectFX_Sclera_L` proxy and selected all 1,986 vertices without a
task-specific command or a hard-coded component array. One transaction performed a
0.75-factor closest-point shrinkwrap followed by bounded neighborhood relax.

The p95 surface error fell from `0.0161609803326428` to
`0.00404115335550159`; the after/before ratio was `0.25005620156217`, a 74.99% decrease.
Non-manifold edges remained `0 -> 0`, degenerate faces remained `0 -> 0`, and exact
rollback restored Mesh fingerprint
`829ad7053623d70bef28974b1cfc35e57ae380a0adb51c1f4839a65321b89d02`.
Both FRONT and RIGHT fixed-view captures produced non-zero before/after SHA changes.

The target was consistently oriented but not closed, so the public contract correctly
returned `sign_reliable=false` and `PENETRATION` status `SIGN_UNRELIABLE`. A numeric
maximum-penetration claim is therefore deliberately not made. Target-intersection
evidence changed from 68 to 77 faces, but that count is not equivalent to signed depth
on an open surface. A future local-normal or closed-proxy contract is required before
the requested 0.1%-of-bounds penetration threshold can be asserted for this asset.

## Source and image evidence

The deterministic source remained unchanged at SHA-256
`75dadafb263f61de0ce2fa9a5cdd4aafabf8967084b7959d9e70e75df03f6a26`.
The repository's real `test-model.blend` remained unchanged at SHA-256
`e9ce53fbb7bf0af8847eb2238dc080c55a48bca8459ed5ee7a588d12bcf8c059`.

The 512x384 Eevee evidence render contained 175,213 PNG bytes with SHA-256
`2dea928bf891752bd3c662fbcd2293a68e51c9e0ce7bf1359fd5435d8bfdfdd6`.
The ignored structured report is
`artifacts/live-smoke/20260830T165334Z-e50ed1a0/report-0.12.0.json`, SHA-256
`38c1014f81acd0223d151f0b1c8269e06800e43c37145579a89166a2f7d3b6c5`.

## Defects exposed by the live gate

Four runtime-only issues were repaired before the passing run:

1. The strict external handshake model omitted the four 0.12 capability fields.
2. Screen backface filtering derived a camera point from an orthographic view matrix;
   it now uses the capture's actual unprojected ray and inverse-transpose normals.
3. `BMesh.to_mesh()` and edge recalculation reordered loop/edge indices on the real
   asset despite a coordinate-only edit. Deformations now compute in BMesh but batch
   write only vertex coordinates, and same-topology rollback restores data in place.
4. The smoke harness used the wrong viewport hash key and treated pre-existing invalid
   geometry as newly introduced; both checks now use protocol evidence and baselines.

## Automated, skill, and package gates

```text
uv run --no-sync pytest
282 passed

uv run --no-sync ruff check .
All checks passed!

uv run --no-sync mypy
Success: no issues found in 24 source files
```

The repository skill passed `skill-creator` validation and its project-managed
installed copy passed `scripts/install_codex_skill.py --check`. Release resource
verification passed for:

- `artifacts/blender-research-mcp-addon-0.12.0.zip`, SHA-256
  `e873a5a2aad6a5985c16d32a360b24dc0c05467e5f2ed615523cacc64aa77741`;
- `dist/blender_research_mcp-0.12.0-py3-none-any.whl`, SHA-256
  `c3566c9729e6e71aa9da10671d9dcd6db6e071291768ef942172721828175271`.

`uv lock` resolved 44 packages. `uv sync` stopped only while replacing the currently
running `.venv\\Scripts\\blender-research-mcp.exe` (`os error 32`); dependencies were
unchanged, so all ordinary gates continued through `uv run --no-sync` as required.
