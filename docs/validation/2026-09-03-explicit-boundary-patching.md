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

0.17.5 and real-scene A12 remain pending. No artistic approval is claimed.

Environment: native sandbox pytest encountered WinError 5 on existing host temporary
directories; the unchanged uv command is run with authorized host permissions.
`uv sync` resolved the unchanged dependency set but could not replace the running
MCP executable (Windows error 32). Source imports, version-matched managed add-on and
validation use the existing uv environment; no cache relocation or pip fallback.
