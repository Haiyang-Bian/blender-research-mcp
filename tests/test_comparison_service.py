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
        self.mode = "OBJECT"
        self.object_identity = "object-id"
        self.scale = [1.0, 1.0, 1.0]
        self.hide_render = False
        self.modifier_state = True
        self.shape_key_value = 0.0
        self.material_value: Any = 0.5
        self.material_kind = "FLOAT"
        self.material_users = 1
        self.camera_lens = 50.0
        self.modifier_stack_fingerprint = "f" * 64
        self.modifier_settings: dict[str, Any] = {
            "width": 0.1,
            "segments": 2,
            "limit_method": "ANGLE",
            "angle_limit_degrees": 30.0,
            "affect": "EDGES",
            "width_mode": "OFFSET",
            "profile": 0.5,
            "clamp_overlap": True,
            "harden_normals": False,
        }
        self.active: dict[str, Any] | None = None
        self.accepted_user_save = False
        self.user_intent_revision = 0
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
                "user_intent_revision": self.user_intent_revision,
                "last_user_action": (
                    {"kind": "native_save", "status": "succeeded"}
                    if self.accepted_user_save
                    else None
                ),
            }
        if command == "context.get":
            return {
                "scene": "Scene",
                "mode": self.mode,
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
                "location": [0.0, 0.0, 0.0],
                "rotation_euler_degrees": [0.0, 0.0, 0.0],
                "hide_viewport": False,
                "hide_render": self.hide_render,
                "visibility": {
                    "hide_viewport": False,
                    "hide_render": self.hide_render,
                },
                "data": {
                    "name": "Camera Data",
                    "type": "camera",
                    "session_identity": "camera-id",
                    "users": 1,
                    "shared": False,
                    "library": None,
                    "writable": True,
                    "settings": {
                        "camera_type": "PERSP",
                        "lens": self.camera_lens,
                        "sensor_width": 36.0,
                        "clip_start": 0.1,
                        "clip_end": 1000.0,
                        "shift_x": 0.0,
                        "shift_y": 0.0,
                    },
                    "writable_fields": {
                        "lens": {"minimum": 1.0, "maximum": 250.0},
                    },
                },
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
        if command == "modifier.inspect":
            return {
                "object_name": params["object_name"],
                "object_identity": self.object_identity,
                "scene_generation": self.generation,
                "base_faces": 12,
                "stack_fingerprint": self.modifier_stack_fingerprint,
                "modifiers": [
                    {
                        "name": "Soft Edges",
                        "session_identity": "modifier-bevel-id",
                        "stack_index": 0,
                        "type": "BEVEL",
                        "supported": True,
                        "pending_delete": False,
                        "show_viewport": True,
                        "show_render": True,
                        "settings": dict(self.modifier_settings),
                        "writable_fields": [
                            "show_viewport",
                            "show_render",
                            *self.modifier_settings,
                        ],
                        "ranges": {
                            "width": [0.0, 100_000.0],
                            "segments": [1, 64],
                            "limit_method": ["NONE", "ANGLE"],
                            "angle_limit_degrees": [0.0, 180.0],
                            "affect": ["EDGES", "VERTICES"],
                            "width_mode": ["OFFSET", "WIDTH", "DEPTH", "ABSOLUTE"],
                            "profile": [0.0, 1.0],
                        },
                    }
                ],
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
            if self.active is not None:
                assert params["_view_reference_capture_id"] is not None
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
            "object.set",
            "modifier.set",
        }:
            return self._write(command, params)
        if command == "transaction.rollback":
            if self.accepted_user_save:
                raise BridgeError(
                    ErrorInfo(
                        kind=ErrorKind.CONFLICT,
                        code="TRANSACTION_ACCEPTED_BY_USER_SAVE",
                        message="The user saved the current state",
                    )
                )
            return self._rollback()
        raise AssertionError(command)

    def _current_value(self, kind: str) -> Any:
        if kind.startswith("typed_modifier:"):
            return self.modifier_settings[kind.split(":", 1)[1]]
        return {
            "scale": self.scale[2],
            "visibility": self.hide_render,
            "modifier": self.modifier_state,
            "shape_key": self.shape_key_value,
            "material": self.material_value,
            "camera": self.camera_lens,
        }[kind]

    def _set_value(self, kind: str, value: Any) -> None:
        if kind.startswith("typed_modifier:"):
            self.modifier_settings[kind.split(":", 1)[1]] = value
        elif kind == "scale":
            self.scale[2] = value
        elif kind == "visibility":
            self.hide_render = value
        elif kind == "modifier":
            self.modifier_state = value
        elif kind == "shape_key":
            self.shape_key_value = value
        elif kind == "camera":
            self.camera_lens = value
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
        elif command == "object.set":
            patch = params["patches"][0]
            if patch["type"] == "transform":
                kind, value = "scale", patch["scale"]["z"]
            elif patch["type"] == "visibility":
                kind, value = "visibility", patch["hide_render"]
            else:
                kind, value = "camera", patch["lens"]
        elif command == "modifier.set":
            settings = params["settings"]
            property_name = next(key for key in settings if key != "type")
            kind, value = f"typed_modifier:{property_name}", settings[property_name]
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

    def native_save(self) -> None:
        self.active = None
        self.accepted_user_save = True
        self.user_intent_revision += 1

    def _image(self) -> bytes:
        modifier_magnitude = sum(
            ord(character) for character in repr(sorted(self.modifier_settings.items()))
        )
        magnitude = (
            self.scale[2] * 11
            + int(self.hide_render) * 23
            + int(self.modifier_state) * 31
            + self.shape_key_value * 71
            + float(self.material_value) * 89
            + self.camera_lens * 3
            + modifier_magnitude
        )
        color = int(magnitude) % 180 + 20
        image = Image.new("RGB", (32, 16), (color, 30, 210 - color))
        image.putpixel((0, 0), (255, 255, 255))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


class FakeLightComparisonClient(FakeComparisonClient):
    def __init__(self, property_name: str, *, users: int = 1) -> None:
        super().__init__()
        self.property_name = property_name
        self.light_type = "AREA" if property_name in {"shape", "size", "size_y"} else "POINT"
        self.light_users = users
        self.light_values: dict[str, Any] = {
            "energy": 1000.0,
            "color": "#FFFFFF",
            "radius": 0.25,
            "shape": "RECTANGLE",
            "size": 2.0,
            "size_y": 1.0,
        }

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
        if command != "object.inspect":
            return await super().call(
                command,
                params,
                deadline_ms=deadline_ms,
                expected_scene_generation=expected_scene_generation,
                idempotency_key=idempotency_key,
                read_only=read_only,
            )
        params = params or {}
        self.commands.append(command)
        writable_fields: dict[str, dict[str, Any]] = {
            "energy": {"minimum": 0.0, "maximum": 10_000_000.0},
            "color": {"format": "#RRGGBB_sRGB"},
            "radius": {"minimum": 0.0, "maximum": 100_000.0},
            "shape": {"enum": ["SQUARE", "RECTANGLE", "DISK", "ELLIPSE"]},
            "size": {"minimum": 0.000001, "maximum": 100_000.0},
            "size_y": {"minimum": 0.000001, "maximum": 100_000.0},
        }
        return {
            "name": params["object_name"],
            "session_identity": self.object_identity,
            "scale": list(self.scale),
            "location": [0.0, 0.0, 0.0],
            "rotation_euler_degrees": [0.0, 0.0, 0.0],
            "visibility": {"hide_viewport": False, "hide_render": self.hide_render},
            "data": {
                "name": "Light Data",
                "type": "light",
                "session_identity": "light-id",
                "users": self.light_users,
                "shared": self.light_users > 1,
                "library": None,
                "writable": True,
                "settings": {"light_type": self.light_type, **self.light_values},
                "writable_fields": writable_fields,
            },
            "scene_generation": self.generation,
        }

    def _current_value(self, kind: str) -> Any:
        if kind.startswith("light_"):
            return self.light_values[kind.removeprefix("light_")]
        return super()._current_value(kind)

    def _set_value(self, kind: str, value: Any) -> None:
        if kind.startswith("light_"):
            self.light_values[kind.removeprefix("light_")] = value
        else:
            super()._set_value(kind, value)

    def _write(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        if command != "object.set":
            return super()._write(command, params)
        assert self.active is not None
        patch = params["patches"][0]
        property_name = next(
            key
            for key in patch
            if key
            not in {
                "type",
                "expected_data_identity",
                "expected_data_users",
                "expected_light_type",
                "allow_shared_data",
            }
        )
        kind = f"light_{property_name}"
        value = patch[property_name]
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

    def _image(self) -> bytes:
        current = self.light_values[self.property_name]
        magnitude = (
            int(current[1:3], 16)
            if isinstance(current, str) and current.startswith("#")
            else sum(ord(character) for character in str(current)) % 180
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
    if target_type == "object_setting":
        return {
            **common,
            "locator": {
                "type": "camera",
                "expected_data_identity": "camera-id",
                "expected_data_users": 1,
                "expected_camera_type": "PERSP",
                "property": "lens",
            },
        }
    if target_type == "modifier_setting":
        return {
            **common,
            "modifier_name": "Soft Edges",
            "expected_modifier_identity": "modifier-bevel-id",
            "expected_modifier_type": "BEVEL",
            "expected_stack_index": 0,
            "expected_stack_fingerprint": "f" * 64,
            "property": "width",
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


def object_setting_request(
    locator: dict[str, Any],
    values: tuple[Any, ...],
) -> ComparisonRequest:
    return ComparisonRequest.model_validate(
        {
            "target": {
                "type": "object_setting",
                "object_name": "mesh",
                "expected_object_identity": "object-id",
                "locator": locator,
            },
            "candidates": [
                {"label": chr(ord("A") + index), "value": value}
                for index, value in enumerate(values)
            ],
            "capture": {"object_name": "mesh", "view": "FRONT", "max_size": 512},
        }
    )


def modifier_setting_request(
    property_name: str,
    values: tuple[Any, ...],
) -> ComparisonRequest:
    return ComparisonRequest.model_validate(
        {
            "target": {
                **target("modifier_setting"),
                "property": property_name,
            },
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
        ("object_setting", (35.0, 85.0), "object.set"),
        ("modifier_setting", (0.2, 0.3), "modifier.set"),
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


@pytest.mark.parametrize(
    ("locator", "values", "kind"),
    [
        ({"type": "transform", "channel": "scale", "axis": "z"}, (1.2, 1.4), "scale"),
        ({"type": "visibility", "property": "hide_render"}, (True,), "visibility"),
    ],
)
def test_object_setting_comparison_routes_transform_and_visibility_locators(
    locator: dict[str, Any],
    values: tuple[Any, ...],
    kind: str,
) -> None:
    client = FakeComparisonClient()
    baseline = client._current_value(kind)

    images, result = asyncio.run(
        run_lookdev_comparison(client, object_setting_request(locator, values))
    )

    assert len(images) == len(values) + 1
    assert client._current_value(kind) == baseline
    assert client.commands.count("object.set") == len(values)
    assert result["target_restored"] is True


@pytest.mark.parametrize(
    ("property_name", "values"),
    [
        ("energy", (500.0, 1500.0)),
        ("color", ("#C9DeE5", "#214268")),
        ("size", (1.5, 3.0)),
        ("shape", ("SQUARE", "ELLIPSE")),
    ],
)
def test_object_setting_comparison_routes_typed_light_values(
    property_name: str,
    values: tuple[Any, ...],
) -> None:
    client = FakeLightComparisonClient(property_name)
    locator = {
        "type": "light",
        "expected_data_identity": "light-id",
        "expected_data_users": 1,
        "expected_light_type": client.light_type,
        "property": property_name,
    }
    baseline = client.light_values[property_name]

    _images, result = asyncio.run(
        run_lookdev_comparison(client, object_setting_request(locator, values))
    )

    assert client.light_values[property_name] == baseline
    assert [item["requested_value"] for item in result["candidates"]] == list(values)
    assert result["target_restored"] is True


@pytest.mark.parametrize(
    ("property_name", "values"),
    [
        ("width", (0.2, 0.3)),
        ("segments", (3, 4)),
        ("clamp_overlap", (False,)),
        ("width_mode", ("WIDTH", "DEPTH")),
    ],
)
def test_modifier_setting_comparison_routes_typed_values_and_restores(
    property_name: str,
    values: tuple[Any, ...],
) -> None:
    client = FakeComparisonClient()
    baseline = client.modifier_settings[property_name]

    images, result = asyncio.run(
        run_lookdev_comparison(client, modifier_setting_request(property_name, values))
    )

    assert len(images) == len(values) + 1
    assert client.modifier_settings[property_name] == baseline
    assert client.commands.count("modifier.set") == len(values)
    assert [item["requested_value"] for item in result["candidates"]] == list(values)
    assert result["target_restored"] is True


def test_modifier_setting_comparison_rechecks_enum_and_stack_fingerprint() -> None:
    invalid = FakeComparisonClient()
    with pytest.raises(BridgeError) as enum_error:
        asyncio.run(
            run_lookdev_comparison(
                invalid,
                modifier_setting_request("width_mode", ("NOT_A_MODE",)),
            )
        )
    assert enum_error.value.error.code == "CANDIDATE_OUT_OF_RANGE"
    assert "transaction.begin" not in invalid.commands

    drifted = FakeComparisonClient()
    drifted.modifier_stack_fingerprint = "0" * 64
    with pytest.raises(BridgeError) as drift_error:
        asyncio.run(
            run_lookdev_comparison(
                drifted,
                modifier_setting_request("width", (0.2,)),
            )
        )
    assert drift_error.value.error.code == "MODIFIER_STACK_CONFLICT"


def test_object_setting_comparison_normalizes_color_and_requires_shared_authorization() -> None:
    client = FakeLightComparisonClient("color")
    locator = {
        "type": "light",
        "expected_data_identity": "light-id",
        "expected_data_users": 1,
        "expected_light_type": "POINT",
        "property": "color",
    }
    with pytest.raises(BridgeError) as same_color:
        asyncio.run(
            run_lookdev_comparison(
                client,
                object_setting_request(locator, ("#ffffff",)),
            )
        )
    assert same_color.value.error.code == "CANDIDATE_EQUALS_BASELINE"

    shared_client = FakeLightComparisonClient("energy", users=2)
    shared_locator = {
        **locator,
        "expected_data_users": 2,
        "property": "energy",
    }
    with pytest.raises(BridgeError) as shared:
        asyncio.run(
            run_lookdev_comparison(
                shared_client,
                object_setting_request(shared_locator, (500.0,)),
            )
        )
    assert shared.value.error.code == "SHARED_OBJECT_DATA_CONFIRMATION_REQUIRED"


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
                client.mode = "EDIT_MESH"
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


def test_selection_or_view_drift_does_not_invalidate_comparison() -> None:
    client = FakeComparisonClient()

    async def drift_after_rollback(
        phase: str,
        label: str | None,
        details: dict[str, Any],
    ) -> None:
        del label, details
        if phase == "after_rollback":
            client.context_marker = "user-navigation"

    images, result = asyncio.run(
        run_lookdev_comparison(
            client,
            request("shape_key_value", (0.1,)),
            _phase_hook=drift_after_rollback,
        )
    )

    assert len(images) == 2
    assert result["target_restored"] is True


def test_native_save_accepts_current_candidate_and_stops_without_cleanup_rollback() -> None:
    client = FakeComparisonClient()

    async def save_after_write(
        phase: str,
        label: str | None,
        details: dict[str, Any],
    ) -> None:
        del details
        if phase == "after_write" and label == "A":
            client.native_save()

    with pytest.raises(BridgeError) as accepted:
        asyncio.run(
            run_lookdev_comparison(
                client,
                request("shape_key_value", (0.1, 0.2)),
                _phase_hook=save_after_write,
            )
        )

    assert accepted.value.error.code == "COMPARISON_ACCEPTED_BY_USER_SAVE"
    assert accepted.value.error.details["candidate_label"] == "A"
    assert client.shape_key_value == 0.1
    assert client.commands.count("transaction.rollback") == 0
    assert client.commands.count("shape_key.set_value") == 1


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
