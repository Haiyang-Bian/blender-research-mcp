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
    map_kind: str = "SINGLE"
    source_component_map_ids: tuple[str, ...] = ()
    step_count: int = 1
    transaction_ids: tuple[str, ...] = ()
    separation_id: str | None = None
    branch_role: str | None = None

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
            "transaction_ids": list(self.transaction_ids or (self.transaction_id,)),
            "operation": self.operation,
            "map_kind": self.map_kind,
            "source_component_map_ids": list(self.source_component_map_ids),
            "step_count": self.step_count,
            "separation_id": self.separation_id,
            "branch_role": self.branch_role,
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
    map_kind: str = "SINGLE",
    source_component_map_ids: tuple[str, ...] = (),
    step_count: int = 1,
    transaction_ids: tuple[str, ...] | None = None,
    separation_id: str | None = None,
    branch_role: str | None = None,
) -> ComponentMapRecord:
    if map_kind not in {"SINGLE", "COMPOSED", "SEPARATION_BRANCH"}:
        raise ValueError(f"Unsupported ComponentMap kind: {map_kind}")
    if step_count < 1:
        raise ValueError("ComponentMap step_count must be positive")
    resolved_transaction_ids = transaction_ids or (transaction_id,)
    payload = {
        "transaction_id": transaction_id,
        "transaction_ids": resolved_transaction_ids,
        "operation": operation,
        "map_kind": map_kind,
        "source_component_map_ids": source_component_map_ids,
        "step_count": step_count,
        "separation_id": separation_id,
        "branch_role": branch_role,
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
        map_kind=map_kind,
        source_component_map_ids=source_component_map_ids,
        step_count=step_count,
        transaction_ids=resolved_transaction_ids,
        separation_id=separation_id,
        branch_role=branch_role,
    )


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _endpoint(record: ComponentMapRecord, side: str) -> tuple[str, ...]:
    return (
        str(getattr(record, f"{side}_object_name")),
        str(getattr(record, f"{side}_object_identity")),
        str(getattr(record, f"{side}_mesh_name")),
        str(getattr(record, f"{side}_mesh_identity")),
        str(getattr(record, f"{side}_mesh_revision_id")),
        str(getattr(record, f"{side}_mesh_fingerprint")),
    )


def component_map_chain_mismatch(
    records: tuple[ComponentMapRecord, ...],
) -> tuple[int, tuple[str, ...], tuple[str, ...]] | None:
    for index, (left, right) in enumerate(zip(records, records[1:], strict=False)):
        left_after = _endpoint(left, "after")
        right_before = _endpoint(right, "before")
        if left_after != right_before:
            return index, left_after, right_before
    return None


def _compose_domain(
    records: tuple[ComponentMapRecord, ...],
    domain: str,
) -> tuple[tuple[ComponentRelation, ...], tuple[int, ...], tuple[int, ...]]:
    first = records[0]
    first_rows = first.relations.get(domain, ())
    mapping: dict[int, set[int]] = {
        row.source_index: set(row.target_indices) for row in first_rows
    }
    for deleted_index in first.deleted.get(domain, ()):
        mapping.setdefault(deleted_index, set())

    histories: dict[int, set[str]] = {
        row.source_index: {row.relation} for row in first_rows
    }
    ancestorless = set(first.created.get(domain, ()))

    for record in records[1:]:
        rows = {row.source_index: row for row in record.relations.get(domain, ())}
        next_mapping: dict[int, set[int]] = {}
        next_histories: dict[int, set[str]] = {}
        for source, intermediate_indices in mapping.items():
            targets: set[int] = set()
            history = set(histories.get(source, ()))
            for intermediate in intermediate_indices:
                row = rows.get(intermediate)
                if row is not None:
                    targets.update(row.target_indices)
                    history.add(row.relation)
            next_mapping[source] = targets
            next_histories[source] = history
        mapping = next_mapping
        histories = next_histories

        propagated_created: set[int] = set()
        for intermediate in ancestorless:
            row = rows.get(intermediate)
            if row is not None:
                propagated_created.update(row.target_indices)
        propagated_created.update(record.created.get(domain, ()))
        ancestorless = propagated_created

    reverse_sources: dict[int, set[int]] = {}
    for source, targets in mapping.items():
        for target in targets:
            reverse_sources.setdefault(target, set()).add(source)

    relations: list[ComponentRelation] = []
    deleted: list[int] = []
    for source in sorted(mapping):
        targets = tuple(sorted(mapping[source]))
        if not targets:
            deleted.append(source)
            continue
        has_split = len(targets) > 1
        has_merge = any(len(reverse_sources[target]) > 1 for target in targets)
        history = histories.get(source, set())
        complex_history = "DERIVED" in history or (
            "SPLIT" in history and "MERGED" in history
        )
        if complex_history or (has_split and has_merge):
            relation = "DERIVED"
        elif has_split:
            relation = "SPLIT"
        elif has_merge:
            relation = "MERGED"
        elif history == {"SURVIVED"}:
            relation = "SURVIVED"
        else:
            relation = "DERIVED"
        relations.append(ComponentRelation(source, targets, relation))

    original_targets = {target for targets in mapping.values() for target in targets}
    created = tuple(sorted(ancestorless - original_targets))
    return tuple(relations), created, tuple(deleted)


def compose_component_maps(
    records: tuple[ComponentMapRecord, ...],
) -> ComponentMapRecord:
    if not 2 <= len(records) <= 8:
        raise ValueError("ComponentMap composition requires 2 to 8 maps")
    mismatch = component_map_chain_mismatch(records)
    if mismatch is not None:
        index, left_after, right_before = mismatch
        raise ValueError(
            f"ComponentMap chain breaks between positions {index} and {index + 1}: "
            f"{left_after!r} != {right_before!r}"
        )

    relations: dict[str, tuple[ComponentRelation, ...]] = {}
    created: dict[str, tuple[int, ...]] = {}
    deleted: dict[str, tuple[int, ...]] = {}
    for domain in DOMAINS:
        domain_relations, domain_created, domain_deleted = _compose_domain(records, domain)
        relations[domain] = domain_relations
        created[domain] = domain_created
        deleted[domain] = domain_deleted

    first = records[0]
    last = records[-1]
    transaction_ids = _ordered_unique(
        tuple(
            transaction_id
            for record in records
            for transaction_id in (record.transaction_ids or (record.transaction_id,))
        )
    )
    return make_component_map(
        transaction_id=transaction_ids[0],
        transaction_ids=transaction_ids,
        operation="compose",
        before={
            "object_name": first.before_object_name,
            "object_identity": first.before_object_identity,
            "mesh_name": first.before_mesh_name,
            "mesh_identity": first.before_mesh_identity,
            "mesh_revision_id": first.before_mesh_revision_id,
            "mesh_fingerprint": first.before_mesh_fingerprint,
        },
        after={
            "object_name": last.after_object_name,
            "object_identity": last.after_object_identity,
            "mesh_name": last.after_mesh_name,
            "mesh_identity": last.after_mesh_identity,
            "mesh_revision_id": last.after_mesh_revision_id,
            "mesh_fingerprint": last.after_mesh_fingerprint,
        },
        after_users=last.after_users,
        after_user_objects=last.after_user_objects,
        relations=relations,
        created=created,
        deleted=deleted,
        map_kind="COMPOSED",
        source_component_map_ids=tuple(record.component_map_id for record in records),
        step_count=sum(record.step_count for record in records),
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
