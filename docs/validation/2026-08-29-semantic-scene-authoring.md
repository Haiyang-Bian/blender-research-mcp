# Blender 4.2.23 semantic scene-authoring smoke

- Status: passed
- Date: 2026-08-29
- Branch: `codex/scene-authoring`
- Run ID: `20260829T145905Z-bcc21484`
- Blender: `4.2.23 LTS`
- Add-on and external MCP server: `0.8.0`
- Protocol: `1`
- Port: `9880`
- Elapsed time: `10172.16 ms`

The run began without an MCP session on the isolated port. Managed launch loaded the
current 0.8 add-on into a visible Blender process and advertised structural transactions
v3 plus all seven scene-authoring capability families. Heartbeat advanced from `3` to
`84` while the transaction, render, save, and reload operations ran.

## Deterministic structural transaction fixture

All `.blend`, texture, and render files were created under
`%TEMP%\blender-research-mcp-authoring\20260829T145905Z-bcc21484\`. The deterministic
fixture verified:

- a transaction-created Cube and its exclusive Mesh were removed by explicit rollback;
- a private, environment-gated test hook changed a created Cube after the writer returned;
  commit stopped with `STRUCTURE_CONFLICT` and preserved the injected location `x=0.25`;
- reloading the clean temporary project recovered from that intentionally conflicted
  transaction without force-overwriting the user-side value;
- closing the client while a newly created Ico Sphere transaction was active triggered
  disconnect rollback, and the object was absent after reconnect.

The test hook is not present in the MCP schema or capability list and is rejected unless
`BLENDER_RESEARCH_MCP_TEST_HOOKS=1` is supplied to that managed Blender process.

## Moonlit-water authoring result

One `author:moonlit-water` transaction recorded 15 deltas across object creation,
material creation and assignment, image loading, semantic bump binding, World setup,
and active-Camera selection. It created a water Grid, moon UV Sphere, two Cameras, an
Area light, and a Point light. The materials and World used the requested `#EFF0EA`,
`#C9DEE5`, and `#214268` palette. Deterministic 512×512 wave and 1024×512 star images
were loaded from absolute local paths; the wave image was bound through the supported
Generated/Mapping/Image Texture/Bump chain and the star image drove the World.

Both 512×512 Eevee Next previews were nonblank and visually inspected. Camera A frames
the full moon above a diagonal rippled water plane; Camera B moves the moon into a large
upper-left crop and changes the water perspective. Their maximum channel difference was
`255` and mean absolute difference was `32.0161705`, well above the harness's distinct-view
threshold. The preview evidence is:

- Camera A: `385149` bytes, SHA-256
  `79a1445da1c7ce5a819850415d7dc6cf0bacdc0ab50646f00b06ee840a525ddb`;
- Camera B: `382725` bytes, SHA-256
  `42f3f920524b31ab4f7d4ea54c5ede3e8adb0e928c69abc3a635b5bc9b54839b`.

The transaction committed with all guards intact. The gate then exported two PNG files
and one OPEN_EXR file. The EXR measured `667822` bytes with SHA-256
`33465fea283e79fab04692c7e8925a1a821ae91c5186f5db787fd996b5c17243`.

## Save, reload, and render persistence

The authored project saved as a `1392796`-byte `.blend` with SHA-256
`f31e6415f12546e93ba36411febbd9ffcf59906ae11410f35fbe29fff85305af`.
After `project.reload(save_current=false, use_scripts=false, load_ui=false)`, all six
authored object names remained present, their session identities were correctly renewed,
and Camera A rendered again at 256×256. The post-reload PNG was `104995` bytes with
SHA-256 `d4e0792bed9263d3756e6259d6820b3d463657891a4d62ff20575ac9779d820b`.

The live run exposed two integration defects that the final implementation corrects:
queued depsgraph notifications from an Agent-owned write are now flushed before its
response, and render validation no longer trusts the special Render Result image's
`[0, 0]` size in a timer-driven UI session. Eevee writes a controlled temporary image,
Blender reloads it to validate dimensions and variation, and a successful export then
atomically replaces the requested destination.

## File integrity and evidence

The untouched blank source fixture had SHA-256
`011f64db8b65d8276b42d25a906d8d998e457c9e5dfaae6249759858ae5f3b34` before and after
the run. The managed process was PID `3500`, instance
`cba9edf8-d16d-46d6-9f2a-2d386aa4f807`, launch ID
`45e2755a-61c6-4ea1-8758-0c865794c657`, and resource hash
`86eef35760f50caf681a2d1ebe5e654b5a4582188f7a2dfced9d55613f391d1e`.

Ignored evidence is under
`artifacts/live-smoke/20260829T145905Z-bcc21484/`. The report SHA-256 is
`74ffee45a669a49246ff6d4bc963176d82d01eecb59c1339e4a3574937a1c553` and the fixture
log SHA-256 is
`a65e19fb35d64c214ec9897c743ce81d89b01bed8567c9408b1b4147471eb286`.
The independent 0.6 comparative-preview real Blender smoke remains pending and is not
claimed as passed by this record.
