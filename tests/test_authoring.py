import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.authoring import (
    ConeDefinition,
    HexColor,
    InitialTransform,
    LocationAxisPatch,
    MaterialDefinition,
    ObjectDefinition,
    RGBAColor,
    ScaleAxisPatch,
    TextureMapping,
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


def test_material_definition_accepts_hex_or_explicit_rgba_only() -> None:
    material = MaterialDefinition(
        name="Moon",
        base_color=HexColor(value="#EFF0EA"),
        emission_color=RGBAColor(value=(0.1, 0.2, 0.3, 1.0)),
        roughness=0.7,
    )

    assert material.base_color.type == "hex_srgb"
    assert material.emission_color.type == "rgba"
    with pytest.raises(ValidationError):
        RGBAColor(value=(1.1, 0.2, 0.3, 1.0))
    with pytest.raises(ValidationError):
        MaterialDefinition.model_validate({"name": "Bad", "metallic": True})


def test_texture_mapping_rejects_zero_scale_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        TextureMapping(scale={"x": 0, "y": 1, "z": 1})
    with pytest.raises(ValidationError):
        TextureMapping.model_validate({"unsupported": 1})
