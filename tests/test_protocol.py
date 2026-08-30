from uuid import uuid4

import pytest
from pydantic import ValidationError

from blender_research_mcp.errors import ErrorInfo, ErrorKind
from blender_research_mcp.protocol import CapabilityVersions, RequestEnvelope, ResponseEnvelope


def test_request_rejects_unknown_fields_and_invalid_deadline() -> None:
    with pytest.raises(ValidationError):
        RequestEnvelope(
            request_id=uuid4(),
            session_token="x" * 32,
            command="connection.ping",
            deadline_ms=31_000,
        )
    with pytest.raises(ValidationError):
        RequestEnvelope.model_validate(
            {
                "request_id": str(uuid4()),
                "session_token": "x" * 32,
                "command": "connection.ping",
                "unexpected": True,
            }
        )


def test_response_requires_an_error_on_failure() -> None:
    request_id = uuid4()
    with pytest.raises(ValidationError):
        ResponseEnvelope(
            request_id=request_id,
            ok=False,
            scene_generation=0,
        )

    response = ResponseEnvelope(
        request_id=request_id,
        ok=False,
        scene_generation=2,
        error=ErrorInfo(
            kind=ErrorKind.CONFLICT,
            code="STALE_SCENE",
            message="scene generation changed",
        ),
    )
    assert response.error is not None
    assert response.error.code == "STALE_SCENE"


def test_capability_versions_default_to_incompatible_zeroes() -> None:
    versions = CapabilityVersions()

    assert versions.viewport_capture == 0
    assert versions.viewport_raycast == 0
    assert versions.geometry_inspection == 0
    assert versions.lookdev_inspection == 0
    assert versions.modifier_authoring == 0
    assert versions.mesh_topology == 0
    assert versions.mesh_component_map == 0
    assert versions.mesh_selection == 0
    assert versions.mesh_surface_query == 0
    assert versions.mesh_deformation == 0
    assert versions.mesh_validation == 0
    assert versions.object_visibility == 0
    assert versions.object_transform == 0
    assert versions.object_settings == 0
    assert versions.scene_inspection == 0
    assert versions.object_authoring == 0
    assert versions.material_authoring == 0
    assert versions.image_assets == 0
    assert versions.world_authoring == 0
    assert versions.render_preview == 0
    assert versions.render_export == 0
    assert versions.modifier_state == 0
    assert versions.shape_key_value == 0
    assert versions.material_input == 0
    assert versions.project_lifecycle == 0
    assert versions.application_lifecycle == 0
