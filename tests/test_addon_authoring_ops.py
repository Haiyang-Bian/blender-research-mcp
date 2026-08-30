import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parents[1] / "blender_addon" / "blender_research_mcp_addon"
PACKAGE = "authoring_ops_test_package"


@pytest.fixture(autouse=True)
def _restore_import_state():
    previous = {name: sys.modules.get(name) for name in ("bpy", "bmesh")}
    yield
    for name in list(sys.modules):
        if name == PACKAGE or name.startswith(f"{PACKAGE}."):
            del sys.modules[name]
    for name, module in previous.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class FakeIDCollection:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def __iter__(self):
        return iter(self._values.values())

    def get(self, name: str):
        return self._values.get(name)

    def add(self, value: object) -> None:
        self._values[str(value.name)] = value  # type: ignore[attr-defined]

    def remove(self, value: object) -> None:
        if isinstance(value, Object):
            for collection in list(value.users_collection):
                collection.objects.unlink(value)
            value.data = None
        self._values.pop(str(value.name), None)  # type: ignore[attr-defined]


class FakeMaterials(list[object]):
    pass


class Mesh:
    def __init__(self, name: str, collection: FakeIDCollection) -> None:
        self.name = name
        self.users = 0
        self.vertices = [object()] * 8
        self.edges = [object()] * 12
        self.polygons = [object()] * 6
        self.materials = FakeMaterials()
        self._collection = collection
        collection.add(self)

    def as_pointer(self) -> int:
        return id(self)

    def copy(self):
        return Mesh(f"{self.name} Copy", self._collection)


class PointLight:
    def __init__(self, name: str) -> None:
        self.name = name
        self.users = 0
        self.type = "POINT"
        self.energy = 500.0
        self.color = [1.0, 1.0, 1.0]

    def as_pointer(self) -> int:
        return id(self)


class Object:
    def __init__(self, name: str, object_type: str, data: object | None) -> None:
        self.name = name
        self.type = object_type
        self.users = 0
        self.users_collection: list[Collection] = []
        self.location = [0.0, 0.0, 0.0]
        self.rotation_euler = [0.0, 0.0, 0.0]
        self.scale = [1.0, 1.0, 1.0]
        self.modifiers: list[object] = []
        self.selected = False
        self._data: object | None = None
        self.data = data

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, value: object | None) -> None:
        if self._data is value:
            return
        if self._data is not None:
            self._data.users -= 1
        self._data = value
        if value is not None:
            value.users += 1

    def as_pointer(self) -> int:
        return id(self)

    def copy(self):
        duplicate = Object(self.name, self.type, self.data)
        duplicate.location = list(self.location)
        duplicate.rotation_euler = list(self.rotation_euler)
        duplicate.scale = list(self.scale)
        duplicate.selected = self.selected
        return duplicate

    def select_set(self, selected: bool) -> None:
        self.selected = selected


class CollectionObjects:
    def __init__(self, owner: "Collection", objects: FakeIDCollection) -> None:
        self._owner = owner
        self._objects = objects

    def link(self, obj: Object) -> None:
        self._objects.add(obj)
        if self._owner not in obj.users_collection:
            obj.users_collection.append(self._owner)
            obj.users += 1

    def unlink(self, obj: Object) -> None:
        if self._owner in obj.users_collection:
            obj.users_collection.remove(self._owner)
            obj.users -= 1


class Collection:
    def __init__(self, name: str, objects: FakeIDCollection) -> None:
        self.name = name
        self.users = 1
        self.objects = CollectionObjects(self, objects)

    def as_pointer(self) -> int:
        return id(self)


def _load_modules(*, source_type: str = "MESH"):
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(SOURCE)]
    sys.modules[PACKAGE] = package

    objects = FakeIDCollection()
    meshes = FakeIDCollection()
    lights = FakeIDCollection()
    collection = Collection("Scene Collection", objects)
    if source_type == "MESH":
        data = Mesh("Shared Mesh", meshes)
    else:
        data = PointLight("Shared Light")
        lights.add(data)
    source = Object("Source", source_type, data)
    source.selected = True
    collection.objects.link(source)

    bpy = types.ModuleType("bpy")
    bpy.data = SimpleNamespace(
        objects=objects,
        meshes=meshes,
        lights=lights,
        cameras=FakeIDCollection(),
        collections=FakeIDCollection(),
        images=FakeIDCollection(),
        materials=FakeIDCollection(),
        scenes=FakeIDCollection(),
        worlds=FakeIDCollection(),
    )
    bpy.context = SimpleNamespace(scene=SimpleNamespace(collection=collection))
    bpy.path = SimpleNamespace(abspath=lambda value: value)
    sys.modules["bpy"] = bpy
    sys.modules["bmesh"] = types.ModuleType("bmesh")

    lookdev = types.ModuleType(f"{PACKAGE}.lookdev_ops")
    lookdev.session_identity = lambda kind, value: f"{kind}:{value.as_pointer()}"
    sys.modules[lookdev.__name__] = lookdev

    modifier = types.ModuleType(f"{PACKAGE}.modifier_ops")
    modifier.modifier_stack_summary = lambda _obj: []
    sys.modules[modifier.__name__] = modifier

    model = _load(f"{PACKAGE}.transaction_model", SOURCE / "transaction_model.py")
    structural = _load(f"{PACKAGE}.structural_ops", SOURCE / "structural_ops.py")
    authoring = _load(f"{PACKAGE}.authoring_ops", SOURCE / "authoring_ops.py")
    return authoring, structural, model, source, data, objects


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _transaction(model):
    return model.Transaction("tx", None, {}, "context", 0)


def _guard_mesh_like_material_assignment(transaction, structural, model, data) -> None:
    transaction.record(
        model.StructuralDelta(
            kind="material_assign",
            action="material_slots",
            before=(),
            after=(structural.make_structure_guard("mesh", data),),
            payload={"data": data, "before": tuple(data.materials)},
        )
    )


def _linked_duplicate(authoring, transaction, source, name: str):
    duplicate, delta = authoring.duplicate_object(
        transaction,
        source_name=source.name,
        expected_source_identity=f"object:{source.as_pointer()}",
        name=name,
        linked_data=True,
        collection_name=None,
        expected_collection_identity=None,
        transform=None,
    )
    transaction.record(delta)
    return duplicate


def test_multiple_linked_duplicates_refresh_guard_commit_and_rollback() -> None:
    authoring, structural, model, source, mesh, objects = _load_modules()
    transaction = _transaction(model)
    _guard_mesh_like_material_assignment(transaction, structural, model, mesh)

    first = _linked_duplicate(authoring, transaction, source, "Linked 1")
    structural.validate_structural_transaction(transaction)
    second = _linked_duplicate(authoring, transaction, source, "Linked 2")
    structural.validate_structural_transaction(transaction)

    assert first.data is second.data is source.data
    assert first.selected is second.selected is False
    assert source.selected is True
    assert mesh.users == 3
    assert next(iter(transaction.expected_structures().values())).users == 3

    for delta in reversed(transaction.structural_deltas()):
        structural.restore_structural_delta(delta)

    assert objects.get("Linked 1") is None
    assert objects.get("Linked 2") is None
    assert mesh.users == 1


def test_linked_duplicate_refreshes_object_data_users_and_preserves_external_conflict() -> None:
    authoring, structural, model, source, light, _objects = _load_modules(source_type="LIGHT")
    transaction = _transaction(model)
    data_identity = f"pointlight:{light.as_pointer()}"
    transaction.record(
        model.ObjectDataDelta(
            object_name=source.name,
            object_identity=f"object:{source.as_pointer()}",
            data_name=light.name,
            data_identity=data_identity,
            data_kind="light",
            expected_users=1,
            before={"energy": 500.0},
            after={"energy": 700.0},
        )
    )

    _linked_duplicate(authoring, transaction, source, "Linked Light")
    delta = next(item for item in transaction.deltas if isinstance(item, model.ObjectDataDelta))
    assert delta.expected_users == 2

    light.users += 1
    expected = transaction.expected_properties()
    reference = next(iter(expected))
    assert reference.target[-1] == "2"
    assert light.users == 3


def test_three_linked_duplicates_without_existing_data_guard_restore_users() -> None:
    authoring, structural, model, source, mesh, objects = _load_modules()
    transaction = _transaction(model)

    for index in range(3):
        _linked_duplicate(authoring, transaction, source, f"Linked {index}")
        structural.validate_structural_transaction(transaction)

    assert mesh.users == 4
    for delta in reversed(transaction.structural_deltas()):
        structural.restore_structural_delta(delta)
    assert mesh.users == 1
    assert all(objects.get(f"Linked {index}") is None for index in range(3))


def test_external_user_count_change_after_latest_agent_write_remains_a_conflict() -> None:
    authoring, structural, model, source, mesh, _objects = _load_modules()
    transaction = _transaction(model)
    _guard_mesh_like_material_assignment(transaction, structural, model, mesh)
    _linked_duplicate(authoring, transaction, source, "Linked 1")
    _linked_duplicate(authoring, transaction, source, "Linked 2")
    structural.validate_structural_transaction(transaction)

    external = Object("External", "MESH", mesh)
    source.users_collection[0].objects.link(external)
    for _operation in ("commit", "rollback"):
        with pytest.raises(model.TransactionModelError) as error:
            structural.validate_structural_transaction(transaction)
        assert error.value.code == "STRUCTURE_CONFLICT"
    assert mesh.users == 4


def test_independent_duplicate_does_not_refresh_shared_mesh_guard() -> None:
    authoring, structural, model, source, mesh, _objects = _load_modules()
    transaction = _transaction(model)
    _guard_mesh_like_material_assignment(transaction, structural, model, mesh)

    duplicate, delta = authoring.duplicate_object(
        transaction,
        source_name=source.name,
        expected_source_identity=f"object:{source.as_pointer()}",
        name="Independent",
        linked_data=False,
        collection_name=None,
        expected_collection_identity=None,
        transform=None,
    )
    transaction.record(delta)
    structural.validate_structural_transaction(transaction)

    assert duplicate.data is not mesh
    assert mesh.users == 1
    mesh_guard = next(
        guard for guard in transaction.expected_structures().values() if guard.kind == "mesh"
    )
    assert mesh_guard.users == 1
