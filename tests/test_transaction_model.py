import importlib.util
import struct
import sys
from pathlib import Path

import pytest


def load_transaction_model():
    path = (
        Path(__file__).parents[1]
        / "blender_addon"
        / "blender_research_mcp_addon"
        / "transaction_model.py"
    )
    spec = importlib.util.spec_from_file_location("transaction_model_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_transaction_book_is_single_owner_and_tracks_expected_properties() -> None:
    model = load_transaction_model()
    book = model.TransactionBook()
    transaction = book.begin(
        label="eye preview",
        context_snapshot={"active": "目.L"},
        context_fingerprint="fingerprint",
        scene_generation=4,
    )
    transaction.deltas.extend(
        [
            model.ScaleDelta("Cutter", "object:1", {"z": 1.0}, {"z": 1.1}),
            model.ScaleDelta("Cutter", "object:1", {"z": 1.1}, {"z": 1.2}),
        ]
    )

    assert transaction.expected_properties() == {
        model.PropertyRef("object_scale", ("Cutter", "object:1"), "z"): 1.2
    }
    assert transaction.delta_kinds() == ["object_scale"]
    with pytest.raises(model.TransactionModelError, match="already active"):
        book.begin(
            label=None,
            context_snapshot={},
            context_fingerprint="other",
            scene_generation=4,
        )

    book.finish(transaction, "rolled_back")
    assert book.active is None
    assert book.last_status == "rolled_back"


def test_transaction_tracks_all_typed_delta_kinds_and_last_write_wins() -> None:
    model = load_transaction_model()
    transaction = model.Transaction(
        transaction_id="tx",
        label=None,
        context_snapshot={},
        context_fingerprint="context",
        started_generation=1,
    )
    transaction.deltas.extend(
        [
            model.VisibilityDelta(
                "Face",
                "object:face",
                {"hide_render": False},
                {"hide_render": True},
            ),
            model.VisibilityDelta(
                "Face",
                "object:face",
                {"hide_render": True},
                {"hide_render": False},
            ),
            model.ModifierStateDelta(
                "Face",
                "object:face",
                "Subdivision",
                "modifier:subsurf",
                {"show_viewport": True},
                {"show_viewport": False},
            ),
            model.ShapeKeyDelta(
                "Face",
                "object:face",
                "Smile",
                "shape_key:smile",
                0.0,
                0.5,
            ),
            model.MaterialInputDelta(
                "Face",
                "object:face",
                0,
                "Skin",
                "material:skin",
                "Principled BSDF",
                "node:principled",
                "Roughness",
                "socket:roughness",
                "FLOAT",
                0.5,
                0.7,
            ),
        ]
    )

    expected = transaction.expected_properties()

    assert expected[
        model.PropertyRef(
            "object_visibility",
            ("Face", "object:face"),
            "hide_render",
        )
    ] is False
    assert transaction.delta_kinds() == [
        "material_input",
        "modifier_state",
        "object_visibility",
        "shape_key_value",
    ]


def test_property_values_compare_without_bool_or_vector_coercion() -> None:
    model = load_transaction_model()

    assert model.values_equal(0.5, 0.50000001)
    blender_roundtrip = struct.unpack("<f", struct.pack("<f", 6.2))[0]
    assert model.values_equal(6.2, blender_roundtrip)
    bits = struct.unpack("<I", struct.pack("<f", blender_roundtrip))[0]
    next_float32 = struct.unpack("<f", struct.pack("<I", bits + 1))[0]
    assert not model.values_equal(blender_roundtrip, next_float32)
    assert model.values_equal((0.1, 0.2, 0.3), (0.1, 0.2, 0.30000001))
    assert model.values_equal(True, True)
    assert not model.values_equal(True, 1)
    assert not model.values_equal((0.1, 0.2), (0.1, 0.2, 0.3))
    assert model.values_equal("RECTANGLE", "RECTANGLE")
    assert not model.values_equal("RECTANGLE", "SQUARE")


def test_idempotency_cache_replays_same_input_and_rejects_reuse() -> None:
    model = load_transaction_model()
    cache = model.IdempotencyCache(maximum=2)
    request = {
        "request_id": "first",
        "command": "object.transform",
        "params": {"scale": {"z": 1.1}},
        "expected_scene_generation": 2,
        "idempotency_key": "key-1",
    }
    fingerprint = model.request_fingerprint(request)
    cache.store("key-1", fingerprint, {"ok": True, "result": {"after": 1.1}})

    assert cache.lookup("key-1", fingerprint) == {
        "ok": True,
        "result": {"after": 1.1},
    }
    request["params"] = {"scale": {"z": 1.2}}
    with pytest.raises(model.TransactionModelError, match="different input"):
        cache.lookup("key-1", model.request_fingerprint(request))


def test_idempotency_cache_can_remove_auto_rolled_back_transaction() -> None:
    model = load_transaction_model()
    cache = model.IdempotencyCache()
    cache.store(
        "begin-key",
        "fingerprint",
        {"result": {"transaction_id": "tx-1", "status": "active"}},
    )

    cache.remove_transaction("tx-1")

    assert cache.lookup("begin-key", "fingerprint") is None


def test_structural_deltas_are_guarded_and_reported_without_property_coercion() -> None:
    model = load_transaction_model()
    transaction = model.Transaction(
        transaction_id="tx-structure",
        label="build scene",
        context_snapshot={},
        context_fingerprint="context",
        started_generation=2,
    )
    original = model.StructureGuard("object", "Moon", "object:1", "fingerprint-1", 1)
    refreshed = model.StructureGuard("object", "Moon", "object:1", "fingerprint-2", 1)
    transaction.record(
        model.StructuralDelta(
            kind="object_create",
            action="create_resource",
            before=(),
            after=(original,),
        )
    )

    assert transaction.expected_properties() == {}
    assert transaction.expected_structures() == {
        ("object", "Moon", "object:1"): original
    }
    assert transaction.delta_kinds() == ["object_create"]

    transaction.refresh_structure_guard(refreshed)
    assert transaction.expected_structures() == {
        ("object", "Moon", "object:1"): refreshed
    }


def test_transaction_rejects_more_than_256_deltas() -> None:
    model = load_transaction_model()
    transaction = model.Transaction(
        transaction_id="tx-limit",
        label=None,
        context_snapshot={},
        context_fingerprint="context",
        started_generation=0,
    )
    for index in range(model.MAX_TRANSACTION_DELTAS):
        transaction.record(
            model.ScaleDelta(
                "Cube",
                "object:1",
                {"x": float(index)},
                {"x": float(index + 1)},
            )
        )

    with pytest.raises(model.TransactionModelError) as error:
        transaction.record(
            model.ScaleDelta("Cube", "object:1", {"x": 256.0}, {"x": 257.0})
        )

    assert error.value.code == "TRANSACTION_DELTA_LIMIT"


def test_object_transform_delta_tracks_location_rotation_and_scale_separately() -> None:
    model = load_transaction_model()
    transaction = model.Transaction(
        transaction_id="tx-transform",
        label=None,
        context_snapshot={},
        context_fingerprint="context",
        started_generation=0,
    )
    transaction.record(
        model.ObjectTransformDelta(
            object_name="Moon",
            object_identity="object:moon",
            before={
                "location": {"z": 0.0},
                "rotation_euler": {"x": 0.0},
                "scale": {"x": 1.0},
            },
            after={
                "location": {"z": 4.0},
                "rotation_euler": {"x": 1.5707963267948966},
                "scale": {"x": 2.0},
            },
        )
    )

    assert transaction.delta_kinds() == [
        "object_location",
        "object_rotation_euler",
        "object_scale",
    ]
    assert transaction.expected_properties()[
        model.PropertyRef("object_location", ("Moon", "object:moon"), "z")
    ] == 4.0


def test_object_data_delta_guards_identity_users_and_typed_values() -> None:
    model = load_transaction_model()
    transaction = model.Transaction(
        transaction_id="tx-data",
        label=None,
        context_snapshot={},
        context_fingerprint="context",
        started_generation=0,
    )
    transaction.record(
        model.ObjectDataDelta(
            object_name="Key Light",
            object_identity="object:1",
            data_name="Key Light Data",
            data_identity="light:1",
            data_kind="light",
            expected_users=2,
            before={"shape": "SQUARE", "color": (1.0, 1.0, 1.0)},
            after={"shape": "RECTANGLE", "color": (0.5, 0.6, 0.7)},
        )
    )

    assert transaction.delta_kinds() == ["light_setting"]
    expected = transaction.expected_properties()
    reference = model.PropertyRef(
        "light_setting",
        ("Key Light", "object:1", "Key Light Data", "light:1", "2"),
        "shape",
    )
    assert expected[reference] == "RECTANGLE"

    transaction.refresh_object_data_users("light:1", 3)
    refreshed = transaction.expected_properties()
    assert model.PropertyRef(
        "light_setting",
        ("Key Light", "object:1", "Key Light Data", "light:1", "3"),
        "shape",
    ) in refreshed


def test_modifier_stack_guard_keeps_baseline_and_refreshes_latest_expected_state() -> None:
    model = load_transaction_model()
    transaction = model.Transaction("tx-modifiers", None, {}, "context", 0)

    guard = transaction.ensure_modifier_stack_guard(
        object_name="Hull",
        object_identity="object:hull",
        fingerprint="baseline",
    )
    transaction.refresh_modifier_stack_guard(
        object_name="Hull",
        object_identity="object:hull",
        fingerprint="after-agent-write",
    )
    transaction.record(
        model.ModifierSettingsDelta(
            "Hull",
            "object:hull",
            "Soft Edges",
            "modifier:bevel",
            "BEVEL",
            {"width": 0.1},
            {"width": 0.25},
        )
    )

    assert guard.baseline_fingerprint == "baseline"
    assert guard.expected_fingerprint == "after-agent-write"
    assert transaction.expected_properties() == {}
    assert transaction.delta_kinds() == ["modifier_settings"]


def test_mesh_snapshot_guard_and_edit_delta_are_transaction_local() -> None:
    model = load_transaction_model()
    transaction = model.Transaction("tx-mesh", None, {}, "context", 0)
    guard = model.MeshSnapshotGuard(
        object_name="Hull",
        object_identity="object:hull",
        mesh_name="Hull Mesh",
        mesh_identity="mesh:hull",
        baseline_fingerprint="a" * 64,
        expected_fingerprint="a" * 64,
        expected_users=1,
        expected_user_objects=(("Hull", "object:hull"),),
        data_scope="OBJECT",
    )

    transaction.add_mesh_snapshot_guard(guard)
    guard.expected_fingerprint = "b" * 64
    transaction.record(
        model.MeshEditDelta(
            object_name="Hull",
            object_identity="object:hull",
            mesh_name="Hull Mesh",
            mesh_identity="mesh:hull",
            operation="extrude_faces",
            before_fingerprint="a" * 64,
            after_fingerprint="b" * 64,
            data_scope="OBJECT",
        )
    )

    assert transaction.mesh_snapshot_guard("Hull Mesh", "mesh:hull") is guard
    assert transaction.expected_properties() == {}
    assert transaction.delta_kinds() == ["mesh_edit"]
    transaction.remove_mesh_snapshot_guard(guard)
    assert transaction.mesh_snapshot_guards == {}
