import io

import pytest
from PIL import Image
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.comparison import (
    ComparisonRequest,
    ComparisonTarget,
    image_difference_statistics,
    images_are_visually_indistinguishable,
    validate_evidence_image,
)


def target(target_type: str) -> dict[str, object]:
    common = {
        "type": target_type,
        "object_name": "绯雪_edit_mesh",
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
            "shape_key_name": "真面目",
            "expected_shape_key_identity": "shape-key-id",
        }
    if target_type == "material_input":
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
    raise AssertionError(target_type)


@pytest.mark.parametrize(
    "target_type",
    [
        "object_scale_axis",
        "object_visibility",
        "modifier_state",
        "shape_key_value",
        "material_input",
        "object_setting",
        "modifier_setting",
    ],
)
def test_comparison_target_is_a_closed_discriminated_union(target_type: str) -> None:
    parsed = TypeAdapter(ComparisonTarget).validate_python(target(target_type))
    assert parsed.type == target_type
    with pytest.raises(ValidationError):
        TypeAdapter(ComparisonTarget).validate_python({**target(target_type), "extra": True})


def request(
    target_type: str = "shape_key_value",
    values: tuple[object, ...] = (0.1, 0.2),
) -> dict[str, object]:
    return {
        "target": target(target_type),
        "candidates": [
            {"label": chr(ord("A") + index), "value": value}
            for index, value in enumerate(values)
        ],
        "capture": {"object_name": "绯雪_edit_mesh", "view": "FRONT"},
    }


def test_request_bounds_labels_values_and_capture() -> None:
    parsed = ComparisonRequest.model_validate(request())
    assert [candidate.label for candidate in parsed.candidates] == ["A", "B"]
    assert parsed.capture.max_size == 800

    duplicate_labels = request()
    duplicate_labels["candidates"][1]["label"] = "A"  # type: ignore[index]
    with pytest.raises(ValidationError, match="labels must be unique"):
        ComparisonRequest.model_validate(duplicate_labels)

    with pytest.raises(ValidationError, match="values must be unique"):
        ComparisonRequest.model_validate(request(values=(0.1, 0.10000001)))
    with pytest.raises(ValidationError):
        ComparisonRequest.model_validate(request(values=(float("nan"),)))
    with pytest.raises(ValidationError):
        ComparisonRequest.model_validate(
            {**request(), "capture": {"object_name": "mesh", "max_size": 1001}}
        )
    with pytest.raises(ValidationError, match="semantic base view"):
        ComparisonRequest.model_validate(
            {
                **request(),
                "capture": {
                    "object_name": "mesh",
                    "orbit": {"yaw_degrees": 30.0, "pitch_degrees": 10.0},
                },
            }
        )


def test_target_specific_candidate_types_are_strict() -> None:
    ComparisonRequest.model_validate(request("object_visibility", (True,)))
    with pytest.raises(ValidationError, match="exactly one"):
        ComparisonRequest.model_validate(request("object_visibility", (True, False)))
    with pytest.raises(ValidationError, match="floating-point"):
        ComparisonRequest.model_validate(request("shape_key_value", (1,)))
    with pytest.raises(ValidationError):
        ComparisonRequest.model_validate(request("material_input", ([0.1, 1, 0.3],)))
    ComparisonRequest.model_validate(request("object_setting", (35.0, 85.0)))
    with pytest.raises(ValidationError, match="floating-point"):
        ComparisonRequest.model_validate(request("object_setting", (35,)))
    ComparisonRequest.model_validate(request("modifier_setting", (0.2, 0.3)))
    integer_target = {**target("modifier_setting"), "property": "segments"}
    parsed = ComparisonRequest.model_validate(
        {**request(), "target": integer_target, "candidates": [{"label": "A", "value": 3}]}
    )
    assert parsed.candidates[0].value == 3
    with pytest.raises(ValidationError, match="JSON integers"):
        ComparisonRequest.model_validate(
            {
                **request(),
                "target": integer_target,
                "candidates": [{"label": "A", "value": 3.0}],
            }
        )


def test_modifier_setting_target_rejects_wrong_type_fields_and_candidate_kinds() -> None:
    with pytest.raises(ValidationError, match="not valid"):
        TypeAdapter(ComparisonTarget).validate_python(
            {**target("modifier_setting"), "property": "thickness"}
        )
    boolean_target = {**target("modifier_setting"), "property": "clamp_overlap"}
    ComparisonRequest.model_validate(
        {**request(), "target": boolean_target, "candidates": [{"label": "A", "value": False}]}
    )
    with pytest.raises(ValidationError, match="exactly one"):
        ComparisonRequest.model_validate(
            {
                **request(),
                "target": boolean_target,
                "candidates": [
                    {"label": "A", "value": False},
                    {"label": "B", "value": True},
                ],
            }
        )
    enum_target = {**target("modifier_setting"), "property": "width_mode"}
    ComparisonRequest.model_validate(
        {
            **request(),
            "target": enum_target,
            "candidates": [{"label": "A", "value": "WIDTH"}],
        }
    )


def test_object_setting_locators_validate_typed_candidates_and_color_equivalence() -> None:
    base = target("object_setting")
    transform = {
        **base,
        "locator": {"type": "transform", "channel": "rotation_euler_degrees", "axis": "y"},
    }
    visibility = {
        **base,
        "locator": {"type": "visibility", "property": "hide_render"},
    }
    light = {
        **base,
        "locator": {
            "type": "light",
            "expected_data_identity": "pointlight:1",
            "expected_data_users": 1,
            "expected_light_type": "POINT",
            "property": "color",
        },
    }
    ComparisonRequest.model_validate(
        {**request(), "target": transform, "candidates": [{"label": "A", "value": 45.0}]}
    )
    ComparisonRequest.model_validate(
        {**request(), "target": visibility, "candidates": [{"label": "A", "value": True}]}
    )
    ComparisonRequest.model_validate(
        {**request(), "target": light, "candidates": [{"label": "A", "value": "#C9DeE5"}]}
    )
    with pytest.raises(ValidationError, match="values must be unique"):
        ComparisonRequest.model_validate(
            {
                **request(),
                "target": light,
                "candidates": [
                    {"label": "A", "value": "#c9dee5"},
                    {"label": "B", "value": "#C9DEE5"},
                ],
            }
        )
    with pytest.raises(ValidationError):
        ComparisonRequest.model_validate(
            {**request(), "target": light, "candidates": [{"label": "A", "value": "blue"}]}
        )


def test_object_setting_locator_rejects_properties_for_the_wrong_data_type() -> None:
    base = target("object_setting")
    with pytest.raises(ValidationError):
        TypeAdapter(ComparisonTarget).validate_python(
            {
                **base,
                "locator": {
                    "type": "light",
                    "expected_data_identity": "sunlight:1",
                    "expected_data_users": 1,
                    "expected_light_type": "SUN",
                    "property": "radius",
                },
            }
        )
    with pytest.raises(ValidationError):
        TypeAdapter(ComparisonTarget).validate_python(
            {
                **base,
                "locator": {
                    "type": "camera",
                    "expected_data_identity": "camera:1",
                    "expected_data_users": 1,
                    "expected_camera_type": "ORTHO",
                    "property": "lens",
                },
            }
        )


def png(color: tuple[int, int, int, int], *, size: tuple[int, int] = (16, 8)) -> bytes:
    image = Image.new("RGBA", size, color)
    image.putpixel((0, 0), (255, 255, 255, color[3]))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_image_statistics_ignore_alpha_and_report_structure() -> None:
    baseline = png((10, 20, 30, 255))
    alpha_only = png((10, 20, 30, 0))
    changed = png((50, 60, 70, 255))

    identical_statistics = image_difference_statistics(baseline, alpha_only)
    assert identical_statistics.max_channel_difference == 0
    assert images_are_visually_indistinguishable(identical_statistics) is True

    changed_statistics = image_difference_statistics(baseline, changed)
    assert changed_statistics.mean_absolute_difference == 39.6875
    assert changed_statistics.structure_mean_absolute_difference > 0
    assert images_are_visually_indistinguishable(changed_statistics) is False


def test_image_evidence_rejects_invalid_blank_and_mismatched_images() -> None:
    with pytest.raises(ValueError, match="valid PNG"):
        validate_evidence_image(b"not-png", label="candidate")

    blank_output = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 0, 0)).save(blank_output, format="PNG")
    with pytest.raises(ValueError, match="blank"):
        validate_evidence_image(blank_output.getvalue(), label="candidate")

    with pytest.raises(ValueError, match="different dimensions"):
        image_difference_statistics(png((10, 20, 30, 255)), png((10, 20, 30, 255), size=(8, 8)))
