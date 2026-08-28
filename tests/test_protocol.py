from uuid import uuid4

import pytest
from pydantic import ValidationError

from blender_research_mcp.errors import ErrorInfo, ErrorKind
from blender_research_mcp.protocol import RequestEnvelope, ResponseEnvelope


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
