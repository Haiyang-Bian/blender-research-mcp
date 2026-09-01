from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.scene_organization import CollectionParent

SOURCE = Path(__file__).parents[1] / "blender_addon" / "blender_research_mcp_addon"
PACKAGE = "scene_organization_test_package"
PARENTS = TypeAdapter(CollectionParent)


@pytest.fixture(autouse=True)
def _restore_import_state():
    previous = sys.modules.get("bpy")
    yield
    for name in list(sys.modules):
        if name == PACKAGE or name.startswith(f"{PACKAGE}."):
            del sys.modules[name]
    if previous is None:
        sys.modules.pop("bpy", None)
    else:
        sys.modules["bpy"] = previous


class Matrix:
    def __init__(self, value: str) -> None:
        self.value = value

    def copy(self):
        return Matrix(self.value)

    def inverted_safe(self):
        return Matrix(f"inverse:{self.value}")

    def identity(self) -> None:
        self.value = "identity"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Matrix) and self.value == other.value


class IDCollection:
    def __init__(self, factory=None) -> None:
        self.values: dict[str, object] = {}
        self.factory = factory

    def __iter__(self):
        return iter(self.values.values())

    def get(self, name: str):
        return self.values.get(name)

    def add(self, value: object) -> None:
        self.values[str(value.name)] = value  # type: ignore[attr-defined]

    def new(self, name: str):
        assert self.factory is not None
        value = self.factory(name)
        self.add(value)
        return value

    def remove(self, value: object) -> None:
        self.values.pop(str(value.name), None)  # type: ignore[attr-defined]


class LinkCollection:
    def __init__(self, owner: object, *, child_links: bool = False) -> None:
        self.owner = owner
        self.child_links = child_links
        self.values: list[object] = []

    def __iter__(self):
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __contains__(self, value: object) -> bool:
        if isinstance(value, str):
            return any(item.name == value for item in self.values)  # type: ignore[attr-defined]
        return value in self.values

    def link(self, value: object) -> None:
        if value in self.values:
            return
        self.values.append(value)
        if self.child_links:
            value.users += 1  # type: ignore[attr-defined]
        else:
            value.users_collection.append(self.owner)  # type: ignore[attr-defined]
            value.users += 1  # type: ignore[attr-defined]

    def unlink(self, value: object) -> None:
        self.values.remove(value)
        if self.child_links:
            value.users -= 1  # type: ignore[attr-defined]
        else:
            value.users_collection.remove(self.owner)  # type: ignore[attr-defined]
            value.users -= 1  # type: ignore[attr-defined]


class Collection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.users = 0
        self.library = None
        self.override_library = None
        self.objects = LinkCollection(self)
        self.children = LinkCollection(self, child_links=True)

    def as_pointer(self) -> int:
        return id(self)


class Object:
    def __init__(self, name: str) -> None:
        self.name = name
        self.users = 0
        self.library = None
        self.override_library = None
        self.users_collection: list[Collection] = []
        self.parent: Object | None = None
        self.parent_type = "OBJECT"
        self.parent_bone = ""
        self.matrix_parent_inverse = Matrix("identity")
        self.matrix_world = Matrix(f"world:{name}")
        self.matrix_basis = Matrix(f"basis:{name}")

    def as_pointer(self) -> int:
        return id(self)


class Scene:
    def __init__(self, name: str, root: Collection) -> None:
        self.name = name
        self.users = 1
        self.collection = root

    def as_pointer(self) -> int:
        return id(self)


def _load_modules():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(SOURCE)]
    sys.modules[PACKAGE] = package

    collections = IDCollection(Collection)
    objects = IDCollection()
    root = Collection("Scene Collection")
    scene = Scene("Scene", root)
    scenes = IDCollection()
    scenes.add(scene)
    bpy = types.ModuleType("bpy")
    bpy.data = SimpleNamespace(collections=collections, objects=objects, scenes=scenes)
    sys.modules["bpy"] = bpy

    lookdev = types.ModuleType(f"{PACKAGE}.lookdev_ops")
    lookdev.session_identity = lambda kind, value: f"{kind}:{value.as_pointer()}"
    sys.modules[lookdev.__name__] = lookdev

    model = _load(f"{PACKAGE}.transaction_model", SOURCE / "transaction_model.py")

    structural = types.ModuleType(f"{PACKAGE}.structural_ops")

    def fingerprint(kind, value):
        if kind == "collection":
            payload = (
                tuple(item.name for item in value.children),
                tuple(item.name for item in value.objects),
            )
        elif kind == "scene":
            payload = tuple(item.name for item in value.collection.children)
        else:
            payload = (
                value.parent.name if value.parent is not None else None,
                value.parent_type,
                value.parent_bone,
                value.matrix_parent_inverse.value,
                value.matrix_world.value,
                value.matrix_basis.value,
                tuple(item.name for item in value.users_collection),
            )
        import hashlib

        return hashlib.sha256(repr(payload).encode()).hexdigest()

    structural.structure_fingerprint = fingerprint
    structural.make_structure_guard = lambda kind, value: model.StructureGuard(
        kind,
        value.name,
        f"{kind}:{value.as_pointer()}",
        fingerprint(kind, value),
        value.users,
    )
    structural.refresh_structure_guard_if_present = lambda *_args: None
    sys.modules[structural.__name__] = structural

    authoring = types.ModuleType(f"{PACKAGE}.authoring_ops")

    class AuthoringOperationError(RuntimeError):
        def __init__(self, code, message, *, kind="precondition", details=None):
            super().__init__(message)
            self.code = code
            self.kind = kind
            self.details = details or {}

    authoring.AuthoringOperationError = AuthoringOperationError
    authoring.object_summary = lambda obj: {
        "name": obj.name,
        "session_identity": f"object:{obj.as_pointer()}",
    }
    sys.modules[authoring.__name__] = authoring

    ops = _load(f"{PACKAGE}.scene_organization_ops", SOURCE / "scene_organization_ops.py")
    return ops, model, structural, collections, objects, root, scene


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _transaction(model):
    return model.Transaction("tx", None, {}, "context", 0)


def test_collection_parent_schema_is_closed_and_discriminated() -> None:
    root = PARENTS.validate_python(
        {
            "type": "SCENE_ROOT",
            "scene_name": "Scene",
            "expected_scene_identity": "scene:1",
            "expected_scene_structure_fingerprint": "a" * 64,
        }
    )
    assert root.type == "SCENE_ROOT"
    with pytest.raises(ValidationError):
        PARENTS.validate_python({**root.model_dump(), "extra": True})


def test_create_collection_is_exact_nested_and_rollback_owned() -> None:
    ops, model, structural, collections, _objects, root, scene = _load_modules()
    transaction = _transaction(model)
    collection, delta = ops.create_collection(
        transaction,
        {
            "name": "Modules",
            "parent": {
                "type": "SCENE_ROOT",
                "scene_name": scene.name,
                "expected_scene_identity": f"scene:{scene.as_pointer()}",
                "expected_scene_structure_fingerprint": structural.structure_fingerprint(
                    "scene", scene
                ),
            },
        },
    )
    assert collection in root.children
    assert delta.action == "create_resource"
    assert delta.payload["resource"] is collection
    with pytest.raises(ops.AuthoringOperationError) as conflict:
        ops.create_collection(
            transaction,
            {
                "name": "Modules",
                "parent": {
                    "type": "SCENE_ROOT",
                    "scene_name": scene.name,
                    "expected_scene_identity": f"scene:{scene.as_pointer()}",
                    "expected_scene_structure_fingerprint": structural.structure_fingerprint(
                        "scene", scene
                    ),
                },
            },
        )
    assert conflict.value.code == "COLLECTION_NAME_CONFLICT"
    collections.remove(collection)


def test_collection_link_move_and_last_link_are_reversible() -> None:
    ops, model, structural, collections, objects, root, _scene = _load_modules()
    first = Collection("First")
    second = Collection("Second")
    collections.add(first)
    collections.add(second)
    root.children.link(first)
    root.children.link(second)
    obj = Object("Module")
    objects.add(obj)
    first.objects.link(obj)
    transaction = _transaction(model)

    changed, link_delta, _collection, _obj = ops.change_collection_link(
        transaction,
        {
            "collection_name": second.name,
            "expected_collection_identity": f"collection:{second.as_pointer()}",
            "expected_collection_structure_fingerprint": structural.structure_fingerprint(
                "collection", second
            ),
            "object_name": obj.name,
            "expected_object_identity": f"object:{obj.as_pointer()}",
            "expected_object_collections_fingerprint": ops.object_collection_fingerprint(obj),
        },
        link=True,
    )
    assert changed is True and link_delta is not None
    assert obj in second.objects
    ops.restore_scene_organization_delta(link_delta)
    assert obj not in second.objects

    with pytest.raises(ops.AuthoringOperationError) as last:
        ops.change_collection_link(
            transaction,
            {
                "collection_name": first.name,
                "expected_collection_identity": f"collection:{first.as_pointer()}",
                "expected_collection_structure_fingerprint": structural.structure_fingerprint(
                    "collection", first
                ),
                "object_name": obj.name,
                "expected_object_identity": f"object:{obj.as_pointer()}",
                "expected_object_collections_fingerprint": (
                    ops.object_collection_fingerprint(obj)
                ),
            },
            link=False,
        )
    assert last.value.code == "COLLECTION_LAST_OBJECT_LINK"


def test_parent_keep_world_cycle_clear_bone_and_rollback() -> None:
    ops, model, structural, _collections, objects, _root, _scene = _load_modules()
    parent = Object("Parent")
    child = Object("Child")
    objects.add(parent)
    objects.add(child)
    transaction = _transaction(model)
    before_world = child.matrix_world.copy()
    changed, delta, _obj = ops.change_object_parent(
        transaction,
        {
            "child_name": child.name,
            "expected_child_identity": f"object:{child.as_pointer()}",
            "expected_child_structure_fingerprint": structural.structure_fingerprint(
                "object", child
            ),
            "parent_name": parent.name,
            "expected_parent_identity": f"object:{parent.as_pointer()}",
            "expected_parent_structure_fingerprint": structural.structure_fingerprint(
                "object", parent
            ),
            "transform_mode": "KEEP_WORLD",
        },
        clear=False,
    )
    assert changed is True and delta is not None
    assert child.parent is parent
    assert child.matrix_world == before_world
    ops.restore_scene_organization_delta(delta)
    assert child.parent is None
    assert child.matrix_world == before_world

    parent.parent = child
    with pytest.raises(ops.AuthoringOperationError) as cycle:
        ops.change_object_parent(
            transaction,
            {
                "child_name": child.name,
                "expected_child_identity": f"object:{child.as_pointer()}",
                "expected_child_structure_fingerprint": structural.structure_fingerprint(
                    "object", child
                ),
                "parent_name": parent.name,
                "expected_parent_identity": f"object:{parent.as_pointer()}",
                "expected_parent_structure_fingerprint": structural.structure_fingerprint(
                    "object", parent
                ),
                "transform_mode": "KEEP_LOCAL",
            },
            clear=False,
        )
    assert cycle.value.code == "OBJECT_PARENT_CYCLE"
    parent.parent = None

    child.parent = parent
    child.parent_type = "BONE"
    child.parent_bone = "Head"
    changed, clear_delta, _obj = ops.change_object_parent(
        transaction,
        {
            "child_name": child.name,
            "expected_child_identity": f"object:{child.as_pointer()}",
            "expected_child_structure_fingerprint": structural.structure_fingerprint(
                "object", child
            ),
            "expected_parent_name": parent.name,
            "expected_parent_identity": f"object:{parent.as_pointer()}",
            "expected_parent_structure_fingerprint": structural.structure_fingerprint(
                "object", parent
            ),
            "transform_mode": "KEEP_LOCAL",
        },
        clear=True,
    )
    assert changed is True and clear_delta is not None
    assert child.parent is None
    ops.restore_scene_organization_delta(clear_delta)
    assert child.parent is parent
    assert child.parent_type == "BONE"
    assert child.parent_bone == "Head"
