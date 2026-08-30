import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parents[1] / "blender_addon" / "blender_research_mcp_addon"
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
        elif modifier_type == "SOLIDIFY":
            self.thickness = 0.01
            self.offset = -1.0
            self.use_even_offset = False
            self.use_quality_normals = False
            self.use_rim = True
            self.use_rim_only = False
            self.use_flip_normals = False

    def as_pointer(self) -> int:
        return id(self)

    def path_from_id(self, attribute: str) -> str:
        return f'modifiers["{self.name}"].{attribute}'

    def is_property_readonly(self, _attribute: str) -> bool:
        return False

    def get(self, name: str, default: object = None) -> object:
        return self._custom.get(name, default)

    def __setitem__(self, name: str, value: object) -> None:
        self._custom[name] = value

    def __delitem__(self, name: str) -> None:
        del self._custom[name]

    def __contains__(self, name: str) -> bool:
        return name in self._custom


class FailingBevel(FakeModifier):
    def __init__(self, *, restore_fails: bool = False) -> None:
        self._width = 0.1
        self._armed = False
        self._restore_fails = restore_fails
        self._broken = False
        super().__init__("Soft Edges", "BEVEL")
        self._armed = True

    @property
    def width(self) -> float:
        return self._width

    @width.setter
    def width(self, value: float) -> None:
        if self._armed and value == 0.25:
            self._width = value
            self._broken = True
            raise RuntimeError("injected setting failure")
        if self._armed and self._broken and self._restore_fails:
            raise RuntimeError("injected restore failure")
        self._width = value


class FakeModifiers:
    def __init__(self, values: list[FakeModifier]) -> None:
        self._values = values

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, index: int) -> FakeModifier:
        return self._values[index]

    def get(self, name: str) -> FakeModifier | None:
        return next((value for value in self._values if value.name == name), None)

    def new(self, *, name: str, type: str) -> FakeModifier:
        modifier = FakeModifier(name, type)
        self._values.append(modifier)
        return modifier

    def remove(self, modifier: FakeModifier) -> None:
        self._values.remove(modifier)

    def move(self, before: int, after: int) -> None:
        self._values.insert(after, self._values.pop(before))

    def reverse(self) -> None:
        self._values.reverse()


class FakeModifierWrapper:
    def __init__(self, target: FakeModifier) -> None:
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(self._target, name, value)

    def as_pointer(self) -> int:
        return self._target.as_pointer()


class UnstableWrapperModifiers(FakeModifiers):
    def __getitem__(self, index: int) -> FakeModifierWrapper:
        return FakeModifierWrapper(self._values[index])

    def get(self, name: str) -> FakeModifierWrapper | None:
        value = super().get(name)
        return FakeModifierWrapper(value) if value is not None else None


class FakeID:
    def __init__(self, name: str) -> None:
        self.name = name
        self.users = 1
        self.library = None
        self.polygons = [object()] * 12

    def as_pointer(self) -> int:
        return id(self)


class FakeObject:
    def __init__(self, modifiers: list[FakeModifier], name: str = "Mesh") -> None:
        self.name = name
        self.type = "MESH"
        self.library = None
        self.override_library = None
        self.data = FakeID("Mesh Data")
        self.modifiers = FakeModifiers(modifiers)
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
    bpy.context = SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None))
    sys.modules["bpy"] = bpy

    authoring = types.ModuleType(f"{PACKAGE}.authoring_ops")
    authoring.AuthoringOperationError = OperationError
    sys.modules[authoring.__name__] = authoring

    lookdev = types.ModuleType(f"{PACKAGE}.lookdev_ops")
    lookdev.session_identity = lambda kind, value: f"{kind}:{value.as_pointer()}"

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


def test_exact_modifier_uses_rna_session_identity_not_python_wrapper_identity() -> None:
    modifier = FakeModifier("Soft Edges", "BEVEL")
    obj = FakeObject([modifier])
    obj.modifiers = UnstableWrapperModifiers([modifier])
    ops, _model = _load_modules(obj)

    resolved = ops._require_exact_modifier(
        obj,
        modifier_name="Soft Edges",
        modifier_identity=f"modifier:{id(modifier)}",
        modifier_type="BEVEL",
        stack_index=0,
    )

    assert resolved.as_pointer() == modifier.as_pointer()


def test_private_modifier_touch_hook_changes_setting_and_order_deterministically() -> None:
    bevel = FakeModifier("Soft Edges", "BEVEL")
    subsurf = FakeModifier("Smooth", "SUBSURF")
    obj = FakeObject([bevel, subsurf])
    ops, _model = _load_modules(obj)

    setting = ops.touch_modifier_for_test(
        {
            "action": "setting",
            "object_name": "Mesh",
            "modifier_name": "Soft Edges",
            "property": "width",
            "value": 0.77,
        }
    )
    moved = ops.touch_modifier_for_test(
        {
            "action": "move",
            "object_name": "Mesh",
            "modifier_name": "Smooth",
            "target_stack_index": 0,
        }
    )

    assert bevel.width == 0.77
    assert list(obj.modifiers) == [subsurf, bevel]
    assert setting["test_hook"] == moved["test_hook"] == "modifier_touch"
    assert setting["stack_fingerprint"] != moved["stack_fingerprint"]


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


def _base_params(ops, obj: FakeObject) -> dict[str, object]:
    return {
        "object_name": obj.name,
        "expected_object_identity": f"object:{id(obj)}",
        "expected_stack_fingerprint": ops.modifier_stack_fingerprint(obj),
    }


def _target_params(ops, obj: FakeObject, modifier: FakeModifier) -> dict[str, object]:
    result = _base_params(ops, obj)
    result.update(
        {
            "modifier_name": modifier.name,
            "expected_modifier_identity": f"modifier:{id(modifier)}",
            "expected_modifier_type": modifier.type,
            "expected_stack_index": list(obj.modifiers).index(modifier),
        }
    )
    return result


@pytest.mark.parametrize(
    ("modifier_type", "definition", "field", "expected"),
    [
        ("BEVEL", {"width": 0.25, "segments": 3}, "width", 0.25),
        ("SUBSURF", {"levels": 1, "render_levels": 1}, "levels", 1),
        ("SOLIDIFY", {"thickness": 0.2}, "thickness", 0.2),
    ],
)
def test_create_supported_modifiers_and_rollback_identity(
    modifier_type: str,
    definition: dict[str, object],
    field: str,
    expected: object,
) -> None:
    obj = FakeObject([])
    ops, model = _load_modules(obj)
    transaction = _transaction(model)
    baseline = ops.modifier_stack_fingerprint(obj)
    params = _base_params(ops, obj)
    params["definition"] = {
        "type": modifier_type,
        "name": "Authored",
        **definition,
    }

    result = ops.create_modifier(transaction, params)
    created = obj.modifiers.get("Authored")
    assert created is not None
    assert getattr(created, field) == expected
    assert result["delta_type"] == "modifier_create"

    ops.restore_modifier_delta(transaction.deltas[-1])
    ops.validate_restored_modifier_stacks(transaction)
    assert obj.modifiers.get("Authored") is None
    assert ops.modifier_stack_fingerprint(obj) == baseline


def test_set_modifier_is_atomic_noop_aware_and_reversible() -> None:
    modifier = FakeModifier("Soft Edges", "BEVEL")
    obj = FakeObject([modifier])
    ops, model = _load_modules(obj)
    transaction = _transaction(model)
    params = _target_params(ops, obj, modifier)
    params["settings"] = {"type": "BEVEL", "width": 0.25, "segments": 4}

    result = ops.set_modifier(transaction, params)
    assert [item["path"] for item in result["changes"]] == ["segments", "width"]
    assert modifier.width == 0.25
    assert modifier.segments == 4

    next_params = _target_params(ops, obj, modifier)
    next_params["settings"] = {"type": "BEVEL", "width": 0.25}
    noop = ops.set_modifier(transaction, next_params)
    assert noop["changed"] is False
    assert len(transaction.deltas) == 1

    ops.restore_modifier_delta(transaction.deltas[-1])
    ops.validate_restored_modifier_stacks(transaction)
    assert modifier.width == 0.1
    assert modifier.segments == 2


@pytest.mark.parametrize(
    ("restore_fails", "expected_code"),
    [
        (False, "MODIFIER_SETTINGS_APPLY_FAILED"),
        (True, "MODIFIER_SETTINGS_RESTORE_FAILED"),
    ],
)
def test_set_modifier_restores_partial_application_or_reports_restore_failure(
    restore_fails: bool,
    expected_code: str,
) -> None:
    modifier = FailingBevel(restore_fails=restore_fails)
    obj = FakeObject([modifier])
    ops, model = _load_modules(obj)
    transaction = _transaction(model)
    params = _target_params(ops, obj, modifier)
    params["settings"] = {"type": "BEVEL", "segments": 4, "width": 0.25}

    with pytest.raises(OperationError) as error:
        ops.set_modifier(transaction, params)

    assert error.value.code == expected_code
    assert transaction.deltas == []
    if not restore_fails:
        assert modifier.width == 0.1
        assert modifier.segments == 2


def test_move_crosses_unsupported_modifier_and_restores_same_identity() -> None:
    bevel = FakeModifier("Soft Edges", "BEVEL")
    unsupported = FakeModifier("Mirror", "MIRROR")
    subsurf = FakeModifier("Smooth", "SUBSURF")
    obj = FakeObject([bevel, unsupported, subsurf])
    ops, model = _load_modules(obj)
    transaction = _transaction(model)
    params = _target_params(ops, obj, subsurf)
    params["target_stack_index"] = 0

    result = ops.move_modifier(transaction, params)
    assert result["changed"] is True
    assert list(obj.modifiers) == [subsurf, bevel, unsupported]

    ops.restore_modifier_delta(transaction.deltas[-1])
    ops.validate_restored_modifier_stacks(transaction)
    assert list(obj.modifiers) == [bevel, unsupported, subsurf]


def test_create_uses_exact_position_rejects_duplicate_name_and_checks_capacity() -> None:
    existing = FakeModifier("Existing", "MIRROR")
    obj = FakeObject([existing])
    ops, model = _load_modules(obj)
    transaction = _transaction(model)
    params = _base_params(ops, obj)
    params["definition"] = {
        "type": "BEVEL",
        "name": "First",
        "stack_index": 0,
    }
    ops.create_modifier(transaction, params)
    assert [modifier.name for modifier in obj.modifiers] == ["First", "Existing"]

    duplicate_params = _base_params(ops, obj)
    duplicate_params["definition"] = {"type": "BEVEL", "name": "First"}
    with pytest.raises(OperationError) as duplicate_error:
        ops.create_modifier(transaction, duplicate_params)
    assert duplicate_error.value.code == "MODIFIER_NAME_CONFLICT"

    full = _transaction(model)
    full.deltas.extend(
        model.ScaleDelta("Other", "object:other", {"x": 1.0}, {"x": 2.0}) for _index in range(256)
    )
    capacity_params = _base_params(ops, obj)
    capacity_params["definition"] = {"type": "SOLIDIFY", "name": "Too Late"}
    with pytest.raises(model.TransactionModelError) as capacity_error:
        ops.create_modifier(full, capacity_params)
    assert capacity_error.value.code == "TRANSACTION_DELTA_LIMIT"
    assert obj.modifiers.get("Too Late") is None


def test_delete_is_pending_until_commit_and_rollback_restores_state() -> None:
    modifier = FakeModifier("Shell", "SOLIDIFY")
    obj = FakeObject([modifier])
    ops, model = _load_modules(obj)
    transaction = _transaction(model)

    result = ops.delete_modifier(transaction, _target_params(ops, obj, modifier))
    assert result["modifier"]["pending_delete"] is True
    assert modifier.show_viewport is False
    assert obj.modifiers.get("Shell") is modifier

    ops.restore_modifier_delta(transaction.deltas[-1])
    ops.validate_restored_modifier_stacks(transaction)
    assert modifier.show_viewport is True
    assert ops.modifier_pending_delete(modifier) is False

    committed = _transaction(model)
    ops.delete_modifier(committed, _target_params(ops, obj, modifier))
    assert ops.finalize_modifier_delta(committed.deltas[-1]) is not None
    assert obj.modifiers.get("Shell") is None


def test_native_save_finalizes_only_an_untouched_pending_delete() -> None:
    modifier = FakeModifier("Shell", "SOLIDIFY")
    obj = FakeObject([modifier])
    ops, model = _load_modules(obj)
    transaction = _transaction(model)
    ops.delete_modifier(transaction, _target_params(ops, obj, modifier))

    finalized = ops.adopt_modifier_delta_for_native_save(
        transaction.deltas[-1],
        transaction.transaction_id,
    )

    assert finalized == {
        "kind": "modifier_delete",
        "modifier_name": "Shell",
        "action": "finalized_native_save",
    }
    assert obj.modifiers.get("Shell") is None

    user_modifier = FakeModifier("User Shell", "SOLIDIFY")
    user_obj = FakeObject([user_modifier])
    ops, model = _load_modules(user_obj)
    transaction = _transaction(model)
    ops.delete_modifier(transaction, _target_params(ops, user_obj, user_modifier))
    user_modifier.show_viewport = True

    preserved = ops.adopt_modifier_delta_for_native_save(
        transaction.deltas[-1],
        transaction.transaction_id,
    )

    assert preserved == {
        "kind": "modifier_delete",
        "modifier_name": "User Shell",
        "action": "preserved_user_state",
    }
    assert user_obj.modifiers.get("User Shell") is user_modifier
    assert user_modifier.show_viewport is True
    assert ops.modifier_pending_delete(user_modifier) is False


def test_native_save_modifier_adoption_uses_rna_identity_not_python_wrapper_identity() -> None:
    modifier = FakeModifier("Shell", "SOLIDIFY")
    obj = FakeObject([modifier])
    ops, model = _load_modules(obj)
    transaction = _transaction(model)
    ops.delete_modifier(transaction, _target_params(ops, obj, modifier))

    wrapper = SimpleNamespace(
        name=obj.name,
        type=obj.type,
        data=obj.data,
        modifiers=obj.modifiers,
        as_pointer=obj.as_pointer,
    )
    sys.modules["bpy"].data.objects = {obj.name: wrapper}

    finalized = ops.adopt_modifier_delta_for_native_save(
        transaction.deltas[-1],
        transaction.transaction_id,
    )

    assert finalized is not None
    assert finalized["action"] == "finalized_native_save"
    assert obj.modifiers.get("Shell") is None


def test_subdivision_budget_and_boolean_cycles_are_rejected_before_writes() -> None:
    subsurf = FakeModifier("Smooth", "SUBSURF")
    source = FakeObject([subsurf], name="Source")
    source.data.polygons = [object()] * 200_000
    ops, model = _load_modules(source)
    transaction = _transaction(model)
    params = _target_params(ops, source, subsurf)
    params["settings"] = {"type": "SUBSURF", "levels": 2, "render_levels": 2}
    with pytest.raises(OperationError) as budget_error:
        ops.set_modifier(transaction, params)
    assert budget_error.value.code == "SUBDIVISION_BUDGET_EXCEEDED"
    assert transaction.deltas == []

    source.data.polygons = [object()] * 12
    operand = FakeObject([], name="Operand")
    loop = FakeModifier("Back To Source", "BOOLEAN")
    loop.object = source
    operand.modifiers = FakeModifiers([loop])
    sys.modules["bpy"].data.objects[operand.name] = operand
    create_params = _base_params(ops, source)
    create_params["definition"] = {
        "type": "BOOLEAN",
        "name": "Cycle",
        "operand": {
            "object_name": operand.name,
            "expected_object_identity": f"object:{id(operand)}",
        },
    }
    with pytest.raises(OperationError) as cycle_error:
        ops.create_modifier(transaction, create_params)
    assert cycle_error.value.code == "BOOLEAN_CYCLE"
    assert source.modifiers.get("Cycle") is None


def test_boolean_create_requires_exact_mesh_operand_and_respects_solver_options() -> None:
    source = FakeObject([], name="Source")
    operand = FakeObject([], name="Operand")
    ops, model = _load_modules(source)
    sys.modules["bpy"].data.objects[operand.name] = operand
    transaction = _transaction(model)
    params = _base_params(ops, source)
    params["definition"] = {
        "type": "BOOLEAN",
        "name": "Cut",
        "solver": "EXACT",
        "operation": "DIFFERENCE",
        "operand": {
            "object_name": operand.name,
            "expected_object_identity": f"object:{id(operand)}",
        },
    }

    result = ops.create_modifier(transaction, params)
    modifier = source.modifiers.get("Cut")
    assert modifier is not None
    assert modifier.object is operand
    assert result["modifier"]["settings"]["operand"]["object_identity"] == (f"object:{id(operand)}")

    set_params = _target_params(ops, source, modifier)
    set_params["settings"] = {
        "type": "BOOLEAN",
        "solver": "FAST",
        "use_self": True,
    }
    with pytest.raises(OperationError) as solver_error:
        ops.set_modifier(transaction, set_params)
    assert solver_error.value.code == "BOOLEAN_SOLVER_CONFLICT"
