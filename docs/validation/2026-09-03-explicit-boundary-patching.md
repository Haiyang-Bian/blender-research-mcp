# Explicit boundary patching acceptance

## 0.17.3 checkpoint

Blender 4.2.23 LTS, package/add-on 0.17.3, protocol 1, transactions 13.
Native and socket evidence: `artifacts/live-smoke/boundary-20260903T193604/`.
The ignored directory contains fixture.log, rna.json and report.json.

- Native synthetic tests: ordinary two-chain fill, valid degree-four side branch,
  shortcut with a uniquely valid non-overlapping pairing: 16 faces each.
- Hidden-rail and single-loop diagnostics leave the Mesh fingerprint unchanged.
- Managed visible Blender: authenticated handshake and version, read-only inspection,
  Grid Fill, identical UUID replay, explicit rollback, disconnect recovery/reconnect.
- The original fixture SHA is unchanged; call-local context/UI projections match;
  heartbeat advances after mutations, proving the main-thread timer remains responsive.
- Pure graph tests cover ambiguous alternatives, exhausted budgets, closed-loop types,
  explicit path orientation and structured MCP error preservation.

These are engineering fixtures, not the real character acceptance A12.

## 0.17.4 checkpoint

Blender 4.2.23 LTS, package/add-on 0.17.4. Native and live socket evidence:
`artifacts/live-smoke/boundary-20260903T200000/` (rna.json and report.json).

- Four directed sides and one closed loop with four corners each produce 16 quads,
  nine new vertices and exactly unchanged boundary coordinates.
- Directed open bridge with cuts=2 produces 12 quads and 10 new vertices.
- Existing-edge no-op publishes no map/delta; duplicate faces, bow ties and
  zero-area faces are rejected before writes. A nonplanar quad passes triangulation.
- Three UV layers, old pins, seams, corner colors, 761 Groups including a locked
  Group, and old weights survive; the shared source Mesh stays unchanged in OBJECT scope.
- Ambiguous UV sources reject by default; an explicit independent island succeeds
  and reports that unwrap/pack is still required.
- Injected failures after native writeback and during the third selection publication
  restore the call fingerprint, Group identities and original shared Mesh binding,
  leaving no map or partially published output SelectionSets.
- Map checks compare actual old vertex destinations, edge endpoints and face cycles.
- Managed socket gate repeats explicit rollback, disconnect recovery/reconnect,
  identical UUID replay, UI preservation and advancing heartbeat.
- Automated gate: 419 tests passed.

During fixture development, legacy UV/Pin RNA allocation in the new test fixture
caused native heap corruption. The fixture now uses the existing typed bulk access
pattern; the final runs above complete without the crash. OBJECT-scope copies have
new Group identities by design; unchanged source and restored failures retain theirs.

## 0.17.5 checkpoint

Blender 4.2.23 LTS / build d0cbe84903e8, package/add-on 0.17.5, protocol 1,
transactions 13. Automated gate: **429 tests passed**, Ruff and mypy passed;
the suite includes Python 3.11 AST parsing and add-on version/package checks.
The built distribution is `artifacts/blender-research-mcp-addon-0.17.5.zip`.

Synthetic native/socket evidence is in
`artifacts/live-smoke/boundary-20260903T211821/`. Earlier integrated visual evidence
is in `boundary-20260903T202017/`. Collaborative native-save/UI evidence is in
`artifacts/live-smoke/20260903T125001Z-5669ddcc/report-0.17.5.json`.
These are ignored local evidence directories, not packaged assets.

| Gate | New evidence |
| --- | --- |
| A01–A03 | Pure graph and native boundary fixtures distinguish chain/loop/mixed/hidden/internal/branch inputs; unique alternatives, ambiguity, missing rails and budget unknown are separate. Preflight leaves geometry/resources unchanged. |
| A04–A06 | Explicit native isolated grid, corners, invalid cycles, edge no-op, single faces and open bridge; actual vertex/edge/face lineage verified. Existing closed bridge contract retained. |
| A07 | Synthetic divider → subdivision → side strip → central patch → fixed-boundary projection, 16 faces/9 new vertices; all old boundary coordinates preserved. Real scale-dependent fit metrics below. |
| A08 | Three UV layers, pins, seams, colors/materials, 761 Groups, shared Mesh, post-write and partial-resource-publication faults; local unwrap/pack preserves unselected UV and pins. |
| A09 | Native test hooks exercise navigation/selection/shading, actual data conflicts, commit/rollback, native-save adoption and comparison save barriers. Real socket disconnect/reconnect, same UUID replay and advancing UI heartbeat pass. These are automated engineering checks, not manual user evaluation. |
| A10 | Known alias/domain/geometry errors retain prior transaction edits; query-dependent runtime errors restore begin baseline. Nested exact vertex/path remaps and one-generation successful batch pass. |
| A11 | Local denominator and translated-contact fixtures, zero-net two-iteration displacement limit, output cap/expired deadline preflight, version/capability compatibility, package and full test gates. Incomplete quality cannot pass assertions. |
| A12 | Independent real checkpoint, fresh boundary discovery, complete rollback path, independent second commit/save/reload path and read-only component audit pass. Source SHA remains unchanged. |

During real integration, centimetre-scale triangles around z=1.55 exposed float32
contact errors. Local rebasing and a reported coordinate ULP floor resolve these;
the real contact epsilon is 2.384185791015625e-7 local units. No face-pair exclusion
was added merely because a shared vertex exists. Local length/area tolerances are
separate; UV overlap now requires positive area, excluding shared edge/point contact.

The existing UV operator also exposed unsafe legacy UV-selection allocation and
tile offsets affecting unselected UVs. Typed temporary UV selection arrays and
selected-loop-only copyback resolve both. The new three-layer native regression
includes unwrap, pack, old UV coordinates and pins. Native operations remain bounded
by the shared request deadline; native writeback/recovery themselves are not forcibly
interrupted. Oversized output and expired requests are tested before scene writes.

### Real checkpoint and persistent delivery

Input: `blender-projects/test-model-fill-checkpoint.blend`.
SHA-256 before/after:
`f5c3ea3e6cd6d231d9327e8cd3640a3e3e8d5cd3ae787ad217e5d48e07c536df`.
All character files, scenario scripts, complete coordinates/maps and screenshots
remain in the rendering project's `acceptance/explicit-boundary-20260903/`:

- `patched-0.17.5.blend`: committed and reloaded result.
- `live-audit.json`: independent rollback/commit paths, live boundary queries,
  Map composition, fixed seam, fit, UV, weights, context and reload evidence.
- `delivery-audit.json`: source/result read-only per-component comparison.
- `patch-{front,right,top}.png` and `patch-wire-{front,right,top}.png`.
- `scripts/live_patch_checkpoint.py`, `patch_checkpoint_workflow.py` and
  `patch_checkpoint_checks.py`: scene-specific reproduction profile. Generic
  synthetic examples and the parameterized delivery auditor stay in this repository.

The target is CODEX_HeadComplete_HeadShell. Live spatial/topology queries discover
12 lower and 18 upper boundary segments. Two explicit side faces are created, then
the lower path and six upper segments are subdivided to 24 segments per side;
side rails have six segments. The resulting central patch has 144 quads and 115
interior vertices. This count follows these queried paths, not a product constant.
Including connector/subdivision output, the Mesh changes from 3367/9630/6252 to
3510/9950/6428 vertices/edges/faces.

| Measure | Result and scope |
| --- | --- |
| Units / scale | METRIC, scale_length=1; scene profile spans about 0.1 m. No character-scale defaults were added to the product. |
| Fixed seam | All 60 target loop edges have two adjacent faces; zero remaining central target boundary. Original 3367 coordinates are exactly unchanged, exceeding the configured 1e-7 tolerance. Two intentional outer side-band ends are separately reported; the entire head is not claimed watertight. |
| Local quality | 250 faces / 470 edges / 221 vertices plus surrounding Mesh intersection coverage: no duplicate, degenerate, inconsistent-orientation or intersection faces. Reported remaining boundary edges are the two intentional outer ends. |
| Fit source | Explicitly aligned copy of the existing scalp template. It is a shape prior, not original missing anatomy. 111 interior vertices are covered; 4 beyond 0.015 m remain labelled initial form. on_miss=ERROR applies to the explicitly covered edit set. |
| Fit distance | Covered-set RMS 0.009125895 → 0.006844422 m; final maximum 0.010782752 m, below the recorded 0.015 m threshold. Maximum cumulative displacement 0.003594180 m, below 0.01 m. |
| Seam normals | Maximum adjacent face-normal angle 40.1152°, below the recorded 80° engineering threshold. Artistic curvature acceptance is separate. |
| UV | Three layers, 158 authored faces per layer, minimum UV face area 0.0005366469; no positive-area overlap, explicit tile (1,0). Old atlas remains unchanged. Final texture placement needs artistic review. |
| Skin | Only the live binding-matched head bone is a deform group. MMD edge scale and vertex order are preserved metadata. Six initially unassigned subdivision vertices receive explicit head-group weights from the unique template/binding source; new deform weights are explicitly normalized. New vertices have total 1, one influence, zero unassigned/mismatched deform groups. |
| Persistence | Topology, coordinate, UV and weight content signatures match after reload. Session-scoped material identities correctly change and are not mistaken for persistent-content drift. |
| Source preservation | All 3367 old weight vectors, 56382 old UV/Pin corners, mapped Seams/hidden flags, material slots, 761 Group definitions/locks and Armature binding preserved. Other 1692 objects' geometry and transforms remain unchanged. |

Engineering R01–R10 and A01–A12 are complete. **Artistic approval is pending**,
including final texture layout, silhouette/curvature judgement and acceptance of
the four reference-uncovered initial-form vertices. No reconstructed-original-surface
or human-artistic-pass claim is made.

Reproduction: `uv run --no-sync python scripts/live_smoke_boundary.py --explicit
--blender-executable PATH --port UNUSED_PORT` builds only distributable synthetic
geometry. `scripts/patch_workflow_cases.py` is the compact semantic batch example.
For an already saved real result, `scripts/blender_patch_delivery_audit.py` accepts
explicit source/result/evidence/report paths and target name in a separate Blender
background process; it performs only reads.

Environment: native sandbox pytest encountered WinError 5 on existing host temporary
directories; the unchanged uv command is run with authorized host permissions.
`uv sync` resolved the unchanged dependency set but could not replace the running
MCP executable (Windows error 32). Source imports, version-matched managed add-on and
validation use the existing uv environment; no cache relocation or pip fallback.
