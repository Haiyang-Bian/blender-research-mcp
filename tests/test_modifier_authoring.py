import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.modifier_authoring import ModifierDefinition, ModifierSettings


def test_modifier_definitions_are_closed_typed_and_strict() -> None:
    adapter = TypeAdapter(ModifierDefinition)
    bevel = adapter.validate_python(
        {
            "type": "BEVEL",
            "name": "Soft Edges",
            "width": 0.25,
            "segments": 3,
            "affect": "EDGES",
        }
    )
    assert bevel.width == 0.25
    assert bevel.segments == 3

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"type": "BEVEL", "name": "Bad", "segments": 2.0}
        )
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"type": "BEVEL", "name": "Bad", "segments": 2, "unknown": True}
        )


def test_modifier_definitions_enforce_dependent_options() -> None:
    adapter = TypeAdapter(ModifierDefinition)
    with pytest.raises(ValidationError, match="use_rim"):
        adapter.validate_python(
            {
                "type": "SOLIDIFY",
                "name": "Shell",
                "use_rim": False,
                "use_rim_only": True,
            }
        )
    with pytest.raises(ValidationError, match="solver=EXACT"):
        adapter.validate_python(
            {
                "type": "BOOLEAN",
                "name": "Cut",
                "operand": {
                    "object_name": "Cutter",
                    "expected_object_identity": "object:cutter",
                },
                "solver": "FAST",
                "use_hole_tolerant": True,
            }
        )
    with pytest.raises(ValidationError, match="solver=FAST"):
        adapter.validate_python(
            {
                "type": "BOOLEAN",
                "name": "Cut",
                "operand": {
                    "object_name": "Cutter",
                    "expected_object_identity": "object:cutter",
                },
                "solver": "EXACT",
                "double_threshold": 0.001,
            }
        )


def test_modifier_settings_require_a_nonempty_typed_patch() -> None:
    adapter = TypeAdapter(ModifierSettings)
    settings = adapter.validate_python(
        {"type": "SUBSURF", "levels": 3, "show_render": False}
    )
    assert settings.levels == 3
    with pytest.raises(ValidationError, match="at least one"):
        adapter.validate_python({"type": "SUBSURF"})
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "SUBSURF", "levels": True})
