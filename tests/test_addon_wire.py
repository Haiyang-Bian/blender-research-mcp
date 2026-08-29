import importlib.util
from pathlib import Path


def load_wire_module():
    path = (
        Path(__file__).parents[1]
        / "blender_addon"
        / "blender_research_mcp_addon"
        / "wire.py"
    )
    spec = importlib.util.spec_from_file_location("addon_wire", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_addon_wire_handles_fragmented_chinese_payload() -> None:
    wire = load_wire_module()
    frame = wire.encode_frame({"object_name": "目.L"})
    decoder = wire.FrameDecoder()
    assert decoder.feed(frame[:5]) == []
    assert decoder.feed(frame[5:]) == [{"object_name": "目.L"}]
