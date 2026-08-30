"""One-revision ComponentMap records and SelectionSet remapping."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

import bpy

from .mesh_ops import mesh_fingerprint, mesh_revision_id, mesh_user_refs
from .mesh_resource_model import MeshResourceBook, MeshResourceError, SelectionRecord
from .structural_ops import session_identity

DOMAINS = ("VERTEX", "EDGE", "FACE")
RELATIONS = {"SURVIVED", "SPLIT", "MERGED", "DERIVED"}


def _content_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ComponentRelation:
    source_index: int
    target_indices: tuple[int, ...]
    relation: str

    def __post_init__(self) -> None:
        if self.source_index < 0 or tuple(sorted(set(self.target_indices))) != self.target_indices:
            raise ValueError("Component relations require non-negative sorted unique indices")
        if self.relation not in RELATIONS:
            raise ValueError(f"Unsupported component relation: {self.relation}")


@dataclass(frozen=True)
class ComponentMapRecord:
    component_map_id: str
    transaction_id: str
    operation: str
    before_object_name: str
    before_object_identity: str
    before_mesh_name: str
    before_mesh_identity: str
    before_mesh_revision_id: str
    before_mesh_fingerprint: str
    after_object_name: str
    after_object_identity: str
    after_mesh_name: str
    after_mesh_identity: str
    after_mesh_revision_id: str
    after_mesh_fingerprint: str
    after_users: int
    after_user_objects: tuple[tuple[str, str], ...]
    relations: dict[str, tuple[ComponentRelation, ...]]
    created: dict[str, tuple[int, ...]]
    deleted: dict[str, tuple[int, ...]]
    content_sha256: str

    @property
    def relation_count(self) -> int:
        return sum(
            max(1, len(relation.target_indices))
            for domain in DOMAINS
            for relation in self.relations.get(domain, ())
        )

    def _domain_summary(self, domain: str) -> dict[str, int]:
        rows = self.relations.get(domain, ())
        return {
            "survived": sum(item.relation == "SURVIVED" for item in rows),
            "split": sum(item.relation == "SPLIT" for item in rows),
            "merged": sum(item.relation == "MERGED" for item in rows),
            "derived": sum(item.relation == "DERIVED" for item in rows),
            "created": len(self.created.get(domain, ())),
            "deleted": len(self.deleted.get(domain, ())),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "component_map_id": self.component_map_id,
            "transaction_id": self.transaction_id,
            "operation": self.operation,
            "before": {
                "object_name": self.before_object_name,
                "object_identity": self.before_object_identity,
                "mesh_name": self.before_mesh_name,
                "mesh_identity": self.before_mesh_identity,
                "mesh_revision_id": self.before_mesh_revision_id,
                "mesh_fingerprint": self.before_mesh_fingerprint,
            },
            "after": {
                "object_name": self.after_object_name,
                "object_identity": self.after_object_identity,
                "mesh_name": self.after_mesh_name,
                "mesh_identity": self.after_mesh_identity,
                "mesh_revision_id": self.after_mesh_revision_id,
                "mesh_fingerprint": self.after_mesh_fingerprint,
            },
            "domains": {domain: self._domain_summary(domain) for domain in DOMAINS},
            "relation_count": self.relation_count,
            "content_sha256": self.content_sha256,
        }


def make_component_map(
    *,
    transaction_id: str,
    operation: str,
    before: dict[str, Any],
    after: dict[str, Any],
    after_users: int,
    after_user_objects: tuple[tuple[str, str], ...],
    relations: dict[str, tuple[ComponentRelation, ...]],
    created: dict[str, tuple[int, ...]],
    deleted: dict[str, tuple[int, ...]],
) -> ComponentMapRecord:
    payload = {
        "transaction_id": transaction_id,
        "operation": operation,
        "before": before,
        "after": after,
        "relations": {
            domain: [
                [item.source_index, list(item.target_indices), item.relation]
                for item in relations.get(domain, ())
            ]
            for domain in DOMAINS
        },
        "created": {domain: list(created.get(domain, ())) for domain in DOMAINS},
        "deleted": {domain: list(deleted.get(domain, ())) for domain in DOMAINS},
    }
    return ComponentMapRecord(
        component_map_id=str(uuid.uuid4()),
        transaction_id=transaction_id,
        operation=operation,
        before_object_name=str(before["object_name"]),
        before_object_identity=str(before["object_identity"]),
        before_mesh_name=str(before["mesh_name"]),
        before_mesh_identity=str(before["mesh_identity"]),
        before_mesh_revision_id=str(before["mesh_revision_id"]),
        before_mesh_fingerprint=str(before["mesh_fingerprint"]),
        after_object_name=str(after["object_name"]),
        after_object_identity=str(after["object_identity"]),
        after_mesh_name=str(after["mesh_name"]),
        after_mesh_identity=str(after["mesh_identity"]),
        after_mesh_revision_id=str(after["mesh_revision_id"]),
        after_mesh_fingerprint=str(after["mesh_fingerprint"]),
        after_users=after_users,
        after_user_objects=after_user_objects,
        relations=relations,
        created=created,
        deleted=deleted,
        content_sha256=_content_hash(payload),
    )


def _page(items: tuple[Any, ...], offset: int, limit: int) -> tuple[Any, ...]:
    return items[offset : offset + limit]


def inspect_component_map(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    record = book.component_map(str(params.get("component_map_id", "")))
    domain = str(params.get("domain", "SUMMARY"))
    direction = str(params.get("direction", "FORWARD"))
    offset = int(params.get("offset", 0))
    limit = int(params.get("limit", 256))
    if domain == "SUMMARY":
        return {"component_map": record.summary(), "page": None}
    if domain not in DOMAINS or direction not in {"FORWARD", "REVERSE", "CREATED", "DELETED"}:
        raise MeshResourceError("MESH_COMPONENT_MAP_INVALID", "Invalid map domain or direction")
    if offset < 0 or not 1 <= limit <= 4096:
        raise MeshResourceError("MESH_COMPONENT_MAP_INVALID", "Invalid map page")

    if direction == "FORWARD":
        values: tuple[Any, ...] = tuple(
            {
                "source_index": item.source_index,
                "target_indices": list(item.target_indices),
                "relation": item.relation,
            }
            for item in record.relations.get(domain, ())
        )
    elif direction == "REVERSE":
        reverse: dict[int, list[int]] = {}
        for item in record.relations.get(domain, ()):
            for target in item.target_indices:
                reverse.setdefault(target, []).append(item.source_index)
        values = tuple(
            {
                "target_index": target,
                "source_indices": sorted(sources),
                "relation": "MERGED" if len(sources) > 1 else "DERIVED",
            }
            for target, sources in sorted(reverse.items())
        )
    elif direction == "CREATED":
        values = tuple(record.created.get(domain, ()))
    else:
        values = tuple(record.deleted.get(domain, ()))
    page = _page(values, offset, limit)
    return {
        "component_map": record.summary(),
        "domain": domain,
        "direction": direction,
        "items": list(page),
        "pagination": {
            "offset": offset,
            "limit": limit,
            "returned": len(page),
            "total": len(values),
            "truncated": offset + len(page) < len(values),
        },
    }


def release_component_map(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    component_map_id = str(params.get("component_map_id", ""))
    return {
        "component_map_id": component_map_id,
        "released": book.release_component_map(component_map_id),
    }


def _validate_live_after(record: ComponentMapRecord) -> tuple[Any, Any]:
    obj = bpy.data.objects.get(record.after_object_name)
    if obj is None or session_identity("object", obj) != record.after_object_identity:
        raise MeshResourceError(
            "MESH_COMPONENT_MAP_STALE",
            "ComponentMap after-object no longer exists",
            kind="conflict",
        )
    mesh = getattr(obj, "data", None)
    if (
        mesh is None
        or session_identity("mesh", mesh) != record.after_mesh_identity
        or mesh_fingerprint(mesh) != record.after_mesh_fingerprint
        or mesh_revision_id(mesh) != record.after_mesh_revision_id
        or int(mesh.users) != record.after_users
        or mesh_user_refs(mesh) != record.after_user_objects
    ):
        raise MeshResourceError(
            "MESH_COMPONENT_MAP_STALE",
            "ComponentMap after-revision no longer matches the live Mesh",
            kind="conflict",
        )
    return obj, mesh


def remap_selection(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    selection: SelectionRecord = book.selection(str(params.get("selection_id", "")))
    record = book.component_map(str(params.get("component_map_id", "")))
    mode = str(params.get("mode", "ALL_MAPPED"))
    weight_merge = str(params.get("weight_merge", "MAX"))
    if mode not in {"ALL_MAPPED", "EXACT_SURVIVORS", "STRICT"}:
        raise MeshResourceError("MESH_COMPONENT_MAP_INVALID", "Invalid remap mode")
    if weight_merge not in {"MAX", "AVERAGE"}:
        raise MeshResourceError("MESH_COMPONENT_MAP_INVALID", "Invalid weight merge mode")
    if (
        selection.object_identity != record.before_object_identity
        or selection.mesh_identity != record.before_mesh_identity
        or selection.mesh_revision_id != record.before_mesh_revision_id
        or selection.mesh_fingerprint != record.before_mesh_fingerprint
    ):
        raise MeshResourceError(
            "MESH_COMPONENT_MAP_REVISION_MISMATCH",
            "SelectionSet does not target the ComponentMap before-revision",
        )
    obj, mesh = _validate_live_after(record)
    by_source = {
        item.source_index: item for item in record.relations.get(selection.domain, ())
    }
    mapped: dict[int, list[float]] = {}
    missing: list[int] = []
    weights = selection.weights or tuple(1.0 for _index in selection.indices)
    for source, weight in zip(selection.indices, weights, strict=True):
        relation = by_source.get(source)
        if relation is None or not relation.target_indices:
            missing.append(source)
            continue
        if mode == "EXACT_SURVIVORS" and not (
            relation.relation == "SURVIVED" and len(relation.target_indices) == 1
        ):
            continue
        for target in relation.target_indices:
            mapped.setdefault(target, []).append(float(weight))
    if mode == "STRICT" and missing:
        raise MeshResourceError(
            "MESH_SELECTION_REMAP_INCOMPLETE",
            "Strict SelectionSet remap has unmapped components",
            details={"missing_count": len(missing), "missing_sample": missing[:64]},
        )
    indices = tuple(sorted(mapped))
    remapped_weights = None
    if selection.weights is not None:
        remapped_weights = tuple(
            max(mapped[index])
            if weight_merge == "MAX"
            else sum(mapped[index]) / len(mapped[index])
            for index in indices
        )
    rebound = book.add_selection(
        object_name=obj.name,
        object_identity=session_identity("object", obj),
        mesh_name=mesh.name,
        mesh_identity=session_identity("mesh", mesh),
        mesh_revision_id=mesh_revision_id(mesh),
        mesh_fingerprint=mesh_fingerprint(mesh),
        expected_users=int(mesh.users),
        expected_user_objects=mesh_user_refs(mesh),
        domain=selection.domain,
        indices=indices,
        weights=remapped_weights,
        source_query={
            "type": "component_map_remap",
            "selection_id": selection.selection_id,
            "component_map_id": record.component_map_id,
            "mode": mode,
            "weight_merge": weight_merge,
        },
    )
    return {
        "component_map": record.summary(),
        "source_selection": selection.summary(),
        "selection": rebound.summary(),
        "unmapped_count": len(missing),
        "unmapped_sample": missing[:64],
    }
