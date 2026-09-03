from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

import blender_research_mcp.server as server_module
from blender_research_mcp.mesh_authoring import MeshOperation
from blender_research_mcp.mesh_batch import BatchSteps


@pytest.mark.parametrize(
    "second,area",
    [
        ([(0, 0), (1, 0), (0, 1)], 0.5),
        ([(1, 0), (1, 1), (0, 1)], 0.0),
        ([(1, 0), (2, 0), (1, -1)], 0.0),
        ([(0.1, 0.1), (0.3, 0.1), (0.1, 0.3)], 0.02),
    ],
)
def test_uv_overlap_distinguishes_positive_area_from_legal_contacts(second, area):
    path = (
        Path(__file__).parents[1] / "blender_addon/blender_research_mcp_addon/mesh_uv_geometry.py"
    )
    spec = importlib.util.spec_from_file_location("uv_geometry_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = [(0, 0), (1, 0), (0, 1)]
    assert module.triangle_overlap_area(first, second) == pytest.approx(area, abs=1e-14)
    assert module.triangle_overlap_area(
        list(reversed(first)), list(reversed(second))
    ) == pytest.approx(area, abs=1e-14)


@pytest.mark.parametrize("limit", [-1, True, float("nan"), float("inf")])
def test_displacement_budget_rejects_nonfinite_or_invalid_limits(limit):
    with pytest.raises(ValidationError):
        TypeAdapter(MeshOperation).validate_python(
            {
                "type": "smooth",
                "selection_id": "selection",
                "maximum_displacement": limit,
            }
        )


def test_nested_patch_batch_references_are_closed_and_typed():
    operation = {
        "type": "grid_fill",
        "boundary": {
            "type": "CLOSED_LOOP",
            "selection_alias": "loop",
            "corner_aliases": ["a", "b", "c", "d"],
        },
    }
    step = {
        "type": "mesh_edit",
        "target_alias": "mesh",
        "data_scope": "OBJECT",
        "operation": operation,
    }
    assert TypeAdapter(BatchSteps).validate_python([step])[0].operation.boundary.corner_aliases == (
        "a",
        "b",
        "c",
        "d",
    )
    with pytest.raises(ValidationError):
        TypeAdapter(BatchSteps).validate_python(
            [{**step, "operation": {**operation, "selection_alias": "also_a_loop"}}]
        )
    with pytest.raises(ValidationError):
        TypeAdapter(BatchSteps).validate_python(
            [
                {
                    **step,
                    "operation": {
                        **operation,
                        "boundary": {**operation["boundary"], "indices": [1, 2, 3]},
                    },
                }
            ]
        )


def test_optional_workflow_fields_gate_versions_without_changing_legacy_requests(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = []
            self.requirements = []

        async def connect(self):
            return None

        def require_capability(self, name, version=1):
            self.requirements.append((name, version))

        async def call(self, command, params=None, **kwargs):
            self.calls.append((command, params, kwargs))
            return {"changed": True}

        async def close(self):
            return None

    fake = Client()
    monkeypatch.setattr(server_module, "BridgeClient", lambda **kwargs: fake)
    server = server_module.create_server()
    target = {
        "transaction_id": "tx",
        "object_name": "Mesh",
        "expected_object_identity": "object:1",
        "expected_mesh_identity": "mesh:1",
        "expected_mesh_users": 1,
        "expected_mesh_user_objects": [
            {"object_name": "Mesh", "expected_object_identity": "object:1"}
        ],
        "expected_mesh_fingerprint": "a" * 64,
        "data_scope": "OBJECT",
        "expected_scene_generation": 1,
        "idempotency_key": "123e4567-e89b-12d3-a456-426614174000",
    }
    asyncio.run(
        server.call_tool(
            "mesh.edit", {**target, "operation": {"type": "grid_fill", "selection_id": "loop"}}
        )
    )
    assert ("mesh_topology", 6) not in fake.requirements
    assert all(
        key not in fake.calls[-1][1]["operation"]
        for key in ("boundary", "uv_creation", "allow_hidden")
    )
    fake.requirements.clear()
    asyncio.run(
        server.call_tool(
            "mesh.edit",
            {
                **target,
                "operation": {
                    "type": "smooth",
                    "selection_id": "inner",
                    "maximum_displacement": 0.01,
                },
            },
        )
    )
    assert ("mesh_deformation", 2) in fake.requirements
    assert fake.calls[-1][1]["operation"]["maximum_displacement"] == 0.01
    asyncio.run(
        server.call_tool(
            "mesh.validate",
            {"selection_id": "faces", "check": "LOCAL_QUALITY", "scope": "SELECTION_AND_NEIGHBORS"},
        )
    )
    assert ("mesh_validation", 3) in fake.requirements
    assert fake.calls[-1][1]["scope"] == "SELECTION_AND_NEIGHBORS"
