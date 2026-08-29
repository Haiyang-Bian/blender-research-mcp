# Blender 4.2.23 managed application and project lifecycle smoke

- Status: passed
- Date: 2026-08-29
- Branch: `codex/managed-blender-lifecycle`
- Run ID: `20260829T133346Z-3e8a7342`
- Blender: `4.2.23 LTS` (`d0cbe84903e8`)
- Add-on and external MCP server: `0.7.0`
- Protocol: `1`
- Port: `9877`
- Elapsed time: `9469.079 ms`

The run began without an advertised MCP Blender session. A session-only bootstrap
loaded the packaged 0.7 add-on, suppressed Blender's modal startup project chooser
for that process, authenticated with the new manifest, and exposed
`project_lifecycle: 1` and `application_lifecycle: 1`. Repeating
`application.launch` reused the same PID and instance.

## Executable and launch boundary

The installed Microsoft Store alias starts Blender but does not preserve the
environment and `--python` bootstrap arguments needed to associate a managed
session. Direct execution from the protected WindowsApps location also returned
`WinError 5`. For this gate, the installed 4.2.23 application directory was copied
read-only to
`%TEMP%\blender-research-mcp-portable\4.2.23-live-20260829\` and its real
`blender.exe` was used. The copied binary reported the same official 4.2.23 LTS
build and did not require an add-on installation or persistent preference change.

The first managed process was PID `37428`, instance
`0b1020ad-facc-4b24-a90d-605b854dde09`, launch ID
`06fcfed0-a0cf-401d-84a5-4f5847ff7c35`. The materialized managed-resource hash was
`864d6974db3e63a97ddc131ac4297a3a1abe968a863c6c17e78f91701b53239d`.
Heartbeat advanced from `4` to `45` while lifecycle operations ran.

## Project operations

All writable files were created under
`%TEMP%\blender-research-mcp-lifecycle\20260829T133346Z-3e8a7342\`.
The gate verified:

- saving the initial untitled project with Save As;
- committing one object-scale transaction, forcing a save of its semantic delta,
  opening the requested project on the next tick, reconnecting, and verifying the
  absolute target path;
- ordinary save, overwrite-capable Save As, and `already_open` for the current file;
- default reload discarding an unsaved transaction delta;
- `reload(save_current=true)` committing, saving, and retaining that delta;
- `load_ui/use_scripts=false` and their default `true` values reaching successful
  project opens;
- default `application.quit` completing after the clean current project check;
- a second cold launch on PID `16476` with launch ID
  `d589c829-31de-4b10-91ac-43a68c8fb11b`, followed by
  `application.quit(save_current=false)`.

The live run exposed that direct Blender RNA writes can leave `bpy.data.is_dirty`
false. The final implementation therefore treats a committed transaction with one
or more deltas as requiring a save even when Blender's dirty flag is clear. It also
uses the managed `Popen.poll()` result for its own process because a terminated
Windows process can remain discoverable while the parent holds its process handle.

## Trusted project-script evidence

The harness created a small temporary `.blend` containing a registered Text script
whose only effect was writing a run-specific marker. Opening it with
`use_scripts=false, load_ui=false` did not create the marker. After switching away,
opening it with the default `use_scripts=true, load_ui=true` created the marker with
the exact token `trusted-20260829T133346Z-3e8a7342`. This verifies behavior rather
than only schema or parameter propagation. The fixture builder is test-only and
does not add an arbitrary-Python MCP capability.

## File integrity and evidence

The source fixture remained
`C:\Users\26687\Work\projects\blender-projects\test-model.blend`. Its SHA-256 was
`255e6c0a1730e80f2a57dc870dd51bbe45ea210546784f8a7af71b88d6014da3`
before and after the run. Only copies were opened and modified.

Ignored evidence is under
`artifacts/live-smoke/20260829T133346Z-3e8a7342/`. The report SHA-256 is
`a4a3eb2dc38e428261779fe049953ec54a4108c56b08089ea8d4d1da3838b557`;
the fixture-build log SHA-256 is
`18caacb10c624afdcc88a3e71594195503bbfd2bf051080146a710fc3c5fff2f`.
The separate 0.6 comparative-preview live smoke remains pending and is not claimed
as passed by this record.
