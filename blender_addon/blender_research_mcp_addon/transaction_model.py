"""Pure transaction and idempotency state used by Blender command handlers."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


class TransactionModelError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ScaleDelta:
    object_name: str
    object_identity: str
    before: dict[str, float]
    after: dict[str, float]


@dataclass
class VisibilityDelta:
    object_name: str
    object_identity: str
    before: dict[str, bool]
    after: dict[str, bool]


@dataclass
class ModifierStateDelta:
    object_name: str
    object_identity: str
    modifier_name: str
    modifier_identity: str
    before: dict[str, bool]
    after: dict[str, bool]


@dataclass
class ShapeKeyDelta:
    object_name: str
    object_identity: str
    shape_key_name: str
    shape_key_identity: str
    before: float
    after: float


PropertyValue = bool | int | float | tuple[float, ...]


@dataclass
class MaterialInputDelta:
    object_name: str
    object_identity: str
    material_slot_index: int
    material_name: str
    material_identity: str
    node_name: str
    node_identity: str
    socket_identifier: str
    socket_identity: str
    socket_kind: str
    before: PropertyValue
    after: PropertyValue


TransactionDelta = (
    ScaleDelta | VisibilityDelta | ModifierStateDelta | ShapeKeyDelta | MaterialInputDelta
)


@dataclass(frozen=True)
class PropertyRef:
    kind: str
    target: tuple[str, ...]
    attribute: str


def delta_properties(
    delta: TransactionDelta,
) -> list[tuple[PropertyRef, PropertyValue, PropertyValue]]:
    if isinstance(delta, ScaleDelta):
        return [
            (
                PropertyRef(
                    kind="object_scale",
                    target=(delta.object_name, delta.object_identity),
                    attribute=axis,
                ),
                delta.before[axis],
                value,
            )
            for axis, value in delta.after.items()
        ]
    if isinstance(delta, VisibilityDelta):
        return [
            (
                PropertyRef(
                    kind="object_visibility",
                    target=(delta.object_name, delta.object_identity),
                    attribute=attribute,
                ),
                delta.before[attribute],
                value,
            )
            for attribute, value in delta.after.items()
        ]
    if isinstance(delta, ModifierStateDelta):
        return [
            (
                PropertyRef(
                    kind="modifier_state",
                    target=(
                        delta.object_name,
                        delta.object_identity,
                        delta.modifier_name,
                        delta.modifier_identity,
                    ),
                    attribute=attribute,
                ),
                delta.before[attribute],
                value,
            )
            for attribute, value in delta.after.items()
        ]
    if isinstance(delta, ShapeKeyDelta):
        return [
            (
                PropertyRef(
                    kind="shape_key_value",
                    target=(
                        delta.object_name,
                        delta.object_identity,
                        delta.shape_key_name,
                        delta.shape_key_identity,
                    ),
                    attribute="value",
                ),
                delta.before,
                delta.after,
            )
        ]
    if isinstance(delta, MaterialInputDelta):
        return [
            (
                PropertyRef(
                    kind="material_input",
                    target=(
                        delta.object_name,
                        delta.object_identity,
                        str(delta.material_slot_index),
                        delta.material_name,
                        delta.material_identity,
                        delta.node_name,
                        delta.node_identity,
                        delta.socket_identifier,
                        delta.socket_identity,
                        delta.socket_kind,
                    ),
                    attribute="default_value",
                ),
                delta.before,
                delta.after,
            )
        ]
    raise TypeError(f"Unsupported transaction delta: {type(delta).__name__}")


def values_equal(left: PropertyValue, right: PropertyValue) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, tuple) or isinstance(right, tuple):
        if not isinstance(left, tuple) or not isinstance(right, tuple) or len(left) != len(right):
            return False
        pairs = zip(left, right, strict=True)
        return all(values_equal(left_value, right_value) for left_value, right_value in pairs)
    if isinstance(left, int) or isinstance(right, int):
        return type(left) is type(right) and left == right
    return abs(float(left) - float(right)) <= 1e-7


@dataclass
class Transaction:
    transaction_id: str
    label: str | None
    context_snapshot: dict[str, Any]
    context_fingerprint: str
    started_generation: int
    status: str = "active"
    deltas: list[TransactionDelta] = field(default_factory=list)

    def expected_properties(self) -> dict[PropertyRef, PropertyValue]:
        expected: dict[PropertyRef, PropertyValue] = {}
        for delta in self.deltas:
            for reference, _before, after in delta_properties(delta):
                expected[reference] = after
        return expected

    def delta_kinds(self) -> list[str]:
        return sorted(
            {
                reference.kind
                for delta in self.deltas
                for reference, _, _ in delta_properties(delta)
            }
        )


class TransactionBook:
    def __init__(self) -> None:
        self.active: Transaction | None = None
        self.last_status = "none"

    def begin(
        self,
        *,
        label: str | None,
        context_snapshot: dict[str, Any],
        context_fingerprint: str,
        scene_generation: int,
    ) -> Transaction:
        if self.active is not None:
            raise TransactionModelError(
                "TRANSACTION_ACTIVE",
                f"Transaction is already active: {self.active.transaction_id}",
            )
        transaction = Transaction(
            transaction_id=str(uuid.uuid4()),
            label=label,
            context_snapshot=context_snapshot,
            context_fingerprint=context_fingerprint,
            started_generation=scene_generation,
        )
        self.active = transaction
        self.last_status = "active"
        return transaction

    def require(self, transaction_id: str) -> Transaction:
        transaction = self.active
        if transaction is None or transaction.transaction_id != transaction_id:
            raise TransactionModelError(
                "TRANSACTION_NOT_FOUND",
                f"Active transaction does not match: {transaction_id}",
            )
        return transaction

    def finish(self, transaction: Transaction, status: str) -> None:
        if self.active is not transaction:
            raise TransactionModelError(
                "TRANSACTION_NOT_FOUND",
                "Transaction is no longer active",
            )
        transaction.status = status
        self.last_status = status
        self.active = None

    def abandon(self, status: str) -> None:
        if self.active is not None:
            self.active.status = status
            self.active = None
        self.last_status = status


def request_fingerprint(request: dict[str, Any]) -> str:
    relevant = {
        "command": request.get("command"),
        "params": request.get("params", {}),
        "expected_scene_generation": request.get("expected_scene_generation"),
        "idempotency_key": request.get("idempotency_key"),
    }
    encoded = json.dumps(
        relevant,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def context_fingerprint(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class IdempotencyCache:
    def __init__(self, maximum: int = 256) -> None:
        self.maximum = maximum
        self._items: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()

    def lookup(self, key: str, fingerprint: str) -> dict[str, Any] | None:
        item = self._items.get(key)
        if item is None:
            return None
        existing_fingerprint, response = item
        if existing_fingerprint != fingerprint:
            raise TransactionModelError(
                "IDEMPOTENCY_CONFLICT",
                "Idempotency key was already used with different input",
            )
        self._items.move_to_end(key)
        return response.copy()

    def store(self, key: str, fingerprint: str, response: dict[str, Any]) -> None:
        self._items[key] = (fingerprint, response.copy())
        self._items.move_to_end(key)
        while len(self._items) > self.maximum:
            self._items.popitem(last=False)

    def remove_transaction(self, transaction_id: str) -> None:
        keys = [
            key
            for key, (_fingerprint, response) in self._items.items()
            if response.get("result", {}).get("transaction_id") == transaction_id
        ]
        for key in keys:
            del self._items[key]
