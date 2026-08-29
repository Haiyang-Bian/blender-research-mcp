import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.object_settings import ObjectSettingPatches

ADAPTER = TypeAdapter(ObjectSettingPatches)


def test_object_settings_accept_ordered_typed_patches() -> None:
    patches = ADAPTER.validate_python(
        [
            {
                "type": "camera",
                "expected_data_identity": "camera:1",
                "expected_data_users": 1,
                "expected_camera_type": "PERSP",
                "lens": 85,
                "clip_start": 0.1,
                "clip_end": 1000,
            },
            {
                "type": "transform",
                "location": {"z": 4},
                "rotation_euler_degrees": {"x": 90},
            },
            {"type": "visibility", "hide_render": True},
        ]
    )

    assert [patch.type for patch in patches] == ["camera", "transform", "visibility"]
    assert patches[0].model_dump(exclude_none=True)["lens"] == 85.0


@pytest.mark.parametrize(
    "patches",
    [
        [],
        [{"type": "transform"}],
        [{"type": "visibility"}],
        [
            {"type": "transform", "scale": {"x": 1}},
            {"type": "transform", "location": {"x": 2}},
        ],
        [{"type": "transform", "scale": {"x": True}}],
        [{"type": "transform", "location": {"x": float("inf")}}],
        [{"type": "visibility", "hide_render": 1}],
    ],
)
def test_object_settings_reject_empty_duplicate_and_non_strict_values(
    patches: list[dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(patches)


@pytest.mark.parametrize(
    "patch",
    [
        {
            "type": "light",
            "expected_data_identity": "light:1",
            "expected_data_users": 1,
            "expected_light_type": "SUN",
            "radius": 1,
        },
        {
            "type": "light",
            "expected_data_identity": "light:1",
            "expected_data_users": 1,
            "expected_light_type": "AREA",
            "shape": "SQUARE",
            "size_y": 2,
        },
        {
            "type": "light",
            "expected_data_identity": "light:1",
            "expected_data_users": 1,
            "expected_light_type": "POINT",
            "color": "white",
        },
        {
            "type": "camera",
            "expected_data_identity": "camera:1",
            "expected_data_users": 1,
            "expected_camera_type": "PERSP",
            "ortho_scale": 2,
        },
        {
            "type": "camera",
            "expected_data_identity": "camera:1",
            "expected_data_users": 1,
            "expected_camera_type": "ORTHO",
            "lens": 50,
        },
        {
            "type": "camera",
            "expected_data_identity": "camera:1",
            "expected_data_users": 1,
            "expected_camera_type": "PERSP",
            "clip_start": 10,
            "clip_end": 1,
        },
    ],
)
def test_object_settings_enforce_light_and_camera_type_constraints(
    patch: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python([patch])


def test_object_settings_accept_all_supported_light_shapes_and_color() -> None:
    for shape in ("SQUARE", "RECTANGLE", "DISK", "ELLIPSE"):
        payload: dict[str, object] = {
            "type": "light",
            "expected_data_identity": "light:1",
            "expected_data_users": 2,
            "expected_light_type": "AREA",
            "allow_shared_data": True,
            "shape": shape,
            "color": "#C9DeE5",
            "size": 4,
        }
        if shape in {"RECTANGLE", "ELLIPSE"}:
            payload["size_y"] = 2
        patch = ADAPTER.validate_python([payload])[0]
        assert patch.type == "light"


def test_object_settings_limit_request_to_four_distinct_patch_types() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            [
                {"type": "transform", "scale": {"x": 1}},
                {"type": "visibility", "hide_render": False},
                {
                    "type": "light",
                    "expected_data_identity": "light:1",
                    "expected_data_users": 1,
                    "expected_light_type": "POINT",
                    "energy": 10,
                },
                {
                    "type": "camera",
                    "expected_data_identity": "camera:1",
                    "expected_data_users": 1,
                    "expected_camera_type": "PERSP",
                    "lens": 50,
                },
                {"type": "visibility", "hide_viewport": False},
            ]
        )
