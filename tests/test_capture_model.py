import importlib.util
import sys
from pathlib import Path


def load_capture_model():
    path = (
        Path(__file__).parents[1]
        / "blender_addon"
        / "blender_research_mcp_addon"
        / "capture_model.py"
    )
    spec = importlib.util.spec_from_file_location("addon_capture_model_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def evidence(module, capture_id: str, generation: int = 4):
    identity = (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    return module.CaptureEvidence(
        capture_id=capture_id,
        scene_generation=generation,
        scene="Scene",
        view_layer="ViewLayer",
        window_id=1,
        target_name="目标",
        target_identity="object:1",
        viewport_id="1:2",
        view="FRONT",
        display_mode="CURRENT",
        overlays="CURRENT",
        width=800,
        height=400,
        native_sha256="a" * 64,
        projection_kind="ORTHO",
        clip_start=0.01,
        clip_end=1000.0,
        view_matrix=identity,
        projection_matrix=identity,
        perspective_matrix=identity,
    )


def test_capture_book_is_lru_bounded_and_clearable() -> None:
    module = load_capture_model()
    book = module.CaptureBook(limit=2)
    book.add(evidence(module, "first"))
    book.add(evidence(module, "second"))

    assert book.get("first").target_name == "目标"
    book.add(evidence(module, "third"))

    assert book.get("second") is None
    assert book.get("first") is not None
    assert book.get("third") is not None
    assert len(book) == 2
    book.clear()
    assert len(book) == 0
