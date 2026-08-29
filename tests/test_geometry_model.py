import importlib.util
from pathlib import Path


def load_geometry_model():
    path = (
        Path(__file__).parents[1]
        / "blender_addon"
        / "blender_research_mcp_addon"
        / "geometry_model.py"
    )
    spec = importlib.util.spec_from_file_location("addon_geometry_model_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_polygon_summary_classifies_edges_materials_and_area() -> None:
    module = load_geometry_model()
    result = module.summarize_polygon_diagnostics(
        edge_count=5,
        material_slot_count=2,
        polygons=[
            ([0, 1, 2], 0, 1.5),
            ([1, 2, 3], 1, 2.0),
            ([1, 2, 3], 9, 0.5),
        ],
    )

    assert result["surface_area_local"] == 4.0
    assert result["edge_topology"] == {
        "loose": 1,
        "boundary": 1,
        "manifold": 1,
        "non_manifold": 2,
    }
    assert result["material_polygon_counts"] == [1, 1]
    assert result["unassigned_polygon_count"] == 1
