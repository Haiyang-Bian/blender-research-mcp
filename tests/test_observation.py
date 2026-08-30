import asyncio
import base64
import io

import pytest
from PIL import Image

from blender_research_mcp.errors import BridgeError
from blender_research_mcp.observation import (
    collect_observation_bundle,
    settle_capture_generation,
    settle_scene_generation,
)


def png_bytes(color: tuple[int, int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (32, 16), color).save(output, format="PNG")
    return output.getvalue()


class FakeClient:
    def __init__(self, generations: list[int] | None = None) -> None:
        self.generations = iter(generations or [7, 7, 7, 7])
        self.heartbeat = 10
        self.context_calls = 0
        self.object_calls = 0

    async def call(self, command, params=None, **_kwargs):
        if command == "connection.ping":
            self.heartbeat += 1
            return {
                "scene_generation": next(self.generations),
                "heartbeat": self.heartbeat,
            }
        if command == "context.get":
            self.context_calls += 1
            return {
                "scene": "Scene",
                "mode": "OBJECT",
                "active_object": "目.L",
                "selected_objects": ["目.L"],
                "view": {"distance": 2.0},
                "scene_generation": 7,
            }
        if command == "object.inspect":
            self.object_calls += 1
            return {
                "name": "目.L",
                "scale": [1.0, 1.0, 1.0],
                "selected": True,
                "scene_generation": 7,
            }
        if command == "viewport.capture":
            view = params["view"]
            raw = png_bytes((20 + len(view), 40, 60, 255))
            return {
                "object_name": params["object_name"],
                "view": view,
                "capture_id": f"capture-{view}",
                "capture_scene_generation": 7,
                "viewport_id": "1:2",
                "backend": "gpu_offscreen",
                "focus_requirement": "none_when_window_exists",
                "display_mode": params["display_mode"],
                "overlays": params["overlays"],
                "context_unchanged": True,
                "png_base64": base64.b64encode(raw).decode("ascii"),
                "scene_generation": 7,
            }
        raise AssertionError(command)


def test_settle_scene_generation_requires_two_equal_reads() -> None:
    async def scenario():
        client = FakeClient([4, 5, 5])
        return await settle_scene_generation(client, timeout_seconds=1, poll_seconds=0)

    result = asyncio.run(scenario())

    assert result["scene_generation"] == 5
    assert result["heartbeat"] == 13


def test_settle_scene_generation_reports_unstable_scene() -> None:
    async def scenario():
        client = FakeClient([4])
        return await settle_scene_generation(client, timeout_seconds=0, poll_seconds=0)

    with pytest.raises(BridgeError) as unstable:
        asyncio.run(scenario())
    assert unstable.value.error.code == "SCENE_UNSTABLE"
    assert unstable.value.error.retryable is True


def test_single_capture_rejects_a_newer_settled_generation() -> None:
    async def scenario():
        return await settle_capture_generation(
            FakeClient([8, 8]),
            {"capture_scene_generation": 7},
        )

    with pytest.raises(BridgeError) as changed:
        asyncio.run(scenario())
    assert changed.value.error.code == "OBSERVATION_SCENE_CHANGED"
    assert changed.value.error.details == {"before": 7, "after": 8}


def test_bundle_returns_ordered_images_and_consistent_evidence() -> None:
    async def scenario():
        return await collect_observation_bundle(
            FakeClient(),
            object_name="目.L",
            views=("FRONT", "RIGHT"),
            max_size=800,
            viewport_id=None,
        )

    images, result = asyncio.run(scenario())

    assert len(images) == 2
    assert [capture["view"] for capture in result["captures"]] == ["FRONT", "RIGHT"]
    assert [capture["content_index"] for capture in result["captures"]] == [0, 1]
    assert [capture["capture_id"] for capture in result["captures"]] == [
        "capture-FRONT",
        "capture-RIGHT",
    ]
    assert result["context_unchanged"] is True
    assert result["object_unchanged"] is True
    assert result["scene_generation"] == 7
    assert result["warnings"] == [
        {"code": "DUPLICATE_VIEW_HASHES", "views": [["FRONT", "RIGHT"]]}
    ]


def test_bundle_rejects_duplicate_views_before_capture() -> None:
    async def scenario():
        return await collect_observation_bundle(
            FakeClient(),
            object_name="目.L",
            views=("FRONT", "FRONT"),
            max_size=800,
            viewport_id=None,
        )

    with pytest.raises(BridgeError) as duplicate:
        asyncio.run(scenario())
    assert duplicate.value.error.code == "VIEWS_DUPLICATE"


def test_bundle_preserves_selection_drift() -> None:
    class DriftingClient(FakeClient):
        async def call(self, command, params=None, **kwargs):
            result = await super().call(command, params, **kwargs)
            if command == "context.get" and self.context_calls == 2:
                result["active_object"] = "用户对象"
            return result

    async def scenario():
        return await collect_observation_bundle(
            DriftingClient(),
            object_name="目.L",
            views=("FRONT",),
            max_size=800,
            viewport_id=None,
        )

    _images, result = asyncio.run(scenario())

    assert result["context_unchanged"] is True
    assert result["user_ui_preserved"] is True
    assert result["preserved_ui_changes"] == ["active_object"]


def test_bundle_rejects_write_relevant_context_drift() -> None:
    class DriftingClient(FakeClient):
        async def call(self, command, params=None, **kwargs):
            result = await super().call(command, params, **kwargs)
            if command == "context.get" and self.context_calls == 2:
                result["mode"] = "EDIT_MESH"
            return result

    async def scenario():
        return await collect_observation_bundle(
            DriftingClient(),
            object_name="目.L",
            views=("FRONT",),
            max_size=800,
            viewport_id=None,
        )

    with pytest.raises(BridgeError) as drift:
        asyncio.run(scenario())
    assert drift.value.error.code == "OBSERVATION_CONTEXT_DRIFT"
    assert drift.value.error.details == {"changed_fields": ["mode"]}


def test_bundle_rejects_scene_generation_change() -> None:
    async def scenario():
        return await collect_observation_bundle(
            FakeClient([7, 7, 8, 8]),
            object_name="目.L",
            views=("FRONT",),
            max_size=800,
            viewport_id=None,
        )

    with pytest.raises(BridgeError) as changed:
        asyncio.run(scenario())
    assert changed.value.error.code == "OBSERVATION_SCENE_CHANGED"
    assert changed.value.error.details == {"before": 7, "after": 8}


def test_bundle_rejects_object_drift() -> None:
    class DriftingClient(FakeClient):
        async def call(self, command, params=None, **kwargs):
            result = await super().call(command, params, **kwargs)
            if command == "object.inspect" and self.object_calls == 2:
                result["scale"] = [1.0, 1.0, 1.1]
            return result

    async def scenario():
        return await collect_observation_bundle(
            DriftingClient(),
            object_name="目.L",
            views=("FRONT",),
            max_size=800,
            viewport_id=None,
        )

    with pytest.raises(BridgeError) as changed:
        asyncio.run(scenario())
    assert changed.value.error.code == "OBSERVATION_SCENE_CHANGED"
    assert changed.value.error.details == {"changed_fields": ["scale"]}
