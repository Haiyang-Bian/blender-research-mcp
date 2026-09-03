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

These are engineering fixtures, not the real character acceptance A12. Exact face/edge
creation, explicit patches, new attribute provenance and 0.17.5 gates remain pending.

Environment: native sandbox pytest encountered WinError 5 on existing host temporary
directories; the unchanged uv command is run with authorized host permissions.
`uv sync` resolved the unchanged dependency set but could not replace the running
MCP executable (Windows error 32). Source imports, version-matched managed add-on and
validation use the existing uv environment; no cache relocation or pip fallback.
