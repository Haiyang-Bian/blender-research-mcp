"""Pure records and deterministic connectivity for FACE ComponentCatalog resources."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

CATALOG_METRICS = (
    "COUNT",
    "AREA",
    "BOUNDS",
    "MATERIALS",
    "BOUNDARY_COUNT",
)


def _content_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def connected_face_components(
    face_edges: tuple[tuple[int, ...], ...],
    selected_indices: tuple[int, ...],
) -> tuple[tuple[int, ...], ...]:
    """Return deterministic shared-edge connected components."""

    selected = set(selected_indices)
    if len(selected) != len(selected_indices) or tuple(sorted(selected)) != selected_indices:
        raise ValueError("selected face indices must be sorted and unique")
    if any(index < 0 or index >= len(face_edges) for index in selected_indices):
        raise ValueError("selected face index is outside the face domain")
    edge_faces: dict[int, list[int]] = {}
    for face_index in selected_indices:
        for edge_index in face_edges[face_index]:
            edge_faces.setdefault(edge_index, []).append(face_index)
    adjacency: dict[int, set[int]] = {index: set() for index in selected_indices}
    for faces in edge_faces.values():
        for face_index in faces:
            adjacency[face_index].update(other for other in faces if other != face_index)
    remaining = set(selected_indices)
    components: list[tuple[int, ...]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        reached = {seed}
        pending = deque([seed])
        while pending:
            face_index = pending.popleft()
            discovered = adjacency[face_index] & remaining
            remaining.difference_update(discovered)
            reached.update(discovered)
            pending.extend(sorted(discovered))
        components.append(tuple(sorted(reached)))
    return tuple(components)


def component_identity(
    mesh_revision_id: str,
    source_selection_sha256: str,
    face_indices: tuple[int, ...],
) -> str:
    digest = _content_hash(
        {
            "mesh_revision_id": mesh_revision_id,
            "source_selection_sha256": source_selection_sha256,
            "face_indices": face_indices,
        }
    )
    return f"component:{digest}"


@dataclass(frozen=True)
class ComponentCatalogItem:
    component_identity: str
    component_index: int
    face_indices: tuple[int, ...]
    area: float
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    boundary_count: int
    material_slots: tuple[int, ...]
    coverage_ratio: float

    def report(self, include: tuple[str, ...]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "component_identity": self.component_identity,
            "component_index": self.component_index,
            "coverage_ratio": self.coverage_ratio,
        }
        if "COUNT" in include:
            result["face_count"] = len(self.face_indices)
        if "AREA" in include:
            result["area"] = self.area
        if "BOUNDS" in include:
            result["bounds"] = {
                "space": "LOCAL",
                "minimum": list(self.bounds_min),
                "maximum": list(self.bounds_max),
            }
        if "BOUNDARY_COUNT" in include:
            result["boundary_edge_count"] = self.boundary_count
        if "MATERIALS" in include:
            result["material_slots"] = list(self.material_slots)
        return result


@dataclass(frozen=True)
class ComponentCatalogRecord:
    component_catalog_id: str
    object_name: str
    object_identity: str
    mesh_name: str
    mesh_identity: str
    mesh_revision_id: str
    mesh_fingerprint: str
    expected_users: int
    expected_user_objects: tuple[tuple[str, str], ...]
    source_selection_id: str
    source_selection_sha256: str
    source_indices: tuple[int, ...]
    source_weights: tuple[float, ...] | None
    include: tuple[str, ...]
    components: tuple[ComponentCatalogItem, ...]
    content_sha256: str

    @property
    def face_reference_count(self) -> int:
        return sum(len(component.face_indices) for component in self.components)

    def summary(self) -> dict[str, Any]:
        return {
            "component_catalog_id": self.component_catalog_id,
            "object_name": self.object_name,
            "object_identity": self.object_identity,
            "mesh_name": self.mesh_name,
            "mesh_identity": self.mesh_identity,
            "mesh_revision_id": self.mesh_revision_id,
            "source_selection_id": self.source_selection_id,
            "source_selection_sha256": self.source_selection_sha256,
            "source_face_count": len(self.source_indices),
            "component_count": len(self.components),
            "include": list(self.include),
            "content_sha256": self.content_sha256,
        }


def make_component_catalog(
    *,
    object_name: str,
    object_identity: str,
    mesh_name: str,
    mesh_identity: str,
    mesh_revision_id: str,
    mesh_fingerprint: str,
    expected_users: int,
    expected_user_objects: tuple[tuple[str, str], ...],
    source_selection_id: str,
    source_selection_sha256: str,
    source_indices: tuple[int, ...],
    source_weights: tuple[float, ...] | None,
    include: tuple[str, ...],
    component_metrics: tuple[
        tuple[
            tuple[int, ...],
            float,
            tuple[float, float, float],
            tuple[float, float, float],
            int,
            tuple[int, ...],
        ],
        ...,
    ],
) -> ComponentCatalogRecord:
    items = tuple(
        ComponentCatalogItem(
            component_identity=component_identity(
                mesh_revision_id, source_selection_sha256, face_indices
            ),
            component_index=index,
            face_indices=face_indices,
            area=area,
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            boundary_count=boundary_count,
            material_slots=material_slots,
            coverage_ratio=len(face_indices) / len(source_indices),
        )
        for index, (
            face_indices,
            area,
            bounds_min,
            bounds_max,
            boundary_count,
            material_slots,
        ) in enumerate(component_metrics)
    )
    payload = {
        "object_identity": object_identity,
        "mesh_identity": mesh_identity,
        "mesh_revision_id": mesh_revision_id,
        "mesh_fingerprint": mesh_fingerprint,
        "expected_users": expected_users,
        "expected_user_objects": expected_user_objects,
        "source_selection_sha256": source_selection_sha256,
        "include": include,
        "components": [
            {
                "identity": item.component_identity,
                "faces": item.face_indices,
                "area": item.area,
                "bounds_min": item.bounds_min,
                "bounds_max": item.bounds_max,
                "boundary_count": item.boundary_count,
                "material_slots": item.material_slots,
            }
            for item in items
        ],
    }
    return ComponentCatalogRecord(
        component_catalog_id=str(uuid.uuid4()),
        object_name=object_name,
        object_identity=object_identity,
        mesh_name=mesh_name,
        mesh_identity=mesh_identity,
        mesh_revision_id=mesh_revision_id,
        mesh_fingerprint=mesh_fingerprint,
        expected_users=expected_users,
        expected_user_objects=expected_user_objects,
        source_selection_id=source_selection_id,
        source_selection_sha256=source_selection_sha256,
        source_indices=source_indices,
        source_weights=source_weights,
        include=include,
        components=items,
        content_sha256=_content_hash(payload),
    )
