import importlib.util
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


def test_transaction_book_is_single_owner_and_tracks_expected_scale() -> None:
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
            model.ScaleDelta("Cutter", {"z": 1.0}, {"z": 1.1}),
            model.ScaleDelta("Cutter", {"z": 1.1}, {"z": 1.2}),
        ]
    )

    assert transaction.expected_scale() == {("Cutter", "z"): 1.2}
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
