"""Bounded session-local SelectionSet and evaluated-surface resource books."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any

MAX_SELECTIONS = 192
MAX_SELECTION_COMPONENTS = 2_000_000
MAX_SURFACES = 8
MAX_SURFACE_TRIANGLES = 2_000_000
MAX_COMPONENT_MAPS = 128
MAX_COMPONENT_MAP_RELATIONS = 8_000_000
MAX_SINGLE_COMPONENT_MAP_RELATIONS = 4_000_000
MAX_COMPONENT_CATALOGS = 16
MAX_COMPONENT_CATALOG_FACE_REFERENCES = 2_000_000
MAX_COMPONENT_CATALOG_COMPONENTS = 500_000


class MeshResourceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: str = "validation",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.kind = kind
        self.details = details or {}


def selection_content_hash(
    revision_id: str,
    domain: str,
    indices: tuple[int, ...],
    weights: tuple[float, ...] | None,
) -> str:
    payload = {
        "revision_id": revision_id,
        "domain": domain,
        "indices": indices,
        "weights": weights,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SelectionRecord:
    selection_id: str
    object_name: str
    object_identity: str
    mesh_name: str
    mesh_identity: str
    mesh_revision_id: str
    mesh_fingerprint: str
    expected_users: int
    expected_user_objects: tuple[tuple[str, str], ...]
    domain: str
    indices: tuple[int, ...]
    weights: tuple[float, ...] | None
    source_query: dict[str, Any]
    content_sha256: str

    def summary(self) -> dict[str, Any]:
        weights = self.weights
        return {
            "selection_id": self.selection_id,
            "object_name": self.object_name,
            "object_identity": self.object_identity,
            "mesh_name": self.mesh_name,
            "mesh_identity": self.mesh_identity,
            "mesh_revision_id": self.mesh_revision_id,
            "domain": self.domain,
            "component_count": len(self.indices),
            "weighted": weights is not None,
            "weight_min": min(weights) if weights else None,
            "weight_max": max(weights) if weights else None,
            "content_sha256": self.content_sha256,
            "source_query": self.source_query,
        }


@dataclass(frozen=True)
class SurfaceRecord:
    surface_id: str
    scene: str
    view_layer: str
    frame: int
    object_name: str
    object_identity: str
    mesh_name: str
    mesh_identity: str
    mesh_revision_id: str
    geometry: str
    object_matrix: tuple[tuple[float, float, float, float], ...]
    evaluated_fingerprint: str
    vertex_count: int
    triangle_count: int
    closed_manifold: bool
    consistently_oriented: bool
    bvh: Any
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]

    @property
    def sign_reliable(self) -> bool:
        return self.closed_manifold and self.consistently_oriented

    def summary(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "scene": self.scene,
            "view_layer": self.view_layer,
            "frame": self.frame,
            "object_name": self.object_name,
            "object_identity": self.object_identity,
            "mesh_name": self.mesh_name,
            "mesh_identity": self.mesh_identity,
            "mesh_revision_id": self.mesh_revision_id,
            "geometry": self.geometry,
            "evaluated_fingerprint": self.evaluated_fingerprint,
            "vertex_count": self.vertex_count,
            "triangle_count": self.triangle_count,
            "closed_manifold": self.closed_manifold,
            "consistently_oriented": self.consistently_oriented,
            "sign_reliable": self.sign_reliable,
        }


class MeshResourceBook:
    def __init__(self) -> None:
        self._selections: OrderedDict[str, SelectionRecord] = OrderedDict()
        self._surfaces: OrderedDict[str, SurfaceRecord] = OrderedDict()
        self._component_maps: OrderedDict[str, Any] = OrderedDict()
        self._component_catalogs: OrderedDict[str, Any] = OrderedDict()
        self._expired: deque[str] = deque(maxlen=256)

    def clear(self) -> None:
        self._selections.clear()
        self._surfaces.clear()
        self._component_maps.clear()
        self._component_catalogs.clear()
        self._expired.clear()

    def add_selection(
        self,
        *,
        object_name: str,
        object_identity: str,
        mesh_name: str,
        mesh_identity: str,
        mesh_revision_id: str,
        mesh_fingerprint: str,
        expected_users: int,
        expected_user_objects: tuple[tuple[str, str], ...],
        domain: str,
        indices: tuple[int, ...],
        weights: tuple[float, ...] | None,
        source_query: dict[str, Any],
    ) -> SelectionRecord:
        if len(indices) > 500_000:
            raise MeshResourceError(
                "MESH_RESOURCE_BUDGET_EXCEEDED",
                "A SelectionSet may contain at most 500000 components",
            )
        if tuple(sorted(set(indices))) != indices:
            raise MeshResourceError(
                "MESH_RESOURCE_INVALID", "SelectionSet indices must be sorted and unique"
            )
        if weights is not None and len(weights) != len(indices):
            raise MeshResourceError(
                "MESH_RESOURCE_INVALID", "SelectionSet weights must match its indices"
            )
        selection_id = str(uuid.uuid4())
        record = SelectionRecord(
            selection_id=selection_id,
            object_name=object_name,
            object_identity=object_identity,
            mesh_name=mesh_name,
            mesh_identity=mesh_identity,
            mesh_revision_id=mesh_revision_id,
            mesh_fingerprint=mesh_fingerprint,
            expected_users=expected_users,
            expected_user_objects=expected_user_objects,
            domain=domain,
            indices=indices,
            weights=weights,
            source_query=source_query,
            content_sha256=selection_content_hash(mesh_revision_id, domain, indices, weights),
        )
        self._selections[selection_id] = record
        self._selections.move_to_end(selection_id)
        self._evict_selections()
        return record

    def _evict_selections(self) -> None:
        while len(self._selections) > MAX_SELECTIONS or sum(
            len(item.indices) for item in self._selections.values()
        ) > MAX_SELECTION_COMPONENTS:
            selection_id, _record = self._selections.popitem(last=False)
            self._expired.append(selection_id)

    def selection(self, selection_id: str) -> SelectionRecord:
        record = self._selections.get(selection_id)
        if record is None:
            if selection_id in self._expired:
                raise MeshResourceError(
                    "MESH_RESOURCE_EXPIRED",
                    f"SelectionSet was evicted: {selection_id}",
                    kind="not_found",
                )
            raise MeshResourceError(
                "MESH_RESOURCE_NOT_FOUND",
                f"SelectionSet does not exist: {selection_id}",
                kind="not_found",
            )
        self._selections.move_to_end(selection_id)
        return record

    def release_selection(self, selection_id: str) -> bool:
        return self._selections.pop(selection_id, None) is not None

    def add_surface(self, record: SurfaceRecord) -> SurfaceRecord:
        self._surfaces[record.surface_id] = record
        self._surfaces.move_to_end(record.surface_id)
        while len(self._surfaces) > MAX_SURFACES or sum(
            item.triangle_count for item in self._surfaces.values()
        ) > MAX_SURFACE_TRIANGLES:
            surface_id, _surface = self._surfaces.popitem(last=False)
            self._expired.append(surface_id)
        if record.surface_id not in self._surfaces:
            raise MeshResourceError(
                "MESH_RESOURCE_BUDGET_EXCEEDED",
                "Evaluated surface exceeds the retained triangle budget",
            )
        return record

    def surface(self, surface_id: str) -> SurfaceRecord:
        record = self._surfaces.get(surface_id)
        if record is None:
            if surface_id in self._expired:
                raise MeshResourceError(
                    "MESH_RESOURCE_EXPIRED",
                    f"SurfaceRef was evicted: {surface_id}",
                    kind="not_found",
                )
            raise MeshResourceError(
                "MESH_RESOURCE_NOT_FOUND",
                f"SurfaceRef does not exist: {surface_id}",
                kind="not_found",
            )
        self._surfaces.move_to_end(surface_id)
        return record

    def release_surface(self, surface_id: str) -> bool:
        return self._surfaces.pop(surface_id, None) is not None

    def add_component_map(self, record: Any) -> Any:
        if record.relation_count > MAX_SINGLE_COMPONENT_MAP_RELATIONS:
            raise MeshResourceError(
                "MESH_COMPONENT_MAP_BUDGET_EXCEEDED",
                "ComponentMap exceeds the per-resource relation budget",
            )
        self._component_maps[record.component_map_id] = record
        self._component_maps.move_to_end(record.component_map_id)
        while len(self._component_maps) > MAX_COMPONENT_MAPS or sum(
            item.relation_count for item in self._component_maps.values()
        ) > MAX_COMPONENT_MAP_RELATIONS:
            component_map_id, _item = self._component_maps.popitem(last=False)
            self._expired.append(component_map_id)
        if record.component_map_id not in self._component_maps:
            raise MeshResourceError(
                "MESH_COMPONENT_MAP_BUDGET_EXCEEDED",
                "ComponentMap exceeds the retained relation budget",
            )
        return record

    def component_map(self, component_map_id: str) -> Any:
        record = self._component_maps.get(component_map_id)
        if record is None:
            if component_map_id in self._expired:
                raise MeshResourceError(
                    "MESH_COMPONENT_MAP_EXPIRED",
                    f"ComponentMap was evicted: {component_map_id}",
                    kind="not_found",
                )
            raise MeshResourceError(
                "MESH_COMPONENT_MAP_NOT_FOUND",
                f"ComponentMap does not exist: {component_map_id}",
                kind="not_found",
            )
        self._component_maps.move_to_end(component_map_id)
        return record

    def release_component_map(self, component_map_id: str) -> bool:
        return self._component_maps.pop(component_map_id, None) is not None

    def add_component_catalog(self, record: Any) -> Any:
        if len(record.components) > MAX_COMPONENT_CATALOG_COMPONENTS:
            raise MeshResourceError(
                "MESH_COMPONENT_CATALOG_BUDGET_EXCEEDED",
                "ComponentCatalog exceeds the per-resource component budget",
            )
        self._component_catalogs[record.component_catalog_id] = record
        self._component_catalogs.move_to_end(record.component_catalog_id)
        while (
            len(self._component_catalogs) > MAX_COMPONENT_CATALOGS
            or sum(item.face_reference_count for item in self._component_catalogs.values())
            > MAX_COMPONENT_CATALOG_FACE_REFERENCES
            or sum(len(item.components) for item in self._component_catalogs.values())
            > MAX_COMPONENT_CATALOG_COMPONENTS
        ):
            component_catalog_id, _item = self._component_catalogs.popitem(last=False)
            self._expired.append(component_catalog_id)
        if record.component_catalog_id not in self._component_catalogs:
            raise MeshResourceError(
                "MESH_COMPONENT_CATALOG_BUDGET_EXCEEDED",
                "ComponentCatalog exceeds the retained resource budget",
            )
        return record

    def component_catalog(self, component_catalog_id: str) -> Any:
        record = self._component_catalogs.get(component_catalog_id)
        if record is None:
            if component_catalog_id in self._expired:
                raise MeshResourceError(
                    "MESH_COMPONENT_CATALOG_EXPIRED",
                    f"ComponentCatalog was evicted: {component_catalog_id}",
                    kind="not_found",
                )
            raise MeshResourceError(
                "MESH_COMPONENT_CATALOG_NOT_FOUND",
                f"ComponentCatalog does not exist: {component_catalog_id}",
                kind="not_found",
            )
        self._component_catalogs.move_to_end(component_catalog_id)
        return record

    def release_component_catalog(self, component_catalog_id: str) -> bool:
        return self._component_catalogs.pop(component_catalog_id, None) is not None

    @property
    def selection_count(self) -> int:
        return len(self._selections)

    @property
    def surface_count(self) -> int:
        return len(self._surfaces)

    @property
    def component_map_count(self) -> int:
        return len(self._component_maps)

    @property
    def component_catalog_count(self) -> int:
        return len(self._component_catalogs)
