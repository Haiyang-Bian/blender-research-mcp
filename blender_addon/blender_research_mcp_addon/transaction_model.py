"""Pure transaction and idempotency state used by Blender command handlers."""

from __future__ import annotations

import hashlib
import json
import struct
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

MAX_TRANSACTION_DELTAS = 256
TRANSACTION_CONTEXT_KEYS = (
    "scene",
    "view_layer",
    "mode",
    "frame_current",
    "active_camera",
)
USER_UI_CONTEXT_KEYS = (
    "workspace",
    "window_id",
    "area_id",
    "region_id",
    "viewport_id",
    "active_object",
    "selected_objects",
    "view",
)


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
class ObjectTransformDelta:
    object_name: str
    object_identity: str
    before: dict[str, dict[str, float]]
    after: dict[str, dict[str, float]]


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
class ModifierSettingsDelta:
    object_name: str
    object_identity: str
    modifier_name: str
    modifier_identity: str
    modifier_type: str
    before: dict[str, Any]
    after: dict[str, Any]


@dataclass
class ModifierCreateDelta:
    object_name: str
    object_identity: str
    modifier_name: str
    modifier_identity: str
    modifier_type: str
    stack_index: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModifierMoveDelta:
    object_name: str
    object_identity: str
    modifier_name: str
    modifier_identity: str
    before_index: int
    after_index: int


@dataclass
class ModifierDeleteDelta:
    object_name: str
    object_identity: str
    modifier_name: str
    modifier_identity: str
    modifier_type: str
    stack_index: int
    before: dict[str, bool]
    after: dict[str, bool]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ShapeKeyDelta:
    object_name: str
    object_identity: str
    shape_key_name: str
    shape_key_identity: str
    before: float
    after: float


PropertyValue = bool | int | float | str | tuple[float, ...]


@dataclass
class ObjectDataDelta:
    object_name: str
    object_identity: str
    data_name: str
    data_identity: str
    data_kind: str
    expected_users: int
    before: dict[str, PropertyValue]
    after: dict[str, PropertyValue]


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


@dataclass(frozen=True)
class StructureGuard:
    """Expected session-local state for one structurally edited Blender resource."""

    kind: str
    name: str
    identity: str
    fingerprint: str
    users: int | None = None


@dataclass
class ModifierStackGuard:
    """Baseline and latest expected state for one object-local Modifier stack."""

    object_name: str
    object_identity: str
    baseline_fingerprint: str
    expected_fingerprint: str


@dataclass
class MeshSnapshotGuard:
    """Baseline snapshot and latest expected state for one edited Mesh data-block."""

    object_name: str
    object_identity: str
    mesh_name: str
    mesh_identity: str
    baseline_fingerprint: str
    expected_fingerprint: str
    expected_users: int
    expected_user_objects: tuple[tuple[str, str], ...]
    data_scope: str
    snapshot: Any | None = None
    source_mesh: Any | None = None
    source_mesh_name: str | None = None
    source_mesh_identity: str | None = None
    source_fingerprint: str | None = None
    source_expected_users: int | None = None
    source_expected_user_objects: tuple[tuple[str, str], ...] = ()


@dataclass
class MeshEditDelta:
    object_name: str
    object_identity: str
    mesh_name: str
    mesh_identity: str
    operation: str
    before_fingerprint: str
    after_fingerprint: str
    data_scope: str


@dataclass
class StructuralDelta:
    """A reversible structural change interpreted by the Blender-side authoring layer.

    ``payload`` deliberately remains Blender-private.  It may contain runtime data-block
    references needed to restore an unlink or remove a transaction-created resource; it
    is never serialized into an MCP response.
    """

    kind: str
    action: str
    before: tuple[StructureGuard, ...]
    after: tuple[StructureGuard, ...]
    payload: dict[str, Any] = field(default_factory=dict)


TransactionDelta = (
    ScaleDelta
    | ObjectTransformDelta
    | VisibilityDelta
    | ModifierStateDelta
    | ModifierSettingsDelta
    | ModifierCreateDelta
    | ModifierMoveDelta
    | ModifierDeleteDelta
    | ShapeKeyDelta
    | MaterialInputDelta
    | ObjectDataDelta
    | MeshEditDelta
    | StructuralDelta
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
    if isinstance(delta, ObjectTransformDelta):
        properties = []
        for channel, values in delta.after.items():
            kind = {
                "location": "object_location",
                "rotation_euler": "object_rotation_euler",
                "scale": "object_scale",
            }[channel]
            properties.extend(
                (
                    PropertyRef(
                        kind=kind,
                        target=(delta.object_name, delta.object_identity),
                        attribute=axis,
                    ),
                    delta.before[channel][axis],
                    value,
                )
                for axis, value in values.items()
            )
        return properties
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
    if isinstance(delta, ObjectDataDelta):
        return [
            (
                PropertyRef(
                    kind=f"{delta.data_kind}_setting",
                    target=(
                        delta.object_name,
                        delta.object_identity,
                        delta.data_name,
                        delta.data_identity,
                        str(delta.expected_users),
                    ),
                    attribute=attribute,
                ),
                delta.before[attribute],
                value,
            )
            for attribute, value in delta.after.items()
        ]
    if isinstance(
        delta,
        (
            ModifierSettingsDelta,
            ModifierCreateDelta,
            ModifierMoveDelta,
            ModifierDeleteDelta,
            MeshEditDelta,
        ),
    ):
        return []
    if isinstance(delta, StructuralDelta):
        return []
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
    if isinstance(left, str) or isinstance(right, str):
        return type(left) is type(right) and left == right
    # Blender RNA numeric properties are stored as IEEE-754 single-precision
    # values. Compare at that storage precision so a submitted decimal such as
    # 6.2 matches the 6.199999809... value read back from Blender, while a
    # genuine one-ULP user edit still remains a conflict.
    return struct.pack("<f", float(left)) == struct.pack("<f", float(right))


@dataclass
class Transaction:
    transaction_id: str
    label: str | None
    context_snapshot: dict[str, Any]
    context_fingerprint: str
    started_generation: int
    status: str = "active"
    deltas: list[TransactionDelta] = field(default_factory=list)
    modifier_stack_guards: dict[tuple[str, str], ModifierStackGuard] = field(default_factory=dict)
    mesh_snapshot_guards: dict[tuple[str, str], MeshSnapshotGuard] = field(default_factory=dict)

    def ensure_capacity(self, additional: int = 1) -> None:
        if isinstance(additional, bool) or additional < 0:
            raise ValueError("additional must be a non-negative integer")
        if len(self.deltas) + additional > MAX_TRANSACTION_DELTAS:
            raise TransactionModelError(
                "TRANSACTION_DELTA_LIMIT",
                f"A transaction may contain at most {MAX_TRANSACTION_DELTAS} deltas",
            )

    def record(self, delta: TransactionDelta) -> None:
        self.ensure_capacity()
        self.deltas.append(delta)

    def structural_deltas(self) -> list[StructuralDelta]:
        return [delta for delta in self.deltas if isinstance(delta, StructuralDelta)]

    def expected_structures(self) -> dict[tuple[str, str, str], StructureGuard]:
        expected: dict[tuple[str, str, str], StructureGuard] = {}
        for delta in self.structural_deltas():
            for guard in delta.after:
                expected[(guard.kind, guard.name, guard.identity)] = guard
        return expected

    def refresh_structure_guard(self, guard: StructureGuard) -> None:
        """Refresh every matching guard after a later agent-owned structural write."""

        key = (guard.kind, guard.name, guard.identity)
        found = False
        for delta in self.structural_deltas():
            updated = []
            for current in delta.after:
                if (current.kind, current.name, current.identity) == key:
                    updated.append(guard)
                    found = True
                else:
                    updated.append(current)
            delta.after = tuple(updated)
        if not found:
            raise TransactionModelError(
                "STRUCTURE_GUARD_NOT_FOUND",
                f"No structural guard exists for {guard.kind} {guard.name}",
            )

    def refresh_object_data_users(self, data_identity: str, users: int) -> None:
        for delta in self.deltas:
            if isinstance(delta, ObjectDataDelta) and delta.data_identity == data_identity:
                delta.expected_users = users

    def ensure_modifier_stack_guard(
        self,
        *,
        object_name: str,
        object_identity: str,
        fingerprint: str,
    ) -> ModifierStackGuard:
        key = (object_name, object_identity)
        guard = self.modifier_stack_guards.get(key)
        if guard is None:
            guard = ModifierStackGuard(
                object_name=object_name,
                object_identity=object_identity,
                baseline_fingerprint=fingerprint,
                expected_fingerprint=fingerprint,
            )
            self.modifier_stack_guards[key] = guard
        return guard

    def refresh_modifier_stack_guard(
        self,
        *,
        object_name: str,
        object_identity: str,
        fingerprint: str,
    ) -> None:
        key = (object_name, object_identity)
        guard = self.modifier_stack_guards.get(key)
        if guard is None:
            raise TransactionModelError(
                "MODIFIER_STACK_GUARD_NOT_FOUND",
                f"No Modifier stack guard exists for {object_name}",
            )
        guard.expected_fingerprint = fingerprint

    def mesh_snapshot_guard(self, mesh_name: str, mesh_identity: str) -> MeshSnapshotGuard | None:
        return self.mesh_snapshot_guards.get((mesh_name, mesh_identity))

    def add_mesh_snapshot_guard(self, guard: MeshSnapshotGuard) -> None:
        key = (guard.mesh_name, guard.mesh_identity)
        if key in self.mesh_snapshot_guards:
            raise TransactionModelError(
                "MESH_SNAPSHOT_ACTIVE",
                f"A Mesh snapshot already exists for {guard.mesh_name}",
            )
        self.mesh_snapshot_guards[key] = guard

    def remove_mesh_snapshot_guard(self, guard: MeshSnapshotGuard) -> None:
        self.mesh_snapshot_guards.pop((guard.mesh_name, guard.mesh_identity), None)

    def expected_properties(self) -> dict[PropertyRef, PropertyValue]:
        expected: dict[PropertyRef, PropertyValue] = {}
        for delta in self.deltas:
            for reference, _before, after in delta_properties(delta):
                expected[reference] = after
        return expected

    def delta_kinds(self) -> list[str]:
        kinds = {
            reference.kind for delta in self.deltas for reference, _, _ in delta_properties(delta)
        }
        kinds.update(delta.kind for delta in self.structural_deltas())
        for delta in self.deltas:
            if isinstance(delta, ModifierSettingsDelta):
                kinds.add("modifier_settings")
            elif isinstance(delta, ModifierCreateDelta):
                kinds.add("modifier_create")
            elif isinstance(delta, ModifierMoveDelta):
                kinds.add("modifier_move")
            elif isinstance(delta, ModifierDeleteDelta):
                kinds.add("modifier_delete")
            elif isinstance(delta, MeshEditDelta):
                kinds.add("mesh_edit")
        return sorted(kinds)


class TransactionBook:
    def __init__(self) -> None:
        self.active: Transaction | None = None
        self.last_status = "none"
        self._terminal: OrderedDict[str, dict[str, Any]] = OrderedDict()

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

    def finish(
        self,
        transaction: Transaction,
        status: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.active is not transaction:
            raise TransactionModelError(
                "TRANSACTION_NOT_FOUND",
                "Transaction is no longer active",
            )
        transaction.status = status
        self.last_status = status
        self.active = None
        terminal = {
            "transaction_id": transaction.transaction_id,
            "label": transaction.label,
            "status": status,
        }
        if details:
            terminal.update(details)
        self._terminal[transaction.transaction_id] = terminal
        self._terminal.move_to_end(transaction.transaction_id)
        while len(self._terminal) > 32:
            self._terminal.popitem(last=False)

    def terminal(self, transaction_id: str) -> dict[str, Any] | None:
        result = self._terminal.get(transaction_id)
        return dict(result) if result is not None else None

    def abandon(self, status: str) -> None:
        if self.active is not None:
            self.finish(self.active, status)
        else:
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


def context_projection(
    snapshot: dict[str, Any],
    keys: tuple[str, ...],
) -> dict[str, Any]:
    """Return a stable named projection without treating absent fields as evidence."""

    return {key: snapshot[key] for key in keys if key in snapshot}


def transaction_context_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep only state that can change the meaning or safety of a scene write."""

    return context_projection(snapshot, TRANSACTION_CONTEXT_KEYS)


def user_ui_context_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep navigation and selection evidence that belongs to the human UI."""

    return context_projection(snapshot, USER_UI_CONTEXT_KEYS)


def changed_context_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    """Describe exact nested context drift for diagnostics and preservation evidence."""

    if isinstance(before, dict) and isinstance(after, dict):
        changed: list[str] = []
        for key in sorted(before.keys() | after.keys()):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                changed.append(path)
            else:
                changed.extend(changed_context_paths(before[key], after[key], path))
        return changed
    return [] if before == after else [prefix or "context"]


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
