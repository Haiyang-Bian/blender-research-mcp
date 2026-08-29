import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.authoring import (
    ConeDefinition,
    InitialTransform,
    LocationAxisPatch,
    ObjectDefinition,
    ScaleAxisPatch,
)


def test_object_definition_is_closed_and_discriminated() -> None:
    adapter = TypeAdapter(ObjectDefinition)
    cube = adapter.validate_python(
        {
            "type": "cube",
            "name": "Moon Blockout",
            "size": 4,
            "transform": {
                "location": {"x": 1, "y": 2, "z": 3},
                "rotation_euler_degrees": {"x": 0, "y": 15, "z": 30},
                "scale": {"x": 1, "y": 1, "z": 1},
            },
        }
    )

    assert cube.type == "cube"
    assert cube.size == 4.0
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "torus", "name": "Unsupported"})
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "cube", "name": "Cube", "arbitrary": True})


def test_collection_identity_and_primitive_ranges_are_strict() -> None:
    adapter = TypeAdapter(ObjectDefinition)

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"type": "plane", "name": "Water", "collection_name": "Scene"}
        )
    with pytest.raises(ValidationError):
        ConeDefinition(type="cone", name="Nothing", radius1=0, radius2=0)
    with pytest.raises(ValidationError):
        ScaleAxisPatch(x=0)
    with pytest.raises(ValidationError):
        LocationAxisPatch(x=True)


def test_initial_transform_uses_absolute_xyz_values() -> None:
    transform = InitialTransform()

    assert transform.location.model_dump() == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert transform.rotation_euler_degrees.model_dump() == {
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
    }
    assert transform.scale.model_dump() == {"x": 1.0, "y": 1.0, "z": 1.0}
