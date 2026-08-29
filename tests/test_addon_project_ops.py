import ast
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
ADDON = ROOT / "blender_addon" / "blender_research_mcp_addon"


class FakeWindowManagerOps:
    def __init__(self, data) -> None:
        self.data = data
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.result = {"FINISHED"}

    def save_mainfile(self):
        self.calls.append(("save_mainfile", {}))
        self.data.is_saved = True
        self.data.is_dirty = False
        return self.result

    def save_as_mainfile(self, **kwargs):
        self.calls.append(("save_as_mainfile", kwargs))
        self.data.filepath = kwargs["filepath"]
        self.data.is_saved = True
        self.data.is_dirty = False
        return self.result

    def open_mainfile(self, **kwargs):
        self.calls.append(("open_mainfile", kwargs))
        self.data.filepath = kwargs["filepath"]
        self.data.is_saved = True
        self.data.is_dirty = False
        return self.result

    def quit_blender(self):
        self.calls.append(("quit_blender", {}))
        return self.result


def load_project_ops(data):
    wm = FakeWindowManagerOps(data)
    fake_bpy = SimpleNamespace(data=data, ops=SimpleNamespace(wm=wm))
    previous = sys.modules.get("bpy")
    sys.modules["bpy"] = fake_bpy
    try:
        spec = importlib.util.spec_from_file_location(
            "addon_project_ops_test",
            ADDON / "project_ops.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            del sys.modules["bpy"]
        else:
            sys.modules["bpy"] = previous
    return module, wm


def test_project_status_does_not_need_a_viewport(tmp_path: Path) -> None:
    current = tmp_path / "current.blend"
    current.write_bytes(b"blend")
    data = SimpleNamespace(filepath=str(current), is_saved=True, is_dirty=True)
    module, _wm = load_project_ops(data)

    status = module.project_status(7, {"transaction_id": "tx"}, None)

    assert status == {
        "filepath": str(current),
        "is_saved": True,
        "is_dirty": True,
        "scene_generation": 7,
        "active_transaction": {"transaction_id": "tx"},
        "last_operation": None,
    }


def test_committed_semantic_deltas_require_save_even_when_blender_is_clean() -> None:
    data = SimpleNamespace(filepath="", is_saved=False, is_dirty=False)
    module, _wm = load_project_ops(data)

    assert module.transition_needs_save(True, None)
    assert module.transition_needs_save(False, {"delta_count": 1})
    assert not module.transition_needs_save(False, {"delta_count": 0})
    assert not module.transition_needs_save(False, None)


def test_save_as_overwrites_without_file_selector_and_becomes_current(tmp_path: Path) -> None:
    target = tmp_path / "saved-as.blend"
    data = SimpleNamespace(filepath="", is_saved=False, is_dirty=True)
    module, wm = load_project_ops(data)

    result = module.save_project(str(target))

    assert result["mode"] == "save_as"
    assert result["path"] == str(target)
    assert wm.calls == [
        (
            "save_as_mainfile",
            {"filepath": str(target), "check_existing": False},
        )
    ]


def test_untitled_save_requires_path_and_cancelled_save_is_reported(tmp_path: Path) -> None:
    data = SimpleNamespace(filepath="", is_saved=False, is_dirty=True)
    module, wm = load_project_ops(data)

    with pytest.raises(module.ProjectOperationError) as untitled:
        module.save_project()
    assert untitled.value.code == "CURRENT_PROJECT_UNTITLED"

    target = tmp_path / "target.blend"
    wm.result = {"CANCELLED"}
    with pytest.raises(module.ProjectOperationError) as cancelled:
        module.save_project(str(target))
    assert cancelled.value.code == "PROJECT_SAVE_FAILED"


def test_project_paths_are_absolute_blend_files_with_expected_existence(tmp_path: Path) -> None:
    data = SimpleNamespace(filepath="", is_saved=False, is_dirty=False)
    module, _wm = load_project_ops(data)
    existing = tmp_path / "scene.blend"
    existing.write_bytes(b"blend")

    assert module.validate_open_path(str(existing)) == existing
    with pytest.raises(module.ProjectOperationError) as relative:
        module.validate_open_path("scene.blend")
    assert relative.value.code == "PROJECT_PATH_INVALID"
    with pytest.raises(module.ProjectOperationError) as missing:
        module.validate_open_path(str(tmp_path / "missing.blend"))
    assert missing.value.code == "PROJECT_NOT_FOUND"
    with pytest.raises(module.ProjectOperationError) as wrong_suffix:
        module.validate_save_path(str(tmp_path / "scene.txt"))
    assert wrong_suffix.value.code == "PROJECT_PATH_INVALID"


def test_open_forwards_trusted_script_and_ui_flags_and_reports_cancel(tmp_path: Path) -> None:
    target = tmp_path / "target.blend"
    target.write_bytes(b"blend")
    data = SimpleNamespace(filepath="", is_saved=False, is_dirty=False)
    module, wm = load_project_ops(data)

    module.open_project(str(target), use_scripts=False, load_ui=False)
    assert wm.calls[-1] == (
        "open_mainfile",
        {
            "filepath": str(target),
            "display_file_selector": False,
            "use_scripts": False,
            "load_ui": False,
        },
    )

    wm.result = {"CANCELLED"}
    with pytest.raises(module.ProjectOperationError) as cancelled:
        module.open_project(str(target), use_scripts=True, load_ui=True)
    assert cancelled.value.code == "PROJECT_OPEN_FAILED"


def test_pending_file_operation_runs_only_from_the_next_tick_path() -> None:
    tree = ast.parse((ADDON / "state.py").read_text(encoding="utf-8"))
    addon_state = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AddonState"
    )
    methods = {
        node.name: node
        for node in addon_state.body
        if isinstance(node, ast.FunctionDef)
    }
    tick_calls = [
        ast.unparse(node.func)
        for node in ast.walk(methods["tick"])
        if isinstance(node, ast.Call)
    ]
    open_calls = [
        ast.unparse(node.func)
        for node in ast.walk(methods["_open_project"])
        if isinstance(node, ast.Call)
    ]
    perform_calls = [
        ast.unparse(node.func)
        for node in ast.walk(methods["_perform_pending_lifecycle_operation"])
        if isinstance(node, ast.Call)
    ]

    assert tick_calls.index("self._perform_pending_lifecycle_operation") < tick_calls.index(
        "self.runtime.poll"
    )
    assert "open_project" not in open_calls
    assert "self._schedule_lifecycle_operation" in open_calls
    assert "open_project" in perform_calls
