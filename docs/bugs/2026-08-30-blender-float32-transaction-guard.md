# Blender float32 transaction guard false conflict

- Status: resolved
- Fixed package/add-on version: 0.10.2
- Discovered by: independent `moon-water-serenity` scene authoring

## Symptom

Inside one structural transaction, an `object.set` wrote Camera
`location.z = 6.2`. A following, unrelated `object.set` for a Light failed with:

```text
PROPERTY_CONFLICT: object_location.z changed outside the transaction
```

Inspection showed that no user edit had occurred. Blender returned the Camera value as
`6.199999809265137`, the IEEE-754 single-precision representation of the submitted
decimal.

## Cause

Transaction evidence compared Python floats with a fixed absolute tolerance of `1e-7`.
The Camera round trip differed by about `1.91e-7`, so the add-on misclassified its own
successful RNA write as external drift. The comparison-service duplicate/baseline
logic used the same unsuitable tolerance.

## Resolution

Numeric equality now compares the packed IEEE-754 float32 representation used by
Blender RNA. Submitted values and Blender readback therefore agree at the actual
storage precision. Distinct adjacent float32 values remain unequal, so a genuine
one-ULP external edit is still protected as a conflict.

The change applies recursively to transaction RGB/vector tuples and to external
comparison candidate/baseline checks. Boolean, integer, enum/string, and tuple-length
semantics remain strict and unchanged.

## Regression coverage

- Unit tests cover `6.2` versus its Blender float32 round trip.
- Unit tests construct the adjacent float32 value and require inequality.
- The original live sequence `Cube → Camera(6.2) → Light` completed in one transaction.
- A complete scene-authoring transaction then continued through object, Light/Camera,
  material, local image, World, and Modifier writes without the false conflict.

See [the 0.10.2 validation record](../validation/2026-08-30-float32-guard-and-moon-water.md).
