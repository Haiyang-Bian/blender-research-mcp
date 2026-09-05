# TARGET_INTERSECTION returns whole-object hits for a one-face SelectionSet

> Tracking update, 2026-09-05: the user has now authorized committing this report.
> Still open as QLT-02 in the [0.17.6 plan](../roadmap/2026-09-05-rendering-and-animation-review.md).
> Earlier instructions about leaving the report uncommitted describe its original reporting scope.

## Status and impact

Reproduced on 2026-09-03 with the public semantic tools. A FACE SelectionSet containing only face 2416 returns 382 faces, all outside that selection; face 2416 is not returned. This makes the result unsafe to interpret as a local patch acceptance test. Whole-object queries used for the head's self-intersection acceptance remain whole-object queries; this report does not invalidate them.

No implementation changes, public issue, or commit requested. Leave this report uncommitted.

## Environment and checkpoint

- Windows; Blender 4.2.23 LTS; live add-on 0.17.5; protocol 1.
- Live instance: 52625d21-1ccc-4244-a3e6-0ffe1fd29651.
- Live capabilities: mesh_validation 3, mesh_selection 2, transactions 13.
- Tool repository checkout: 5b8910d1695cccae5cf4bc515f5787ce85d95102. This is source evidence, not proof of exact loaded source bytes.
- Source project: C:\Users\26687\Work\projects\blender-projects.
- Saved asset: test-model-fill-checkpoint.blend; SHA-256 ef0e269819678fbe4356accf229485ad138736d33defc1e9984d4aafcda8885f.
- Target: CODEX_HeadComplete_HeadShell, 3759 vertices, 10319 edges, 6558 faces.
- Mesh revision: 108810eac7610bc405a7b05c2eb85683b3e9cc6864e85b36ed1c20dc62bee293.
- Object/mesh identities: object:2b02a8f4900 / mesh:2afd1159840.
- Object Mode. Head geometry is never written during this probe.

All IDs and face indices below are historical evidence, not live authority for future replay.

## Minimal reproduction

1. Inspect the saved head and the existing CODEX_Modular_Hair_Object. The head is at local X=1.600000023841858; hair is at X=0.
2. In transaction 4dfb8db3-56fa-4805-8e2e-da7ec9b64c5a, temporarily align only the hair object's X to the head. Do not change mesh data, visibility, original model, or save the temporary alignment.
3. Prepare the aligned hair as a BASE SurfaceRef.
4. Create a fresh FACE SelectionSet with query {"type":"indices","indices":[2416]}. Confirm component_count=1. This generated crown triangle's center is [-0.0015202240319922566,0.04516637325286865,1.565364956855774].
5. Call mesh.validate with the following valid input:

~~~json
{
  "selection_id": "d68de047-6103-494c-8c03-dc5baadf4b7d",
  "surface_id": "2439a155-8df8-4370-89d9-08bffee2cf5a",
  "check": "TARGET_INTERSECTION",
  "tolerance": 1e-8,
  "sample_limit": 16
}
~~~

6. Inspect the complete returned selection with limit=512.

## Actual result

~~~json
{
  "check": "TARGET_INTERSECTION",
  "count": 382,
  "selection_id": "dc1609d1-8d30-481f-9377-0ae482f940dd",
  "component_count": 382,
  "scene_generation": 25
}
~~~

- Returned faces: 382; all 382 are outside the requested face.
- Requested face 2416 is absent.
- Examples: 1, 2, 3, 4, 5, 19, 20, 21, 22, 33.
- The same 382-face result had previously been observed for the full 6558-face head against the same aligned hair geometry.
- Full returned indices are retained in the source project's configs/head-finish-20260903.json; no scene assets are copied here.

## Expected behavior and analysis

The interface accepts a revision-bound SelectionSet and describes bounded topology/intersection validation. A local FACE selection should either restrict reported source faces to that selection, or be explicitly rejected/documented as unsupported for this check; silently returning unrelated faces makes local acceptance misleading.

Source-supported explanation: blender_addon/blender_research_mcp_addon/mesh_surface_ops.py lines 782-810 builds triangles/BVH from every vertex and loop triangle, computes overlaps, and creates the result from all polygon_indices without intersecting the source-face selection. The SELF_INTERSECTION branch shares this pattern, but that branch's subset behavior has not been independently reproduced here.

The live behavior is reproduced; exact loaded implementation/source equality has not been established.

## Agent input mistake, not a tool bug

An initial call supplied scope="SELECTION". It was correctly rejected with:
MESH_VALIDATION_INVALID: scope is supported for local topology checks.

The successful reproducer above omits scope. This rejected input is not the defect and did not write data.

## Recovery and limits

- Validation itself is read-only.
- Transaction rollback succeeded, restoring hair X=0.
- Follow-up object.inspect verified [0,0,0].
- Head mesh fingerprint remained 37289e1967595003f56d1cf5d238bd1d2eadf7834a4f13872d2726e7b53c4b63.
- Saved head remains the previously validated/reloaded checkpoint; no probe save occurred.
- This report concerns result scope, not whether each BVH contact is a physically meaningful penetration. Hair is open and sign_reliable=false.

## Regression acceptance

- One non-intersecting selected face plus distant intersecting faces: return zero local hits, or a clear unsupported-scope error.
- One intersecting selected face: return only selected source faces.
- Full-object selection: preserve whole-object counts.
- Test FACE, EDGE, and VERTEX selection-domain behavior or reject unsupported domains explicitly.
- Specify neighbor semantics if supported; never silently scan/report unrelated source regions.
- Verify stale SelectionSet/SurfaceRef errors, unchanged mesh/UV/weights, and user-context preservation.
