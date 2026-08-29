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
PACKAGE = "object_settings_test_package"


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


class CameraData:
    def __init__(self, *, users: int = 1, fail_mode: str | None = None) -> None:
        self.name = "Camera Data"
        self.users = users
        self.library = None
        self.type = "PERSP"
        self._lens = 50.0
        self.fail_mode = fail_mode
        self.sensor_width = 36.0
        self.clip_start = 0.1
        self.clip_end = 1000.0
        self.ortho_scale = 6.0
        self.shift_x = 0.0
        self.shift_y = 0.0

    @property
    def lens(self) -> float:
        return self._lens

    @lens.setter
    def lens(self, value: float) -> None:
        if self.fail_mode == "apply" and value != 50.0:
            raise RuntimeError("injected apply failure")
        if self.fail_mode == "restore":
            raise RuntimeError("injected apply and restore failure")
        self._lens = value


class PointLight:
    def __init__(self) -> None:
        self.name = "Point Data"
        self.users = 1
        self.library = None
        self.type = "POINT"
        self.energy = 500.0
        self.color = [1.0, 1.0, 1.0]
        self.shadow_soft_size = 0.25


class Object:
    def __init__(self, data: CameraData) -> None:
        self.name = "Camera"
        self.type = "CAMERA"
        self.data = data
        self.library = None
        self.override_library = None
        self.location = [0.0, 0.0, 0.0]
        self.rotation_euler = [0.0, 0.0, 0.0]
        self.scale = [1.0, 1.0, 1.0]
        self.hide_viewport = False
        self.hide_render = False

    @staticmethod
    def select_get() -> bool:
        return False


def _load_modules(obj: Object):
    for name in list(sys.modules):
        if name == PACKAGE or name.startswith(f"{PACKAGE}."):
            del sys.modules[name]

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(SOURCE)]
    sys.modules[PACKAGE] = package

    bpy = types.ModuleType("bpy")
    bpy.context = SimpleNamespace(
        view_layer=SimpleNamespace(
            objects=SimpleNamespace(active=None),
            update=lambda: None,
        )
    )
    sys.modules["bpy"] = bpy

    authoring = types.ModuleType(f"{PACKAGE}.authoring_ops")
    authoring.AuthoringOperationError = OperationError
    sys.modules[authoring.__name__] = authoring

    lookdev = types.ModuleType(f"{PACKAGE}.lookdev_ops")

    def require_object(name: str, identity: str) -> Object:
        if name != obj.name or identity != f"object:{id(obj)}":
            raise OperationError("TARGET_IDENTITY_CONFLICT", "object changed", kind="conflict")
        return obj

    lookdev.require_object = require_object
    lookdev.session_identity = lambda kind, value: f"{kind}:{id(value)}"
    sys.modules[lookdev.__name__] = lookdev

    structural = types.ModuleType(f"{PACKAGE}.structural_ops")
    structural.refresh_structure_guard_if_present = lambda *_args: None
    sys.modules[structural.__name__] = structural

    transaction_spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.transaction_model", SOURCE / "transaction_model.py"
    )
    assert transaction_spec is not None and transaction_spec.loader is not None
    transaction_model = importlib.util.module_from_spec(transaction_spec)
    sys.modules[transaction_spec.name] = transaction_model
    transaction_spec.loader.exec_module(transaction_model)

    settings_spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.object_settings_ops", SOURCE / "object_settings_ops.py"
    )
    assert settings_spec is not None and settings_spec.loader is not None
    settings = importlib.util.module_from_spec(settings_spec)
    sys.modules[settings_spec.name] = settings
    settings_spec.loader.exec_module(settings)
    return settings, transaction_model


def _transaction(model):
    return model.Transaction(
        transaction_id="tx",
        label=None,
        context_snapshot={},
        context_fingerprint="context",
        started_generation=0,
    )


def _params(obj: Object, data: CameraData, patches: list[dict[str, object]]):
    return {
        "object_name": obj.name,
        "expected_object_identity": f"object:{id(obj)}",
        "patches": patches,
    }


def _camera_patch(data: CameraData, **settings: object) -> dict[str, object]:
    return {
        "type": "camera",
        "expected_data_identity": f"cameradata:{id(data)}",
        "expected_data_users": data.users,
        "expected_camera_type": "PERSP",
        **settings,
    }


def test_point_light_does_not_require_area_shape_rna() -> None:
    data = PointLight()
    obj = Object(data)  # type: ignore[arg-type]
    obj.type = "LIGHT"
    settings, model = _load_modules(obj)
    transaction = _transaction(model)

    result = settings.apply_object_settings(
        transaction,
        _params(  # type: ignore[arg-type]
            obj,
            data,
            [
                {
                    "type": "light",
                    "expected_data_identity": f"pointlight:{id(data)}",
                    "expected_data_users": 1,
                    "expected_light_type": "POINT",
                    "energy": 750.0,
                    "radius": 0.5,
                }
            ],
        ),
    )

    assert result["changed"] is True
    assert data.energy == 750.0
    assert data.shadow_soft_size == 0.5
    assert len(transaction.deltas) == 1


def test_addon_applies_multi_patch_atomically_and_records_typed_deltas() -> None:
    data = CameraData()
    obj = Object(data)
    settings, model = _load_modules(obj)
    transaction = _transaction(model)

    result = settings.apply_object_settings(
        transaction,
        _params(
            obj,
            data,
            [
                _camera_patch(data, lens=85.0),
                {"type": "visibility", "hide_render": True},
                {"type": "transform", "location": {"z": 4.0}},
            ],
        ),
    )

    assert obj.location[2] == 4.0
    assert obj.hide_render is True
    assert data.lens == 85.0
    assert result["changed"] is True
    assert [change["path"] for change in result["changes"]] == [
        "camera.lens",
        "transform.location.z",
        "visibility.hide_render",
    ]
    assert len(transaction.deltas) == 3
    assert transaction.delta_kinds() == [
        "camera_setting",
        "object_location",
        "object_visibility",
    ]


def test_addon_noop_does_not_record_a_delta() -> None:
    data = CameraData()
    obj = Object(data)
    settings, model = _load_modules(obj)
    transaction = _transaction(model)

    result = settings.apply_object_settings(
        transaction,
        _params(
            obj,
            data,
            [
                _camera_patch(data, lens=50.0),
                {"type": "transform", "scale": {"x": 1.0}},
                {"type": "visibility", "hide_render": False},
            ],
        ),
    )

    assert result["changed"] is False
    assert result["changes"] == []
    assert transaction.deltas == []


def test_addon_validates_capacity_before_any_write() -> None:
    data = CameraData()
    obj = Object(data)
    settings, model = _load_modules(obj)
    transaction = _transaction(model)
    transaction.deltas.extend(
        model.ScaleDelta("Other", "object:other", {"x": 1.0}, {"x": 2.0})
        for _index in range(255)
    )

    with pytest.raises(model.TransactionModelError) as error:
        settings.apply_object_settings(
            transaction,
            _params(
                obj,
                data,
                [
                    {"type": "transform", "location": {"z": 4.0}},
                    _camera_patch(data, lens=85.0),
                ],
            ),
        )

    assert error.value.code == "TRANSACTION_DELTA_LIMIT"
    assert obj.location[2] == 0.0
    assert data.lens == 50.0


def test_addon_restores_earlier_patches_when_later_application_fails() -> None:
    data = CameraData(fail_mode="apply")
    obj = Object(data)
    settings, model = _load_modules(obj)
    transaction = _transaction(model)

    with pytest.raises(OperationError) as error:
        settings.apply_object_settings(
            transaction,
            _params(
                obj,
                data,
                [
                    {"type": "transform", "location": {"z": 4.0}},
                    _camera_patch(data, lens=85.0),
                ],
            ),
        )

    assert error.value.code == "OBJECT_SETTINGS_APPLY_FAILED"
    assert obj.location[2] == 0.0
    assert data.lens == 50.0
    assert transaction.deltas == []


def test_addon_reports_when_partial_restore_cannot_be_proven() -> None:
    data = CameraData(fail_mode="restore")
    obj = Object(data)
    settings, model = _load_modules(obj)

    with pytest.raises(OperationError) as error:
        settings.apply_object_settings(
            _transaction(model),
            _params(obj, data, [_camera_patch(data, lens=85.0)]),
        )

    assert error.value.code == "OBJECT_SETTINGS_RESTORE_FAILED"


def test_addon_rejects_shared_data_without_exact_authorization() -> None:
    data = CameraData(users=2)
    obj = Object(data)
    settings, model = _load_modules(obj)
    patch = _camera_patch(data, lens=85.0)

    with pytest.raises(OperationError) as error:
        settings.apply_object_settings(
            _transaction(model),
            _params(obj, data, [patch]),
        )
    assert error.value.code == "SHARED_OBJECT_DATA_CONFIRMATION_REQUIRED"

    patch["allow_shared_data"] = True
    result = settings.apply_object_settings(
        _transaction(model),
        _params(obj, data, [patch]),
    )
    assert result["changed"] is True
    assert data.lens == 85.0


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("expected_data_identity", "cameradata:stale", "OBJECT_DATA_IDENTITY_MISMATCH"),
        ("expected_data_users", 3, "OBJECT_DATA_USERS_MISMATCH"),
    ],
)
def test_addon_rejects_data_identity_and_user_count_drift(
    field: str,
    value: object,
    code: str,
) -> None:
    data = CameraData(users=2)
    obj = Object(data)
    settings, model = _load_modules(obj)
    patch = _camera_patch(data, lens=85.0)
    patch["allow_shared_data"] = True
    patch[field] = value

    with pytest.raises(OperationError) as error:
        settings.apply_object_settings(
            _transaction(model),
            _params(obj, data, [patch]),
        )
    assert error.value.code == code
