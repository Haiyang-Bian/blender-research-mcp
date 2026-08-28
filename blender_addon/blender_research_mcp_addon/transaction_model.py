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
    before: dict[str, float]
    after: dict[str, float]


@dataclass
class Transaction:
    transaction_id: str
    label: str | None
    context_snapshot: dict[str, Any]
    context_fingerprint: str
    started_generation: int
    status: str = "active"
    deltas: list[ScaleDelta] = field(default_factory=list)

    def expected_scale(self) -> dict[tuple[str, str], float]:
        expected: dict[tuple[str, str], float] = {}
        for delta in self.deltas:
            for axis, value in delta.after.items():
                expected[(delta.object_name, axis)] = value
        return expected


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
