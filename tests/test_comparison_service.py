import asyncio
import base64
import io
from typing import Any

import pytest
from PIL import Image

from blender_research_mcp.comparison import ComparisonRequest, run_lookdev_comparison
from blender_research_mcp.errors import BridgeError, ErrorInfo, ErrorKind


class FakeComparisonClient:
    def __init__(self) -> None:
        self.generation = 0
        self.heartbeat = 100
        self.context_marker = "stable"
        self.object_identity = "object-id"
        self.scale = [1.0, 1.0, 1.0]
        self.hide_render = False
        self.modifier_state = True
        self.shape_key_value = 0.0
        self.material_value: Any = 0.5
        self.material_kind = "FLOAT"
        self.material_users = 1
        self.active: dict[str, Any] | None = None
        self.rollback_no_restore = False
        self.commands: list[str] = []
        self.mutation_keys: list[str] = []

    async def call(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        deadline_ms: int = 5000,
        expected_scene_generation: int | None = None,
        idempotency_key: str | None = None,
        read_only: bool,
    ) -> dict[str, Any]:
        del deadline_ms
        params = params or {}
        self.commands.append(command)
        if not read_only:
            assert idempotency_key is not None
            self.mutation_keys.append(idempotency_key)
            assert expected_scene_generation == self.generation
        if command == "connection.ping":
            self.heartbeat += 1
            return {
                "scene_generation": self.generation,
                "heartbeat": self.heartbeat,
                "capability_versions": {},
            }
        if command == "context.get":
            return {
                "scene": "Scene",
                "mode": "OBJECT",
                "active_object": "mesh",
                "selected_objects": ["mesh"],
                "marker": self.context_marker,
                "scene_generation": self.generation,
            }
        if command == "object.inspect":
            return {
                "name": params["object_name"],
                "session_identity": self.object_identity,
                "scale": list(self.scale),
                "hide_viewport": False,
                "hide_render": self.hide_render,
                "visible": True,
                "scene_generation": self.generation,
            }
        if command == "object.lookdev.inspect":
            return {
                "name": params["object_name"],
                "session_identity": self.object_identity,
                "visibility": {
                    "hide_viewport": False,
                    "hide_render": self.hide_render,
                    "visible": True,
                },
                "modifiers": [
                    {
                        "name": "Armature",
                        "session_identity": "modifier-id",
                        "show_viewport": self.modifier_state,
                        "show_render": True,
                    }
                ],
                "shape_keys": [
                    {
                        "name": "Smile",
                        "session_identity": "shape-key-id",
                        "value": self.shape_key_value,
                        "slider_min": 0.0,
                        "slider_max": 1.0,
                        "driven": False,
                    }
                ],
                "scene_generation": self.generation,
            }
        if command == "material.inspect":
            return {
                "object_name": params["object_name"],
                "object_identity": self.object_identity,
                "material_slot_index": 0,
                "material_name": "Face",
                "material_identity": "material-id",
                "material_users": self.material_users,
                "affected_objects": ["mesh"],
                "sockets": [
                    {
                        "node_name": "Principled BSDF",
                        "node_identity": "node-id",
                        "socket_identifier": "Roughness",
                        "socket_identity": "socket-id",
                        "socket_kind": self.material_kind,
                        "value": self.material_value,
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "writable": True,
                        "blocked_reasons": [],
                    }
                ],
                "scene_generation": self.generation,
            }
        if command == "viewport.capture":
            image = self._image()
            return {
                "png_base64": base64.b64encode(image).decode("ascii"),
                "capture_scene_generation": self.generation,
                "capture_id": f"capture-{len(self.commands)}",
                "view": params["view"],
                "scene_generation": self.generation,
            }
        if command == "transaction.begin":
            assert self.active is None
            self.active = {"transaction_id": "tx", "label": params["label"]}
            return {
                "transaction_id": "tx",
                "status": "active",
                "scene_generation": self.generation,
            }
        if command in {
            "object.transform",
            "object.visibility.set",
            "modifier.set_state",
            "shape_key.set_value",
            "material.set_input",
        }:
            return self._write(command, params)
        if command == "transaction.rollback":
            return self._rollback()
        raise AssertionError(command)

    def _current_value(self, kind: str) -> Any:
        return {
            "scale": self.scale[2],
            "visibility": self.hide_render,
            "modifier": self.modifier_state,
            "shape_key": self.shape_key_value,
            "material": self.material_value,
        }[kind]

    def _set_value(self, kind: str, value: Any) -> None:
        if kind == "scale":
            self.scale[2] = value
        elif kind == "visibility":
            self.hide_render = value
        elif kind == "modifier":
            self.modifier_state = value
        elif kind == "shape_key":
            self.shape_key_value = value
        else:
            self.material_value = value

    def _write(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.active is not None
        if command == "object.transform":
            kind, value = "scale", params["scale"]["z"]
        elif command == "object.visibility.set":
            kind, value = "visibility", params["visibility"]["hide_render"]
        elif command == "modifier.set_state":
            kind, value = "modifier", params["state"]["show_viewport"]
        elif command == "shape_key.set_value":
            kind, value = "shape_key", params["value"]
        else:
            kind, value = "material", params["value"]
        before = self._current_value(kind)
        self._set_value(kind, value)
        self.active.update({"kind": kind, "before": before, "after": value})
        self.generation += 1
        return {
            "transaction_id": "tx",
            "before": before,
            "after": value,
            "scene_generation": self.generation,
        }

    def _rollback(self) -> dict[str, Any]:
        assert self.active is not None
        kind = str(self.active["kind"])
        if self._current_value(kind) != self.active["after"]:
            raise BridgeError(
                ErrorInfo(
                    kind=ErrorKind.CONFLICT,
                    code="PROPERTY_CONFLICT",
                    message="The property was changed outside the transaction",
                )
            )
        if not self.rollback_no_restore:
            self._set_value(kind, self.active["before"])
        self.active = None
        self.generation += 1
        return {
            "transaction_id": "tx",
            "status": "rolled_back",
            "context_restored": True,
            "scene_generation": self.generation,
        }

    def manual_shape_key_edit(self, value: float) -> None:
        self.shape_key_value = value
        self.generation += 1

    def _image(self) -> bytes:
        magnitude = (
            self.scale[2] * 11
            + int(self.hide_render) * 23
            + int(self.modifier_state) * 31
            + self.shape_key_value * 71
            + float(self.material_value) * 89
        )
        color = int(magnitude) % 180 + 20
        image = Image.new("RGB", (32, 16), (color, 30, 210 - color))
        image.putpixel((0, 0), (255, 255, 255))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


def target(target_type: str) -> dict[str, Any]:
    common = {
        "type": target_type,
        "object_name": "mesh",
        "expected_object_identity": "object-id",
    }
    if target_type == "object_scale_axis":
        return {**common, "axis": "z"}
    if target_type == "object_visibility":
        return {**common, "property": "hide_render"}
    if target_type == "modifier_state":
        return {
            **common,
            "modifier_name": "Armature",
            "expected_modifier_identity": "modifier-id",
            "property": "show_viewport",
        }
    if target_type == "shape_key_value":
        return {
            **common,
            "shape_key_name": "Smile",
            "expected_shape_key_identity": "shape-key-id",
        }
    return {
        **common,
        "material_slot_index": 0,
        "material_name": "Face",
        "expected_material_identity": "material-id",
        "expected_material_users": 1,
        "node_name": "Principled BSDF",
        "expected_node_identity": "node-id",
        "socket_identifier": "Roughness",
        "expected_socket_identity": "socket-id",
    }


def request(target_type: str, values: tuple[Any, ...]) -> ComparisonRequest:
    return ComparisonRequest.model_validate(
        {
            "target": target(target_type),
            "candidates": [
                {"label": chr(ord("A") + index), "value": value}
                for index, value in enumerate(values)
            ],
            "capture": {"object_name": "mesh", "view": "FRONT", "max_size": 512},
        }
    )


@pytest.mark.parametrize(
    ("target_type", "values", "writer"),
    [
        ("object_scale_axis", (1.1, 1.2), "object.transform"),
        ("object_visibility", (True,), "object.visibility.set"),
        ("modifier_state", (False,), "modifier.set_state"),
        ("shape_key_value", (0.1, 0.2), "shape_key.set_value"),
        ("material_input", (0.4, 0.6), "material.set_input"),
    ],
)
def test_comparison_routes_every_target_and_restores_each_candidate(
    target_type: str,
    values: tuple[Any, ...],
    writer: str,
) -> None:
    client = FakeComparisonClient()

    images, result = asyncio.run(run_lookdev_comparison(client, request(target_type, values)))

    assert len(images) == len(values) + 1
    assert [item["label"] for item in result["candidates"]] == [
        chr(ord("A") + index) for index in range(len(values))
    ]
    assert [item["content_index"] for item in result["candidates"]] == list(
        range(1, len(values) + 1)
    )
    assert [item["content_index"] for item in result["items"]] == list(
        range(len(values) + 1)
    )
    baseline = result["items"][0]
    assert baseline["label"] == "baseline"
    assert baseline["writer"] is None
    assert baseline["rollback"] is None
    assert baseline["difference"] == {
        "max_channel_difference": 0,
        "mean_absolute_difference": 0.0,
        "rms_difference": 0.0,
        "structure_mean_absolute_difference": 0.0,
    }
    assert baseline["elapsed_ms"] >= 0.0
    assert result["target_restored"] is True
    assert client.commands.count(writer) == len(values)
    assert client.commands.count("transaction.begin") == len(values)
    assert client.commands.count("transaction.rollback") == len(values)
    assert "transaction.commit" not in client.commands
    assert len(client.mutation_keys) == len(set(client.mutation_keys))


def test_repeating_the_same_comparison_is_safe_and_not_cached() -> None:
    client = FakeComparisonClient()
    comparison = request("shape_key_value", (0.1, 0.2))

    first = asyncio.run(run_lookdev_comparison(client, comparison))
    second = asyncio.run(run_lookdev_comparison(client, comparison))

    assert len(first[0]) == len(second[0]) == 3
    assert client.shape_key_value == 0.0
    assert client.commands.count("shape_key.set_value") == 4


def test_same_property_manual_edit_preserves_conflict_and_stops() -> None:
    client = FakeComparisonClient()

    async def edit_after_write(
        phase: str,
        label: str | None,
        details: dict[str, Any],
    ) -> None:
        del details
        if phase == "after_write" and label == "A":
            client.manual_shape_key_edit(0.7)

    with pytest.raises(BridgeError) as exc_info:
        asyncio.run(
            run_lookdev_comparison(
                client,
                request("shape_key_value", (0.1, 0.2)),
                _phase_hook=edit_after_write,
            )
        )

    assert exc_info.value.error.code == "PROPERTY_CONFLICT"
    assert exc_info.value.error.details["candidate_label"] == "A"
    assert exc_info.value.error.details["comparison_phase"] == "rollback"
    assert client.shape_key_value == 0.7
    assert client.commands.count("shape_key.set_value") == 1


def test_successful_rollback_without_restored_value_is_a_restore_failure() -> None:
    client = FakeComparisonClient()
    client.rollback_no_restore = True

    with pytest.raises(BridgeError) as exc_info:
        asyncio.run(run_lookdev_comparison(client, request("shape_key_value", (0.1,))))

    assert exc_info.value.error.code == "COMPARISON_RESTORE_FAILED"
    assert exc_info.value.error.details["candidate_label"] == "A"


@pytest.mark.parametrize("drift", ["context", "identity"])
def test_context_or_identity_drift_after_rollback_is_a_restore_failure(drift: str) -> None:
    client = FakeComparisonClient()

    async def drift_after_rollback(
        phase: str,
        label: str | None,
        details: dict[str, Any],
    ) -> None:
        del label, details
        if phase == "after_rollback":
            if drift == "context":
                client.context_marker = "user-edit"
            else:
                client.object_identity = "replacement"

    with pytest.raises(BridgeError) as exc_info:
        asyncio.run(
            run_lookdev_comparison(
                client,
                request("shape_key_value", (0.1,)),
                _phase_hook=drift_after_rollback,
            )
        )

    assert exc_info.value.error.code == "COMPARISON_RESTORE_FAILED"


def test_live_range_and_material_scope_are_rechecked_before_mutation() -> None:
    out_of_range = FakeComparisonClient()
    with pytest.raises(BridgeError) as exc_info:
        asyncio.run(
            run_lookdev_comparison(out_of_range, request("shape_key_value", (1.5,)))
        )
    assert exc_info.value.error.code == "CANDIDATE_OUT_OF_RANGE"
    assert "transaction.begin" not in out_of_range.commands

    shared = FakeComparisonClient()
    shared.material_users = 2
    shared_request = request("material_input", (0.4,))
    with pytest.raises(BridgeError) as exc_info:
        asyncio.run(run_lookdev_comparison(shared, shared_request))
    assert exc_info.value.error.code == "MATERIAL_USERS_CONFLICT"
    assert "transaction.begin" not in shared.commands
