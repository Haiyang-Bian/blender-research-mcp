# Explicit grid preflight reports no intersections but written quad intersects neighbors

> Tracking update, 2026-09-05: archived into version control with user authorization.
> Still open as QLT-01 in the [0.17.6 plan](../roadmap/2026-09-05-rendering-and-animation-review.md).
> The original observations below are preserved; the tessellation root cause remains to be isolated.

- Status: observed live on 2026-09-03; implementation not changed.
- Environment: Windows, Blender 4.2.23 LTS, loaded add-on 0.17.5, repository HEAD previously verified 5b8910d.
- Project: C:\Users\26687\Work\projects\blender-projects\test-model-fill-checkpoint.blend.
- Target: CODEX_HeadComplete_HeadShell, object:1c42f0ac080, mesh:1c3c2756e50.
- Recovery: transaction cda47185-8c97-4a92-abfb-bfa0d754a9d5 rolled back successfully at generation 82. Restored 3479 vertices / 9681 edges / 6198 faces; fingerprint b7a902808a0bd14047af4280f4e9d899cd22971dd3d3e1639549868626d0d2dd matches the exact pre-stage fingerprint. Previously saved posterior-neck patch is retained. Defective ear-root patch was not saved.

## Observed contract discrepancy

An explicit CLOSED_LOOP grid_fill with use_interp_simple=true, cyclic corners, matching opposite counts [12,5,12,5], and independent islands on UVMap/UV1/UV2 succeeded. Its candidate preflight reported new_intersections=0 and all introduced_issues arrays empty.

Immediately after the four ear repair patches, whole-mesh SELF_INTERSECTION reported 3 faces: 5140, 5874, 6497. This persisted at tolerances 1e-6 and 1e-8. LOCAL_QUALITY at 1e-10 independently reported intersection pairs [5140,6497] and [5874,6497]. Orientation was consistent, degenerate count 0. This is not a native-user-save conflict, stale selection rejection, or an input-only failed preflight.

The problematic face 6497 was created by the right_lower grid. The subsequent left_lower patch added geometry only and preserved every existing vertex position and edge lineage; it did not change these right-side faces.

## Exact failing operation

- Right lower operation generation: 80
- ComponentMap: c9d49099-52e4-4c7b-bace-0405214b16de
- Before mesh fingerprint: 368feacbc1840160e352a9e72d20cf51385f2591167fb8d9d7784d9ec0853cae
- After mesh fingerprint: be7539958b31ba7b08deb1ae9657c3d639a29d20065d9ca53cbbb4867cd07497
- Before counts: {"vertices":3671,"edges":10113,"faces":6438,"loops":19984}
- After counts: {"vertices":3715,"edges":10216,"faces":6498,"loops":20224}
- New patch: 60 quads / 44 vertices.
- Boundary paths: [[2413,2414,2460,3137,3179,3191,3190,3189,3482,3177,3483,3178,3136],[3136,3265,3266,3267,3268,2694],[2694,3286,3285,3284,3283,3282,1004,3277,3278,3279,3280,3281,2573],[2573,3491,3492,3493,3494,2413]]
- UV: independent islands in all three layers; material slot 51; smooth=true; PRESERVE_INTERPOLATE UV and weights.
- Candidate quality complete: true; new_intersections: 0.

Selection UUIDs are session/revision-bound; recreate exact selections against the listed live topology or a matching fixture. Do not replay stale IDs against another revision.

## Bounded geometry evidence

The following positions and ordered face cycles were read from the actual mesh after writeback. Units are Blender local units.

```json
[
  {
    "face": 5140,
    "vertices": [
      2693,
      2691,
      2694
    ],
    "material": 26,
    "coords": [
      {
        "index": 2693,
        "co": [
          0.041803840547800064,
          0.03207077085971832,
          1.4253860712051392
        ]
      },
      {
        "index": 2691,
        "co": [
          0.04403819888830185,
          0.04297195374965668,
          1.432051658630371
        ]
      },
      {
        "index": 2694,
        "co": [
          0.05263286083936691,
          0.03664665296673775,
          1.4354482889175415
        ]
      }
    ]
  },
  {
    "face": 5874,
    "vertices": [
      3261,
      3268,
      2694,
      2691
    ],
    "material": 51,
    "coords": [
      {
        "index": 3261,
        "co": [
          0.04451226815581322,
          0.047994837164878845,
          1.4336413145065308
        ]
      },
      {
        "index": 3268,
        "co": [
          0.05147320777177811,
          0.04247290641069412,
          1.4363586902618408
        ]
      },
      {
        "index": 2694,
        "co": [
          0.05263286083936691,
          0.03664665296673775,
          1.4354482889175415
        ]
      },
      {
        "index": 2691,
        "co": [
          0.04403819888830185,
          0.04297195374965668,
          1.432051658630371
        ]
      }
    ]
  },
  {
    "face": 6497,
    "vertices": [
      3286,
      2694,
      3268,
      3714
    ],
    "material": 51,
    "coords": [
      {
        "index": 3286,
        "co": [
          0.05062200129032135,
          0.03485666215419769,
          1.433349847793579
        ]
      },
      {
        "index": 2694,
        "co": [
          0.05263286083936691,
          0.03664665296673775,
          1.4354482889175415
        ]
      },
      {
        "index": 3268,
        "co": [
          0.05147320777177811,
          0.04247290641069412,
          1.4363586902618408
        ]
      },
      {
        "index": 3714,
        "co": [
          0.05089179053902626,
          0.0412985198199749,
          1.4362072944641113
        ]
      }
    ]
  }
]
```

## Analysis: facts vs hypothesis

Confirmed: patch candidate success and actual-mesh validation disagree. There were no whole-mesh self-intersections at the saved trimmed-scalp stage, and preflight for the posterior patch and partition faces reported no newly introduced intersections.

Hypothesis requiring a regression fixture: mesh_patch_quality.triangles uses mathutils.geometry.tessellate_polygon, whereas the resulting Mesh and later inspection use Blender loop triangles. A nonplanar quad can choose a different diagonal at actual Mesh tessellation. The code read this session showed the former candidate tessellator and the latter surrounding-mesh loop-triangle path. The precise actual diagonal was not exposed by the semantic mesh inspection API, so this is not yet a proven implementation root cause.

Alternative possibility: numerical or contact classification differences between preflight and LOCAL_QUALITY. Changing SELF_INTERSECTION tolerance did not remove the reported hits. Do not mark fixed based only on source edits.

## Acceptance criteria

1. A minimal fixture containing the three reported face cycles reproduces or definitively rejects the tessellation hypothesis.
2. Candidate validation checks the triangulation that the written Mesh will actually use, or fails without writeback if equivalence cannot be guaranteed.
3. Successful explicit fills must also pass post-write local validation on created faces and their neighbors; on discrepancy, restore geometry AND attributes.
4. Add a regression with a nonplanar quad adjacent to two existing faces and verify face intersection pairs, not just aggregate counts.
5. Retest live on this anatomical boundary and retain normal user-intent/transaction guards.

This report does not authorize implementation changes, publishing, or committing the report.
