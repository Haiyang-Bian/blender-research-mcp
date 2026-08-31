from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.mesh_attributes import UVOperation

ADAPTER = TypeAdapter(UVOperation)
LAYER = {"layer_name": "UVMap", "expected_layer_identity": "uv:1"}
CORNER = {"loop_index": 0, "face_index": 0, "corner_index": 0, "vertex_index": 0}


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "layer_create", "layer_name": "UV2", "source": "EMPTY"},
        {"type": "layer_delete", "layer": LAYER},
        {"type": "layer_roles", "layer": LAYER, "render": True},
        {"type": "seam_set", "selection_id": "selection", "seam": True},
        {
            "type": "coordinate_set",
            "layer": LAYER,
            "mode": "ABSOLUTE",
            "corners": [{**CORNER, "uv": [0.25, 0.75]}],
        },
        {
            "type": "transform",
            "layer": LAYER,
            "selection_id": "selection",
            "translation": [1.0, 0.0],
        },
        {"type": "pin_set", "layer": LAYER, "corners": [CORNER], "pinned": True},
        {
            "type": "unwrap",
            "layer": LAYER,
            "selection_id": "selection",
            "method": "ANGLE_BASED",
        },
        {
            "type": "pack",
            "layer": LAYER,
            "selection_id": "selection",
            "tile_u": 1,
            "tile_v": 0,
        },
    ],
)
def test_uv_operations_are_closed_and_typed(operation: dict[str, object]) -> None:
    parsed = ADAPTER.validate_python(operation)
    assert parsed.type == operation["type"]


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "layer_create", "layer_name": "UV2", "source": "LAYER"},
        {"type": "layer_roles", "layer": LAYER},
        {
            "type": "coordinate_set",
            "layer": LAYER,
            "corners": [
                {**CORNER, "uv": [0.0, 0.0]},
                {**CORNER, "uv": [1.0, 1.0]},
            ],
        },
        {
            "type": "transform",
            "layer": LAYER,
            "selection_id": "selection",
        },
        {"type": "pin_set", "layer": LAYER, "corners": [CORNER], "pinned": 1},
        {
            "type": "pack",
            "layer": LAYER,
            "selection_id": "selection",
            "tile_u": True,
        },
        {"type": "seam_set", "selection_id": "selection", "seam": True, "extra": 1},
    ],
)
def test_uv_operations_reject_ambiguous_or_non_strict_payloads(
    operation: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(operation)


def test_uv_coordinate_bounds_and_raw_corner_budget() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            {
                "type": "coordinate_set",
                "layer": LAYER,
                "corners": [{**CORNER, "uv": [float("inf"), 0.0]}],
            }
        )
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            {
                "type": "coordinate_set",
                "layer": LAYER,
                "corners": [
                    {
                        "loop_index": index,
                        "face_index": 0,
                        "corner_index": index,
                        "vertex_index": index,
                        "uv": [0.0, 0.0],
                    }
                    for index in range(4097)
                ],
            }
        )

