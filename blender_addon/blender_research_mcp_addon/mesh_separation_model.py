"""Pure connected-region helpers for Mesh separation."""

from __future__ import annotations


def face_region_component_count(
    face_edges: tuple[tuple[int, ...], ...],
    selected_indices: tuple[int, ...],
) -> int:
    if not selected_indices:
        return 0
    if any(index < 0 or index >= len(face_edges) for index in selected_indices):
        raise ValueError("selected face index is outside the Mesh")
    selected = set(selected_indices)
    edge_faces: dict[int, list[int]] = {}
    for face_index in selected:
        for edge_index in face_edges[face_index]:
            edge_faces.setdefault(edge_index, []).append(face_index)

    remaining = set(selected)
    components = 0
    while remaining:
        components += 1
        pending = [remaining.pop()]
        while pending:
            face_index = pending.pop()
            neighbors = {
                neighbor
                for edge_index in face_edges[face_index]
                for neighbor in edge_faces.get(edge_index, ())
            }
            discovered = neighbors & remaining
            remaining.difference_update(discovered)
            pending.extend(discovered)
    return components
