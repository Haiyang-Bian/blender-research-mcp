"""Create a tiny .blend whose registered Text records trusted script execution."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--token", required=True)
    script_args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    args = parser.parse_args(script_args)

    output = args.output.resolve()
    marker = args.marker.resolve()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    text = bpy.data.texts.new("blender_research_mcp_lifecycle_autorun.py")
    text.write(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text({args.token!r}, encoding='utf-8')\n"
    )
    text.use_module = True
    result = bpy.ops.wm.save_as_mainfile(
        filepath=str(output),
        check_existing=False,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"Could not create lifecycle autorun fixture: {result}")


main()
