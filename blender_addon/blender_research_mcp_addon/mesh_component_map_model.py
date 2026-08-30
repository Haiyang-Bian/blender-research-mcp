"""Pure one-revision ComponentMap records and deterministic remap rules."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

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


def remap_relation_values(
    *,
    source_indices: tuple[int, ...],
    source_weights: tuple[float, ...] | None,
    relations: tuple[ComponentRelation, ...],
    mode: str,
    weight_merge: str,
) -> tuple[tuple[int, ...], tuple[float, ...] | None, tuple[int, ...]]:
    """Apply exact same-domain lineage without inspecting Blender state."""

    by_source = {item.source_index: item for item in relations}
    mapped: dict[int, list[float]] = {}
    missing: list[int] = []
    weights = source_weights or tuple(1.0 for _index in source_indices)
    for source, weight in zip(source_indices, weights, strict=True):
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
    indices = tuple(sorted(mapped))
    remapped_weights = None
    if source_weights is not None:
        remapped_weights = tuple(
            max(mapped[index])
            if weight_merge == "MAX"
            else sum(mapped[index]) / len(mapped[index])
            for index in indices
        )
    return indices, remapped_weights, tuple(missing)


def reverse_relation_values(
    relations: tuple[ComponentRelation, ...],
) -> tuple[dict[str, Any], ...]:
    reverse: dict[int, list[tuple[int, str]]] = {}
    for item in relations:
        for target in item.target_indices:
            reverse.setdefault(target, []).append((item.source_index, item.relation))
    return tuple(
        {
            "target_index": target,
            "source_indices": sorted(source for source, _relation in sources),
            "relation": "MERGED" if len(sources) > 1 else sources[0][1],
        }
        for target, sources in sorted(reverse.items())
    )
