"""Execute the recovery kernels with deterministic RNA/BMesh stand-ins.

Real CustomData allocation and edge layout are additionally checked by the
Blender regression script; Python stand-ins cannot prove native memory safety.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ADDON = Path(__file__).parents[1] / "blender_addon" / "blender_research_mcp_addon"


class OperationError(RuntimeError):
    def __init__(self, code: str, message: str, **kwargs: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = kwargs.get("details", {})


def kernels(filename: str, names: set[str], **dependencies: Any) -> dict[str, Any]:
    path = ADDON / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = [node for node in tree.body if getattr(node, "name", None) in names]
    assert len(selected) == len(names)
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
    exec(compile(module, str(path), "exec"), dependencies)
    return dependencies


class Groups(list[Any]):
    def new(self, *, name: str) -> Any:
        group = SimpleNamespace(name=name, lock_weight=False)
        self.append(group)
        return group


def schema(obj: Any, *, identities: bool = False) -> tuple[Any, ...]:
    return tuple(
        (g.name, g.lock_weight, *([id(g)] if identities else [])) for g in obj.vertex_groups
    )


def weight_kernels() -> tuple[dict[str, Any], Any, Any]:
    obj = SimpleNamespace(name="Body", vertex_groups=Groups())
    for i in range(761):
        obj.vertex_groups.new(name=f"Bone{i}")
    mesh = SimpleNamespace(name="Body Mesh", weights=(((0, 1.0),),))
    obj.data = mesh
    writes = []

    def write(_obj: Any, values: Any) -> None:
        writes.append(values)
        mesh.weights = values

    functions = kernels(
        "mesh_weight_ops.py",
        {"_restore_schemas", "_restore_call_state"},
        bpy=SimpleNamespace(data=SimpleNamespace(objects={obj.name: obj})),
        session_identity=lambda _kind, value: str(id(value)),
        _group_schema=schema,
        _capture_weights=lambda value: value.weights,
        _write_weights=write,
        MeshWeightOperationError=OperationError,
    )
    functions["writes"] = writes
    return functions, obj, mesh


def test_rejected_topology_preserves_all_group_identities_and_does_not_rewrite_weights() -> None:
    functions, obj, mesh = weight_kernels()
    before = schema(obj, identities=True)
    functions["_restore_call_state"](
        mesh,
        {obj.name: str(id(obj))},
        {obj.name: schema(obj)},
        mesh.weights,
        OperationError("MESH_BOUNDARY_INVALID", "not a boundary"),
    )
    assert schema(obj, identities=True) == before
    assert not functions["writes"]


def test_failed_write_restores_values_without_recreating_unchanged_groups() -> None:
    functions, obj, mesh = weight_kernels()
    before = schema(obj, identities=True)
    baseline = mesh.weights
    mesh.weights = (((0, 0.5),),)
    functions["_restore_call_state"](
        mesh,
        {obj.name: str(id(obj))},
        {obj.name: schema(obj)},
        baseline,
        RuntimeError("write"),
    )
    assert mesh.weights == baseline
    assert schema(obj, identities=True) == before
    assert functions["writes"] == [baseline]


def test_restore_checks_object_identity_and_proves_weight_values() -> None:
    functions, obj, mesh = weight_kernels()
    with pytest.raises(OperationError) as caught:
        functions["_restore_call_state"](
            mesh,
            {obj.name: "replaced"},
            {obj.name: schema(obj)},
            mesh.weights,
            RuntimeError(),
        )
    assert caught.value.code == "MESH_WEIGHT_RESTORE_FAILED"
    functions["_write_weights"] = lambda *_args: None
    with pytest.raises(OperationError) as caught:
        functions["_restore_call_state"](
            mesh,
            {obj.name: str(id(obj))},
            {obj.name: schema(obj)},
            (),
            RuntimeError(),
        )
    assert caught.value.code == "MESH_WEIGHT_RESTORE_FAILED"


@pytest.mark.parametrize("right_slots", [[], [None]])
def test_join_keeps_unassigned_faces_unassigned(right_slots: list[Any]) -> None:
    material = SimpleNamespace(name="Skin")

    def source(slots: list[Any]) -> dict[str, Any]:
        return {
            "mesh": SimpleNamespace(materials=slots, polygons=[SimpleNamespace(material_index=0)])
        }

    function = kernels(
        "mesh_join_ops.py",
        {"_material_schema"},
        session_identity=lambda _kind, value: str(id(value)),
    )
    result = function["_material_schema"](
        [source([material]), source(right_slots)],
        "PRESERVE_BY_IDENTITY",
    )
    assert result["items"] == [material, None]
    assert result["indices"][None] == 1


def test_join_all_slotless_or_dropped_materials_stay_empty() -> None:
    sources = [
        {"mesh": SimpleNamespace(materials=[], polygons=[SimpleNamespace(material_index=0)])}
    ]
    function = kernels("mesh_join_ops.py", {"_material_schema"})["_material_schema"]
    for policy in ("PRESERVE_BY_IDENTITY", "DROP", "ERROR_IF_DIFFERENT"):
        assert function(sources * 2, policy)["items"] == []


@dataclass
class Relation:
    source_index: int
    target_indices: tuple[int, ...]
    relation: str


class Element:
    def __init__(self, key: int, tag: int, index: int = 0) -> None:
        self.key, self.tag, self.index = key, tag, index
        self.is_valid = False  # Old RNA wrapper invalidated, native element can survive.

    def __hash__(self) -> int:
        return self.key

    def __getitem__(self, _layer: Any) -> int:
        return self.tag


class Sequence(list[Any]):
    layers = SimpleNamespace(int=SimpleNamespace(remove=lambda _layer: None))

    def index_update(self) -> None:
        for index, value in enumerate(self):
            value.index = index

    def ensure_lookup_table(self) -> None:
        pass


def test_lineage_uses_native_survivors_not_python_wrapper_ids_or_interpolated_vertex_tags() -> None:
    old = (Element(100, 1), Element(200, 2))
    current = Sequence([Element(100, 1), Element(200, 2), Element(300, 1)])
    state = SimpleNamespace(sequence=current, layer="tag", before=old, before_keys=(100, 200))
    function = kernels(
        "mesh_topology_ops.py", {"_finish_lineage"}, DOMAINS=("VERTEX",), ComponentRelation=Relation
    )["_finish_lineage"]
    relations, created, deleted = function(None, {"VERTEX": state}, "subdivide")
    assert relations["VERTEX"] == (Relation(0, (0,), "SURVIVED"), Relation(1, (1,), "SURVIVED"))
    assert created == {"VERTEX": (2,)}
    assert deleted == {"VERTEX": ()}


def test_component_change_counts_ignore_invalidated_python_wrappers() -> None:
    old = [Element(100, 1), Element(200, 2)]
    current = Sequence([Element(100, 1), Element(200, 2), Element(300, 0)])
    baseline = {kind: old for kind in ("vertices", "edges", "faces")}
    baseline.update({kind + "_native_keys": [100, 200] for kind in tuple(baseline)})
    functions = kernels(
        "mesh_ops.py", {"_component_changes", "_index_page"}, MAX_COMPONENT_TARGETS=4096
    )
    result = functions["_component_changes"](
        SimpleNamespace(verts=current, edges=current, faces=current),
        baseline,
    )
    for kind in ("vertices", "edges", "faces"):
        assert result["created"][kind]["indices"] == [2]
        assert result["deleted"][kind]["count"] == 0


@pytest.mark.parametrize("corrupt", [False, True])
def test_bmesh_write_preserves_and_verifies_component_order(corrupt: bool) -> None:
    vertices = Sequence([SimpleNamespace(index=0), SimpleNamespace(index=1)])
    edges = Sequence([SimpleNamespace(verts=vertices)])
    faces = Sequence([])
    mesh = SimpleNamespace(edges=[], polygons=[])
    calls = []

    def update(**kwargs: Any) -> None:
        calls.append(kwargs)

    def write(target: Any) -> None:
        target.edges = [SimpleNamespace(vertices=[0, 0] if corrupt else [0, 1])]

    mesh.update = update
    bm = SimpleNamespace(verts=vertices, edges=edges, faces=faces, to_mesh=write)
    function = kernels("mesh_ops.py", {"write_bmesh_exact"}, MeshOperationError=OperationError)[
        "write_bmesh_exact"
    ]
    if corrupt:
        with pytest.raises(OperationError) as caught:
            function(bm, mesh)
        assert caught.value.code == "MESH_LINEAGE_GENERATION_FAILED"
    else:
        function(bm, mesh)
    assert calls == [{"calc_edges": False, "calc_edges_loose": True}]


def test_regression_scripts_are_python_311() -> None:
    for name in ("blender_regression_0172.py", "live_smoke_0172.py"):
        path = ADDON.parents[1] / "scripts" / name
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11))
