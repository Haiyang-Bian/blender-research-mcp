# Blender 4.2.23 unified object-settings smoke

- Status: passed
- Date: 2026-08-30
- Branch: `codex/unified-object-settings`
- Run ID: `20260829T164727Z-d4c53b3c`
- Blender: `4.2.23 LTS`
- Add-on and external MCP server: `0.9.0`
- Protocol: `1`
- Port: `9882`
- Elapsed time: `32483.486 ms`

The gate cold-launched a managed Blender session on an isolated port, opened a
deterministic temporary `.blend`, exercised the new typed object-setting path, saved and
reloaded the changed copy, produced a reviewed Eevee preview, and then explicitly quit
the managed process. The session was PID `14796`, instance
`dda5dc9d-4d62-4fee-a199-e75fc606afbc`, and launch ID
`29515527-b4a1-45e0-94df-ebfda4fbec83`. Its materialized resource hash was
`ef2aaafcc7d2b8daa7e4d1a0f4159d848fb7f2443e4380551286596792255bda`.

## Typed settings and transaction evidence

The run inspected and changed the supported data for every Light family:

- Point: energy, hexadecimal sRGB color, and radius;
- Spot: energy, color, radius, spot size, and blend;
- Sun: energy, color, and angular diameter;
- Area: energy, color, shape, size, and secondary size.

It also changed perspective Camera transform, lens, sensor width, clipping, and shifts
in one request, recording exactly transform and Camera-data deltas while advancing the
scene generation once. An orthographic Camera request combined transform, orthographic
scale, clipping, and shifts. Both requests were rolled back and their complete inspected
state matched the baseline.

Two-object Light and Camera fixtures shared their respective data-blocks. The default
write returned `SHARED_OBJECT_DATA_CONFIRMATION_REQUIRED`; the same inspected identity
and user count with `allow_shared_data=true` changed both users, and rollback restored
both. Unified `object.set` and legacy `object.transform` no-op requests each recorded
zero deltas and did not advance the scene generation.

The first live attempt also exposed a Blender-RNA-specific defect that fake data had
hidden: Point Light data has no Area-only `shape` property. The Area shape/size_y
validation was narrowed to Area data and a Point-without-shape regression was added
before this passing run.

## Comparative previews

Four `object_setting` targets ran through the production comparison service:

- `Point Light.energy`;
- `Point Light.color`;
- `Area Light.size`;
- `Perspective Camera.lens`.

Every result returned `baseline, A, B`, used a separate transaction per candidate, and
proved target, evidence object, and user context restoration. Scene generations advanced
`17→21`, `21→25`, `25→29`, and `29→33` respectively. The fixture deliberately used a
stable SOLID object view to isolate writer and restoration behavior, so all candidates
received the expected `CANDIDATE_VISUALLY_INDISTINGUISHABLE` warning. The comparison
contract treats this as a warning and neither ranks nor commits a candidate.

## Commit, persistence, and reviewed render

The final request atomically moved `Perspective Camera` to
`[8.5, -10.5, 7.5]`, set its lens to `65 mm`, sensor width to `38 mm`, and horizontal
shift to `0.02`, then committed. `project.save` and `project.reload` both succeeded, and
the re-inspection retained the committed location and lens.

The 320×256 Eevee Next preview contained `90005` PNG bytes, rendered in `230.152 ms`,
restored all temporary render settings, and was visually checked as a non-blank lit cube
with a floor, cast shadow, and valid camera composition. Its SHA-256 is
`fdea999bdc57d239f436b81fd66906f647be37af1e6f6edbe21bda5068d3004e`.
The Blender heartbeat advanced from `3` to `289` during the gate.

## File integrity and evidence

The deterministic source fixture was created under `%TEMP%` and only its copied project
was opened and saved. The source SHA-256 remained
`165a123498dc25bd70253d71cfe3c77ae8cdffa01280dc2c329b73425781b293`
before and after the run. The saved project copy SHA-256 is
`7628bd5d8f934a99c94ccc61069fb13ed98ab3d9c7145250086fc85b936b0fec`.

Ignored evidence is under
`artifacts/live-smoke/20260829T164727Z-d4c53b3c/`. The report SHA-256 is
`32958686e129cfb1726d504b8680a5b216795ed26347aa4abe82536c43007d88`;
the fixture-build log SHA-256 is
`2c927a76793e4c1dae762f2a780107fbd89658ad803be42c1e2cd0e6e5a8cf02`.
