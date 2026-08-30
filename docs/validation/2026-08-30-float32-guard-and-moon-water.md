# 0.10.2 float32 guard and moon-water validation

Date: 2026-08-30

## Scope

This gate used the separate `blender-projects/moon-water-serenity/` delivery directory;
no `.blend`, texture, or rendered scene asset was copied into the MCP repository. It
covered both the newly discovered float32 transaction-guard defect and a user-directed
static scene built through the public MCP schema.

## Runtime

- Blender: 4.2.23 LTS
- Add-on during discovery and fixed-source gate: 0.10.1 resource-hashed managed session
- Cold release gate: server/add-on 0.10.2
- Protocol: 1
- Port: 9877
- Rendering: Eevee Next

The managed resource hash changed after the add-on fix, proving that the cold launch
materialized the updated source rather than reusing the earlier add-on directory.

The dedicated 0.10.2 cold gate used port 9885, PID 20312, launch ID
`b66a3cfe-281d-42d4-af20-52440b01cf92`, and managed resource hash
`08cba27794869f5b966bb8fe9d3fd0a382fee87db617851c0b88e8e5fb118a1d`.
It observed Camera `z = 6.19999980926514`, completed the following Light write,
rolled back both values, and advanced heartbeat 3 → 14 in 2468.885 ms. Its report is
`artifacts/live-smoke/20260830T105639Z-2bcfefa6/report-0.10.2.json` with SHA-256
`ed1c857b840ec5d0bbc1e2b7db22d34bba839cadeb2d7002c1c6ac7ac1816399`.

## Transaction evidence

The original three-step sequence reproduced the defect after writing Camera
`location.z = 6.2`. With the float32 storage comparison in place, the exact sequence
completed through the following Light write. The final scene transaction started from
generation 48 and committed 24 deltas at generation 69 with these delta kinds:

- object create/location/rotation/scale;
- typed Camera and Light settings;
- material create/assign and semantic texture binding;
- image loading and World environment state;
- typed Modifier creation.

One earlier artistic draft containing reflection-strip planes was fully rolled back at
the user's direction. The rollback removed all eight planes, their material, images,
World graph additions, Modifiers, authored objects, and typed property writes. The final
scene contains no `Moon Reflection *` geometry.

## Material-wave scene

The final `Moonlit Water` object is one 96×96 Grid. Its deterministic non-color wave
texture is connected through generated mapping and a Bump node to the Water Material
Principled normal input. The water also retains non-destructive SIMPLE Subdivision
level 1 and Solidify thickness 0.08. Point, Area, and Sun lights produce cool specular
response; the World uses the deterministic equirectangular star texture.

Two 960×640, 64-sample PNGs and one EXR were exported. The PNG evidence was non-blank:

| Evidence | SHA-256 | Bytes |
|---|---|---:|
| Camera A PNG | `6cb39fee02d8f8f94985f98ae43e4955f59c802f3da7c9cec822c788dbaca36e` | 780,726 |
| Camera B PNG | `af684529aa5041b5ab7561b09a5fc7a27c60b3a2bca0975a10d8ce784b78a80a` | 670,142 |
| Camera A EXR | `29cfda9493d9ef0a30b3536be833fdfc855930732afa734a8fb3a019279be18f` | 1,354,644 |

Cross-camera difference statistics were maximum channel difference 243, mean absolute
difference 13.1281, RMS 44.2671, and structural mean absolute difference 11.4591.

## Persistence and context

The committed `.blend` was saved, reloaded with project scripts disabled, re-inspected,
and rendered again. The required objects, Water Bump link, SUBSURF/SOLIDIFY stack, and
active Camera persisted. Before/after user context remained OBJECT mode, active and
selected `Cube`, Layout workspace, Scene/ViewLayer, and frame 1. The heartbeat reached
7858 during the post-reload gate.

## Automated gates

```text
uv run --no-sync pytest
205 passed

uv run --no-sync ruff check .
All checks passed!

uv run --no-sync mypy
Success: no issues found in 21 source files
```

The first sandboxed pytest attempt produced only system-temp `PermissionError` setup
errors. Re-running the unchanged command with normal access to the host pytest temp
directory produced the 205-pass result above.

Release artifacts passed managed-resource verification:

- `artifacts/blender-research-mcp-addon-0.10.2.zip` SHA-256
  `741a89718dbc471d0ee1f0128a42238aa8c9478335bb75812fec0b0a14ab8ba8`
- `dist/blender_research_mcp-0.10.2-py3-none-any.whl` SHA-256
  `27e7a6d346ad23aaddc0a2d554f12716bdc617959f48849883c7d6bb0f99c743`

`uv lock` advanced project metadata from 0.10.1 to 0.10.2. `uv sync` resolved and
built the new package, then stopped only while replacing the currently executing
`.venv\Scripts\blender-research-mcp.exe` (`os error 32`). Dependencies did not change;
all gates and both artifacts used the existing synchronized environment through
`uv run --no-sync`.
