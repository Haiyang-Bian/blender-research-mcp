import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = (
    Path(__file__).parents[1]
    / "blender_addon"
    / "blender_research_mcp_addon"
)
PACKAGE = "modifier_ops_test_package"


class OperationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: str = "precondition",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.kind = kind
        self.details = details or {}


class FakeModifier:
    def __init__(self, name: str, modifier_type: str) -> None:
        self.name = name
        self.type = modifier_type
        self.show_viewport = True
        self.show_render = True
        self._custom: dict[str, object] = {}
        if modifier_type == "BEVEL":
            self.width = 0.1
            self.segments = 2
            self.limit_method = "ANGLE"
            self.angle_limit = 0.5235987755982988
            self.affect = "EDGES"
            self.offset_type = "OFFSET"
            self.profile = 0.5
            self.use_clamp_overlap = True
            self.harden_normals = False
        elif modifier_type == "SUBSURF":
            self.subdivision_type = "CATMULL_CLARK"
            self.levels = 2
            self.render_levels = 2
            self.quality = 3
            self.show_only_control_edges = False
            self.use_limit_surface = True
            self.use_creases = True
        elif modifier_type == "BOOLEAN":
            self.operation = "DIFFERENCE"
            self.solver = "EXACT"
            self.use_self = False
            self.use_hole_tolerant = False
            self.double_threshold = 0.000001
            self.object = None

    def as_pointer(self) -> int:
        return id(self)

    def path_from_id(self, attribute: str) -> str:
        return f'modifiers["{self.name}"].{attribute}'

    def is_property_readonly(self, _attribute: str) -> bool:
        return False

    def get(self, name: str, default: object = None) -> object:
        return self._custom.get(name, default)


class FakeID:
    def __init__(self, name: str) -> None:
        self.name = name
        self.users = 1
        self.library = None
        self.polygons = [object()] * 12

    def as_pointer(self) -> int:
        return id(self)


class FakeObject:
    def __init__(self, modifiers: list[FakeModifier]) -> None:
        self.name = "Mesh"
        self.type = "MESH"
        self.library = None
        self.override_library = None
        self.data = FakeID("Mesh Data")
        self.modifiers = modifiers
        self.animation_data = None

    def as_pointer(self) -> int:
        return id(self)


@pytest.fixture(autouse=True)
def _restore_import_state():
    previous_bpy = sys.modules.get("bpy")
    yield
    for name in list(sys.modules):
        if name == PACKAGE or name.startswith(f"{PACKAGE}."):
            del sys.modules[name]
    if previous_bpy is None:
        sys.modules.pop("bpy", None)
    else:
        sys.modules["bpy"] = previous_bpy


def _load_modules(obj: FakeObject):
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(SOURCE)]
    sys.modules[PACKAGE] = package

    bpy = types.ModuleType("bpy")
    bpy.data = SimpleNamespace(objects={obj.name: obj})
    sys.modules["bpy"] = bpy

    authoring = types.ModuleType(f"{PACKAGE}.authoring_ops")
    authoring.AuthoringOperationError = OperationError
    sys.modules[authoring.__name__] = authoring

    lookdev = types.ModuleType(f"{PACKAGE}.lookdev_ops")
    lookdev.session_identity = lambda kind, value: f"{kind}:{id(value)}"

    def require_object(name: str, identity: str):
        if name != obj.name or identity != f"object:{id(obj)}":
            raise OperationError("TARGET_IDENTITY_CONFLICT", "object changed")
        return obj

    lookdev.require_object = require_object
    sys.modules[lookdev.__name__] = lookdev

    transaction_spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.transaction_model", SOURCE / "transaction_model.py"
    )
    assert transaction_spec is not None and transaction_spec.loader is not None
    transaction = importlib.util.module_from_spec(transaction_spec)
    sys.modules[transaction_spec.name] = transaction
    transaction_spec.loader.exec_module(transaction)

    ops_spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.modifier_ops", SOURCE / "modifier_ops.py"
    )
    assert ops_spec is not None and ops_spec.loader is not None
    ops = importlib.util.module_from_spec(ops_spec)
    sys.modules[ops_spec.name] = ops
    ops_spec.loader.exec_module(ops)
    return ops, transaction


def _transaction(model):
    return model.Transaction("tx", None, {}, "context", 0)


def test_modifier_inspection_is_ordered_typed_and_fingerprinted() -> None:
    modifiers = [FakeModifier("Soft Edges", "BEVEL"), FakeModifier("Legacy", "MIRROR")]
    obj = FakeObject(modifiers)
    ops, _model = _load_modules(obj)

    result = ops.inspect_modifiers("Mesh", 7)

    assert result["scene_generation"] == 7
    assert result["count"] == 2
    assert result["modifiers"][0]["stack_index"] == 0
    assert result["modifiers"][0]["settings"]["angle_limit_degrees"] == 30.0
    assert result["modifiers"][1]["supported"] is False
    assert len(result["stack_fingerprint"]) == 64


def test_modifier_fingerprint_covers_order_identity_and_settings() -> None:
    first = FakeModifier("Soft Edges", "BEVEL")
    second = FakeModifier("Smooth", "SUBSURF")
    obj = FakeObject([first, second])
    ops, _model = _load_modules(obj)
    baseline = ops.modifier_stack_fingerprint(obj)

    first.width = 0.2
    changed_setting = ops.modifier_stack_fingerprint(obj)
    obj.modifiers.reverse()
    changed_order = ops.modifier_stack_fingerprint(obj)

    assert baseline != changed_setting
    assert changed_setting != changed_order


def test_modifier_stack_guard_tracks_agent_writes_and_detects_user_drift() -> None:
    modifier = FakeModifier("Soft Edges", "BEVEL")
    obj = FakeObject([modifier])
    ops, model = _load_modules(obj)
    transaction = _transaction(model)
    baseline = ops.modifier_stack_fingerprint(obj)

    ops.ensure_modifier_stack_guard(transaction, obj, baseline)
    modifier.width = 0.25
    expected = ops.refresh_modifier_stack_guard(transaction, obj)
    ops.validate_modifier_stack_guards(transaction)

    modifier.width = 0.5
    with pytest.raises(model.TransactionModelError) as error:
        ops.validate_modifier_stack_guards(transaction)
    assert error.value.code == "MODIFIER_STACK_CONFLICT"
    guard = transaction.modifier_stack_guards[(obj.name, f"object:{id(obj)}")]
    assert guard.expected_fingerprint == expected


def test_modifier_stack_guard_verifies_full_baseline_after_restore() -> None:
    modifier = FakeModifier("Soft Edges", "BEVEL")
    obj = FakeObject([modifier])
    ops, model = _load_modules(obj)
    transaction = _transaction(model)
    baseline = ops.modifier_stack_fingerprint(obj)
    ops.ensure_modifier_stack_guard(transaction, obj, baseline)
    modifier.width = 0.25
    ops.refresh_modifier_stack_guard(transaction, obj)

    modifier.width = 0.1
    ops.validate_restored_modifier_stacks(transaction)
