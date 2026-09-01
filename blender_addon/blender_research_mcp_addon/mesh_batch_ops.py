"""Declarative, revision-aware Mesh batch execution inside one Blender tick."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import bpy

from .capture_model import CaptureBook
from .mesh_attribute_transfer_ops import transfer_attribute
from .mesh_component_map import compose_component_map, remap_selection
from .mesh_deform_ops import DEFORM_OPERATIONS, edit_mesh_deform
from .mesh_ops import (
    MeshOperationError,
    _validate_mesh_target,
    mesh_fingerprint,
    mesh_revision_id,
    mesh_user_refs,
)
from .mesh_query_ops import derive_selection, query_selection, validate_selection
from .mesh_resource_model import MeshResourceBook, MeshResourceError
from .mesh_separation_ops import separate_mesh
from .mesh_surface_ops import validate_mesh, validate_surface
from .mesh_topology_ops import TOPOLOGY_OPERATIONS, edit_mesh_topology
from .mesh_uv_ops import edit_uv, uv_fingerprint
from .mesh_weight_ops import (
    edit_weights,
    group_schema_fingerprint,
    weights_fingerprint,
)
from .structural_ops import session_identity
from .transaction_model import Transaction

MAX_TARGETS = 8
MAX_STEPS = 32
MAX_ALIASES = 64
MAX_TOPOLOGY_STEPS = 8


@dataclass
class MeshBatchExecutionError(RuntimeError):
    cause: Exception
    batch_id: str
    step_index: int
    step_type: str
    aliases: tuple[str, ...]

    def __str__(self) -> str:
        return str(self.cause)


def _batch_error(code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
    raise MeshOperationError(code, message, details=details)


def _alias(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        _batch_error("MESH_BATCH_INVALID", f"{field} must be a non-empty batch alias")
    return value


def _target_params(target: dict[str, Any], data_scope: str = "OBJECT") -> dict[str, Any]:
    return {
        "object_name": target["object_name"],
        "expected_object_identity": target["expected_object_identity"],
        "expected_mesh_identity": target["expected_mesh_identity"],
        "expected_mesh_users": target["expected_mesh_users"],
        "expected_mesh_user_objects": target["expected_mesh_user_objects"],
        "expected_mesh_fingerprint": target["expected_mesh_fingerprint"],
        "data_scope": data_scope,
    }


def _live_target(alias: str, obj: Any) -> dict[str, Any]:
    mesh = obj.data
    refs = mesh_user_refs(mesh)
    return {
        "alias": alias,
        "object_name": obj.name,
        "expected_object_identity": session_identity("object", obj),
        "expected_mesh_identity": session_identity("mesh", mesh),
        "expected_mesh_users": int(mesh.users),
        "expected_mesh_user_objects": [
            {"object_name": name, "expected_object_identity": identity} for name, identity in refs
        ],
        "expected_mesh_fingerprint": mesh_fingerprint(mesh),
        "mesh_revision_id": mesh_revision_id(mesh),
        "uv_fingerprint": uv_fingerprint(mesh),
        "group_schema_fingerprint": group_schema_fingerprint(obj),
        "weights_fingerprint": weights_fingerprint(mesh),
    }


def _attribute_target(target: dict[str, Any], *, data_scope: str = "OBJECT") -> dict[str, Any]:
    return {
        **_target_params(target, data_scope),
        "expected_group_schema_fingerprint": target["group_schema_fingerprint"],
        "expected_weights_fingerprint": target["weights_fingerprint"],
    }


def _validate_target(target: dict[str, Any]) -> tuple[Any, Any]:
    obj, mesh, _scope, _refs = _validate_mesh_target(_target_params(target))
    return obj, mesh


def _reserve(alias_kinds: dict[str, str], alias: Any, kind: str) -> str:
    name = _alias(alias, f"{kind} alias")
    if name in alias_kinds:
        _batch_error(
            "MESH_BATCH_ALIAS_CONFLICT",
            f"Batch alias is already defined: {name}",
            details={"alias": name, "existing_kind": alias_kinds[name], "new_kind": kind},
        )
    alias_kinds[name] = kind
    if len(alias_kinds) > MAX_ALIASES:
        _batch_error("MESH_BATCH_BUDGET_EXCEEDED", "A batch may define at most 64 aliases")
    return name


def _require_alias(alias_kinds: dict[str, str], alias: Any, kind: str) -> str:
    name = _alias(alias, f"{kind} reference")
    if alias_kinds.get(name) != kind:
        _batch_error(
            "MESH_BATCH_REFERENCE_NOT_FOUND",
            f"Batch {kind} alias does not exist before this step: {name}",
            details={"alias": name, "expected_kind": kind, "actual_kind": alias_kinds.get(name)},
        )
    return name


def _selection_refs(operation: dict[str, Any]) -> tuple[str, ...]:
    if operation.get("type") == "combine":
        values = operation.get("selection_aliases")
        if not isinstance(values, list) or len(values) < 2:
            _batch_error("MESH_BATCH_INVALID", "combine requires selection_aliases")
        return tuple(_alias(value, "selection_aliases") for value in values)
    return (_alias(operation.get("selection_alias"), "selection_alias"),)


def _preflight(
    transaction: Transaction,
    book: MeshResourceBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    targets_raw = params.get("targets")
    inputs = params.get("inputs", [])
    steps = params.get("steps")
    if not isinstance(targets_raw, list) or not 1 <= len(targets_raw) <= MAX_TARGETS:
        _batch_error("MESH_BATCH_INVALID", "targets must contain 1 to 8 exact Mesh targets")
    if not isinstance(inputs, list) or len(inputs) > MAX_ALIASES:
        _batch_error("MESH_BATCH_INVALID", "inputs must contain at most 64 resources")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        _batch_error("MESH_BATCH_INVALID", "steps must contain 1 to 32 operations")
    if params.get("on_error", "ROLLBACK_TRANSACTION") != "ROLLBACK_TRANSACTION":
        _batch_error("MESH_BATCH_INVALID", "on_error must be ROLLBACK_TRANSACTION")

    alias_kinds: dict[str, str] = {}
    targets: dict[str, dict[str, Any]] = {}
    for raw in targets_raw:
        if not isinstance(raw, dict):
            _batch_error("MESH_BATCH_INVALID", "Each target must be an object")
        alias = _reserve(alias_kinds, raw.get("alias"), "target")
        target = dict(raw)
        target["alias"] = alias
        obj, _mesh = _validate_target(target)
        targets[alias] = _live_target(alias, obj)

    input_selections: dict[str, dict[str, Any]] = {}
    input_surfaces: dict[str, str] = {}
    for raw in inputs:
        if not isinstance(raw, dict):
            _batch_error("MESH_BATCH_INVALID", "Each input must be an object")
        kind = raw.get("type")
        if kind == "selection":
            alias = _reserve(alias_kinds, raw.get("alias"), "selection")
            target_alias = _require_alias(alias_kinds, raw.get("target_alias"), "target")
            selection_id = raw.get("selection_id")
            if not isinstance(selection_id, str):
                _batch_error("MESH_BATCH_INVALID", "selection input requires selection_id")
            record = book.selection(selection_id)
            obj, _mesh = validate_selection(record)
            if session_identity("object", obj) != targets[target_alias]["expected_object_identity"]:
                _batch_error(
                    "MESH_BATCH_TARGET_MISMATCH",
                    f"Selection input {alias} does not belong to target {target_alias}",
                )
            input_selections[alias] = {
                "selection_id": selection_id,
                "target_alias": target_alias,
                "remap_mode": raw.get("remap_mode", "ALL_MAPPED"),
                "weight_merge": raw.get("weight_merge", "MAX"),
            }
        elif kind == "surface":
            alias = _reserve(alias_kinds, raw.get("alias"), "surface")
            surface_id = raw.get("surface_id")
            if not isinstance(surface_id, str):
                _batch_error("MESH_BATCH_INVALID", "surface input requires surface_id")
            surface = book.surface(surface_id)
            validate_surface(surface)
            input_surfaces[alias] = surface_id
        else:
            _batch_error("MESH_BATCH_INVALID", f"Unsupported input type: {kind}")

    topology_steps = 0
    capacity = 0
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("type"), str):
            _batch_error("MESH_BATCH_INVALID", f"Step {index} must be a typed object")
        step_type = step["type"]
        if step_type == "selection_query":
            _require_alias(alias_kinds, step.get("target_alias"), "target")
            _reserve(alias_kinds, step.get("output_alias"), "selection")
        elif step_type == "selection_derive":
            operation = step.get("operation")
            if not isinstance(operation, dict):
                _batch_error("MESH_BATCH_INVALID", "selection_derive requires operation")
            for alias in _selection_refs(operation):
                _require_alias(alias_kinds, alias, "selection")
            _reserve(alias_kinds, step.get("output_alias"), "selection")
        elif step_type == "mesh_edit":
            _require_alias(alias_kinds, step.get("target_alias"), "target")
            operation = step.get("operation")
            if not isinstance(operation, dict):
                _batch_error("MESH_BATCH_INVALID", "mesh_edit requires operation")
            _require_alias(alias_kinds, operation.get("selection_alias"), "selection")
            if operation.get("type") in {"project", "shrinkwrap"}:
                _require_alias(alias_kinds, operation.get("surface_alias"), "surface")
            if operation.get("type") in TOPOLOGY_OPERATIONS:
                topology_steps += 1
            if step.get("map_alias") is not None:
                _reserve(alias_kinds, step.get("map_alias"), "component_map")
            created = step.get("created_selection_aliases")
            if created is not None:
                if not isinstance(created, dict):
                    _batch_error(
                        "MESH_BATCH_INVALID",
                        "created_selection_aliases must be an object",
                    )
                for value in created.values():
                    if value is not None:
                        _reserve(alias_kinds, value, "selection")
            capacity += 1
        elif step_type == "mesh_separate":
            _require_alias(alias_kinds, step.get("target_alias"), "target")
            _require_alias(alias_kinds, step.get("selection_alias"), "selection")
            _reserve(alias_kinds, step.get("new_target_alias"), "target")
            _reserve(alias_kinds, step.get("new_selection_alias"), "selection")
            _reserve(alias_kinds, step.get("source_map_alias"), "component_map")
            _reserve(alias_kinds, step.get("separated_map_alias"), "component_map")
            topology_steps += 1
            capacity += 2
        elif step_type in {"uv_edit", "weights_edit"}:
            _require_alias(alias_kinds, step.get("target_alias"), "target")
            operation = step.get("operation")
            if not isinstance(operation, dict):
                _batch_error("MESH_BATCH_INVALID", f"{step_type} requires operation")
            if operation.get("selection_alias") is not None:
                _require_alias(alias_kinds, operation.get("selection_alias"), "selection")
            capacity += 1
        elif step_type == "attribute_transfer":
            _require_alias(alias_kinds, step.get("source_target_alias"), "target")
            _require_alias(alias_kinds, step.get("target_alias"), "target")
            transfer = step.get("transfer")
            if not isinstance(transfer, dict):
                _batch_error("MESH_BATCH_INVALID", "attribute_transfer requires transfer")
            _require_alias(alias_kinds, transfer.get("target_selection_alias"), "selection")
            capacity += 1
        elif step_type == "mesh_validate":
            _require_alias(alias_kinds, step.get("selection_alias"), "selection")
            if step.get("surface_alias") is not None:
                _require_alias(alias_kinds, step.get("surface_alias"), "surface")
            _reserve(alias_kinds, step.get("output_alias"), "validation")
        else:
            _batch_error("MESH_BATCH_INVALID", f"Unsupported step type: {step_type}")
    if topology_steps > MAX_TOPOLOGY_STEPS:
        _batch_error("MESH_BATCH_BUDGET_EXCEEDED", "A batch may contain at most 8 topology steps")
    transaction.ensure_capacity(capacity)
    return {
        "targets": targets,
        "selections": input_selections,
        "surfaces": input_surfaces,
        "alias_kinds": alias_kinds,
        "topology_steps": topology_steps,
        "reserved_deltas": capacity,
    }


def _derive_operation(
    operation: dict[str, Any], selections: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = {
        key: value
        for key, value in operation.items()
        if key not in {"selection_alias", "selection_aliases"}
    }
    if operation.get("type") == "combine":
        result["selection_ids"] = [
            selections[str(alias)]["selection_id"] for alias in operation["selection_aliases"]
        ]
    else:
        result["selection_id"] = selections[str(operation["selection_alias"])]["selection_id"]
    return result


def _edit_operation(
    operation: dict[str, Any],
    selections: dict[str, dict[str, Any]],
    surfaces: dict[str, str],
) -> dict[str, Any]:
    result = {
        key: value
        for key, value in operation.items()
        if key not in {"selection_alias", "surface_alias"}
    }
    result["selection_id"] = selections[str(operation["selection_alias"])]["selection_id"]
    if operation.get("surface_alias") is not None:
        result["surface_id"] = surfaces[str(operation["surface_alias"])]
    return result


def _attribute_operation(
    operation: dict[str, Any], selections: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = {key: value for key, value in operation.items() if key != "selection_alias"}
    if operation.get("selection_alias") is not None:
        result["selection_id"] = selections[str(operation["selection_alias"])]["selection_id"]
    return result


def _transfer_operation(
    transfer: dict[str, Any], selections: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = {key: value for key, value in transfer.items() if key != "target_selection_alias"}
    result["target_selection_id"] = selections[str(transfer["target_selection_alias"])][
        "selection_id"
    ]
    return result


def _rebind_target_selections_same_topology(
    book: MeshResourceBook,
    selections: dict[str, dict[str, Any]],
    target_alias: str,
    obj: Any,
) -> list[dict[str, Any]]:
    mesh = obj.data
    reports = []
    for alias, binding in selections.items():
        if binding["target_alias"] != target_alias:
            continue
        source = book.selection(binding["selection_id"])
        rebound = book.add_selection(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            mesh_name=mesh.name,
            mesh_identity=session_identity("mesh", mesh),
            mesh_revision_id=mesh_revision_id(mesh),
            mesh_fingerprint=mesh_fingerprint(mesh),
            expected_users=int(mesh.users),
            expected_user_objects=mesh_user_refs(mesh),
            domain=source.domain,
            indices=source.indices,
            weights=source.weights,
            source_query={
                "type": "batch_attribute_rebound",
                "source": source.selection_id,
            },
        )
        binding["selection_id"] = rebound.selection_id
        reports.append({"alias": alias, "selection": rebound.summary()})
    return reports


def _remap_target_selections(
    book: MeshResourceBook,
    selections: dict[str, dict[str, Any]],
    target_alias: str,
    component_map_id: str,
) -> list[dict[str, Any]]:
    reports = []
    for alias, binding in selections.items():
        if binding["target_alias"] != target_alias:
            continue
        result = remap_selection(
            book,
            {
                "selection_id": binding["selection_id"],
                "component_map_id": component_map_id,
                "mode": binding["remap_mode"],
                "weight_merge": binding["weight_merge"],
            },
        )
        binding["selection_id"] = result["selection"]["selection_id"]
        reports.append({"alias": alias, **result})
    return reports


def _append_map(
    book: MeshResourceBook,
    target_state: dict[str, Any],
    component_map_id: str,
) -> dict[str, Any] | None:
    target_state["direct_component_map_ids"].append(component_map_id)
    previous = target_state.get("composed_component_map_id")
    ids = (
        [previous, component_map_id] if previous else target_state["direct_component_map_ids"][-2:]
    )
    if len(ids) < 2:
        return None
    result = compose_component_map(book, {"component_map_ids": ids})
    target_state["composed_component_map_id"] = result["component_map"]["component_map_id"]
    target_state["composed_component_map"] = result["component_map"]
    return result["component_map"]


def _assert_validation(result: dict[str, Any], assertions: list[dict[str, Any]]) -> None:
    distances = result.get("distances") if isinstance(result.get("distances"), dict) else {}
    for assertion in assertions:
        kind = assertion.get("type")
        expected = assertion.get("value")
        if kind == "count_at_most":
            actual = result.get("count")
            if actual is None:
                actual = distances.get("count")
            passed = isinstance(actual, int) and actual <= int(expected)
        elif kind == "p95_at_most":
            actual = distances.get("p95")
            passed = actual is not None and float(actual) <= float(expected)
        elif kind == "maximum_at_most":
            actual = distances.get("maximum")
            passed = actual is not None and float(actual) <= float(expected)
        elif kind == "penetration_at_most":
            signed_minimum = distances.get("signed_minimum")
            actual = max(0.0, -float(signed_minimum)) if signed_minimum is not None else None
            passed = actual is not None and actual <= float(expected)
        else:
            actual = result.get("sign_reliable")
            passed = bool(actual) is bool(expected)
        if not passed:
            _batch_error(
                "MESH_BATCH_ASSERTION_FAILED",
                f"Batch validation assertion failed: {kind}",
                details={"assertion": assertion, "actual": actual},
            )


def _step_aliases(step: dict[str, Any]) -> tuple[str, ...]:
    values = []
    for key, value in step.items():
        if key.endswith("alias") and isinstance(value, str):
            values.append(value)
        elif key.endswith("aliases") and isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    return tuple(sorted(set(values)))


def execute_mesh_batch(
    transaction: Transaction,
    book: MeshResourceBook,
    captures: CaptureBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    batch_id = str(uuid.uuid4())
    prepared = _preflight(transaction, book, params)
    targets: dict[str, dict[str, Any]] = prepared["targets"]
    selections: dict[str, dict[str, Any]] = prepared["selections"]
    surfaces: dict[str, str] = prepared["surfaces"]
    maps: dict[str, str] = {}
    validations: dict[str, dict[str, Any]] = {}
    branches = {
        alias: {
            "direct_component_map_ids": [],
            "composed_component_map_id": None,
            "composed_component_map": None,
        }
        for alias in targets
    }
    step_reports: list[dict[str, Any]] = []
    changed = False

    for index, step in enumerate(params["steps"]):
        step_type = str(step["type"])
        try:
            if step_type == "selection_query":
                target_alias = str(step["target_alias"])
                target = targets[target_alias]
                result = query_selection(
                    book,
                    captures,
                    {
                        **_target_params(target),
                        "expected_mesh_revision_id": target["mesh_revision_id"],
                        "domain": step["domain"],
                        "query": step["query"],
                    },
                )
                selections[str(step["output_alias"])] = {
                    "selection_id": result["selection_id"],
                    "target_alias": target_alias,
                    "remap_mode": step.get("remap_mode", "ALL_MAPPED"),
                    "weight_merge": step.get("weight_merge", "MAX"),
                }
                report = result
            elif step_type == "selection_derive":
                operation = _derive_operation(step["operation"], selections)
                result = derive_selection(book, {"operation": operation})
                source_alias = _selection_refs(step["operation"])[0]
                source = selections[source_alias]
                selections[str(step["output_alias"])] = {
                    "selection_id": result["selection_id"],
                    "target_alias": source["target_alias"],
                    "remap_mode": step.get("remap_mode", "ALL_MAPPED"),
                    "weight_merge": step.get("weight_merge", "MAX"),
                }
                report = result
            elif step_type == "mesh_edit":
                target_alias = str(step["target_alias"])
                operation = _edit_operation(step["operation"], selections, surfaces)
                selection_alias = str(step["operation"]["selection_alias"])
                if selections[selection_alias]["target_alias"] != target_alias:
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                call_params = {
                    **_target_params(targets[target_alias], str(step["data_scope"])),
                    "transaction_id": transaction.transaction_id,
                    "operation": operation,
                }
                operation_type = operation["type"]
                if operation_type in DEFORM_OPERATIONS:
                    result = edit_mesh_deform(transaction, book, captures, call_params)
                elif operation_type in TOPOLOGY_OPERATIONS:
                    result = edit_mesh_topology(transaction, book, call_params)
                else:
                    _batch_error("MESH_BATCH_INVALID", f"Unsupported batch edit: {operation_type}")
                changed = changed or bool(result.get("changed"))
                obj = bpy.data.objects.get(targets[target_alias]["object_name"])
                if obj is None:
                    _batch_error("MESH_BATCH_TARGET_MISMATCH", "Edited target disappeared")
                targets[target_alias] = _live_target(target_alias, obj)
                component_map = result.get("component_map")
                remaps: list[dict[str, Any]] = []
                composed = None
                if isinstance(component_map, dict):
                    component_map_id = str(component_map["component_map_id"])
                    remaps = _remap_target_selections(
                        book, selections, target_alias, component_map_id
                    )
                    composed = _append_map(book, branches[target_alias], component_map_id)
                    if step.get("map_alias") is not None:
                        maps[str(step["map_alias"])] = component_map_id
                elif result.get("rebound_selection") is not None:
                    selections[selection_alias]["selection_id"] = result["rebound_selection"][
                        "selection_id"
                    ]
                created_aliases = step.get("created_selection_aliases") or {}
                created = result.get("created_selections") or {}
                for domain_key, alias in created_aliases.items():
                    domain = {"vertex": "VERTEX", "edge": "EDGE", "face": "FACE"}[domain_key]
                    if alias is not None:
                        if domain not in created:
                            _batch_error(
                                "MESH_BATCH_REFERENCE_NOT_FOUND",
                                f"Mesh edit did not create a {domain} SelectionSet",
                            )
                        selections[str(alias)] = {
                            "selection_id": created[domain]["selection_id"],
                            "target_alias": target_alias,
                            "remap_mode": "ALL_MAPPED",
                            "weight_merge": "MAX",
                        }
                report = {**result, "automatic_remaps": remaps, "composed_component_map": composed}
            elif step_type == "mesh_separate":
                target_alias = str(step["target_alias"])
                selection_alias = str(step["selection_alias"])
                if selections[selection_alias]["target_alias"] != target_alias:
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                call_params = {
                    **_target_params(targets[target_alias]),
                    "transaction_id": transaction.transaction_id,
                    "selection_id": selections[selection_alias]["selection_id"],
                    "new_object_name": step["new_object_name"],
                    "collection_name": step.get("collection_name"),
                    "expected_collection_identity": step.get("expected_collection_identity"),
                    "source_attribute_policy": step.get("source_attribute_policy", {}),
                    "separated_attribute_policy": step.get("separated_attribute_policy", {}),
                }
                result = separate_mesh(transaction, book, call_params)
                changed = True
                source_map = result["source_component_map"]
                separated_map = result["separated_component_map"]
                source_map_id = str(source_map["component_map_id"])
                separated_map_id = str(separated_map["component_map_id"])
                source_remaps = _remap_target_selections(
                    book, selections, target_alias, source_map_id
                )
                source_obj = bpy.data.objects.get(result["source_object"]["name"])
                separated_obj = bpy.data.objects.get(result["separated_object"]["name"])
                if source_obj is None or separated_obj is None:
                    _batch_error("MESH_SEPARATION_FAILED", "Separated branch objects disappeared")
                targets[target_alias] = _live_target(target_alias, source_obj)
                new_target_alias = str(step["new_target_alias"])
                targets[new_target_alias] = _live_target(new_target_alias, separated_obj)
                prior_chain = list(branches[target_alias]["direct_component_map_ids"])
                branches.setdefault(
                    new_target_alias,
                    {
                        "direct_component_map_ids": prior_chain,
                        "composed_component_map_id": branches[target_alias].get(
                            "composed_component_map_id"
                        ),
                        "composed_component_map": branches[target_alias].get(
                            "composed_component_map"
                        ),
                    },
                )
                source_composed = _append_map(book, branches[target_alias], source_map_id)
                separated_composed = _append_map(book, branches[new_target_alias], separated_map_id)
                maps[str(step["source_map_alias"])] = source_map_id
                maps[str(step["separated_map_alias"])] = separated_map_id
                selections[str(step["new_selection_alias"])] = {
                    "selection_id": result["separated_selection"]["selection_id"],
                    "target_alias": new_target_alias,
                    "remap_mode": "ALL_MAPPED",
                    "weight_merge": "MAX",
                }
                report = {
                    **result,
                    "automatic_source_remaps": source_remaps,
                    "source_composed_component_map": source_composed,
                    "separated_composed_component_map": separated_composed,
                }
            elif step_type == "uv_edit":
                target_alias = str(step["target_alias"])
                operation = _attribute_operation(step["operation"], selections)
                selection_alias = step["operation"].get("selection_alias")
                if selection_alias is not None and (
                    selections[str(selection_alias)]["target_alias"] != target_alias
                ):
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                target = targets[target_alias]
                call_params = {
                    **_target_params(target, str(step["data_scope"])),
                    "transaction_id": transaction.transaction_id,
                    "expected_uv_fingerprint": target["uv_fingerprint"],
                    "operation": operation,
                }
                result = edit_uv(transaction, book, call_params)
                changed = changed or bool(result.get("changed"))
                obj = bpy.data.objects.get(target["object_name"])
                if obj is None:
                    _batch_error("MESH_BATCH_TARGET_MISMATCH", "UV target disappeared")
                targets[target_alias] = _live_target(target_alias, obj)
                remaps = (
                    _rebind_target_selections_same_topology(book, selections, target_alias, obj)
                    if result.get("changed")
                    else []
                )
                report = {**result, "automatic_rebinds": remaps}
            elif step_type == "weights_edit":
                target_alias = str(step["target_alias"])
                operation = _attribute_operation(step["operation"], selections)
                selection_alias = step["operation"].get("selection_alias")
                if selection_alias is not None and (
                    selections[str(selection_alias)]["target_alias"] != target_alias
                ):
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                target = targets[target_alias]
                call_params = {
                    **_attribute_target(target, data_scope=str(step["data_scope"])),
                    "transaction_id": transaction.transaction_id,
                    "operation": operation,
                }
                result = edit_weights(transaction, book, call_params)
                changed = changed or bool(result.get("changed"))
                obj = bpy.data.objects.get(target["object_name"])
                if obj is None:
                    _batch_error("MESH_BATCH_TARGET_MISMATCH", "Weight target disappeared")
                targets[target_alias] = _live_target(target_alias, obj)
                report = result
            elif step_type == "attribute_transfer":
                source_alias = str(step["source_target_alias"])
                target_alias = str(step["target_alias"])
                transfer = _transfer_operation(step["transfer"], selections)
                selection_alias = str(step["transfer"]["target_selection_alias"])
                if selections[selection_alias]["target_alias"] != target_alias:
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                source = targets[source_alias]
                target = targets[target_alias]
                result = transfer_attribute(
                    transaction,
                    book,
                    {
                        "transaction_id": transaction.transaction_id,
                        "source": _attribute_target(source),
                        "target": _attribute_target(
                            target, data_scope=str(step["target_data_scope"])
                        ),
                        "transfer": transfer,
                    },
                )
                changed = changed or bool(result.get("changed"))
                obj = bpy.data.objects.get(target["object_name"])
                if obj is None:
                    _batch_error("MESH_BATCH_TARGET_MISMATCH", "Attribute target disappeared")
                targets[target_alias] = _live_target(target_alias, obj)
                remaps = (
                    _rebind_target_selections_same_topology(book, selections, target_alias, obj)
                    if result.get("changed") and transfer["type"] == "UV"
                    else []
                )
                report = {**result, "automatic_rebinds": remaps}
            else:
                selection_alias = str(step["selection_alias"])
                target_alias = selections[selection_alias]["target_alias"]
                target = targets[target_alias]
                surface_alias = step.get("surface_alias")
                validate_params = {
                    "selection_id": selections[selection_alias]["selection_id"],
                    "check": step["check"],
                    "surface_id": (
                        surfaces[str(surface_alias)] if surface_alias is not None else None
                    ),
                    "tolerance": step.get("tolerance", 1e-6),
                    "maximum_distance": step.get("maximum_distance", 1_000_000),
                    "threshold": step.get("threshold"),
                    "sample_limit": step.get("sample_limit", 64),
                    "group_names": step.get("group_names"),
                    "expected_group_schema_fingerprint": target["group_schema_fingerprint"],
                    "expected_weights_fingerprint": target["weights_fingerprint"],
                    "target_total": step.get("target_total", 1.0),
                    "maximum_influences": step.get("maximum_influences", 4),
                }
                result = validate_mesh(book, validate_params)
                _assert_validation(result, step.get("assertions", []))
                validations[str(step["output_alias"])] = result
                report = result
            step_reports.append(
                {"index": index, "type": step_type, "status": "succeeded", "result": report}
            )
        except (MeshOperationError, MeshResourceError) as exc:
            raise MeshBatchExecutionError(
                cause=exc,
                batch_id=batch_id,
                step_index=index,
                step_type=step_type,
                aliases=_step_aliases(step),
            ) from exc
        except Exception as exc:
            wrapped = MeshOperationError(
                "MESH_BATCH_INVALID",
                f"Batch step failed: {type(exc).__name__}",
                kind="blender_api",
                details={"error_type": type(exc).__name__, "message": str(exc)},
            )
            raise MeshBatchExecutionError(
                cause=wrapped,
                batch_id=batch_id,
                step_index=index,
                step_type=step_type,
                aliases=_step_aliases(step),
            ) from exc

    final_aliases: dict[str, Any] = {}
    final_aliases.update(
        {alias: {"kind": "target", **target} for alias, target in sorted(targets.items())}
    )
    final_aliases.update(
        {alias: {"kind": "selection", **binding} for alias, binding in sorted(selections.items())}
    )
    final_aliases.update(
        {
            alias: {"kind": "surface", "surface_id": value}
            for alias, value in sorted(surfaces.items())
        }
    )
    final_aliases.update(
        {
            alias: {"kind": "component_map", "component_map_id": value}
            for alias, value in sorted(maps.items())
        }
    )
    final_aliases.update(
        {
            alias: {"kind": "validation", "result": value}
            for alias, value in sorted(validations.items())
        }
    )
    return {
        "transaction_id": transaction.transaction_id,
        "batch_id": batch_id,
        "changed": changed,
        "step_reports": step_reports,
        "aliases": final_aliases,
        "target_branches": branches,
        "preflight": {
            "target_count": len(prepared["targets"]),
            "step_count": len(params["steps"]),
            "alias_count": len(prepared["alias_kinds"]),
            "topology_steps": prepared["topology_steps"],
            "reserved_deltas": prepared["reserved_deltas"],
        },
    }
