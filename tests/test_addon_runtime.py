import importlib.util
import queue
import sys
import threading
import types
from pathlib import Path


def load_addon_module(module_name: str):
    source = Path(__file__).parents[1] / "blender_addon" / "blender_research_mcp_addon"
    package_name = "addon_runtime_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(source)]
    sys.modules[package_name] = package
    for name in ("wire", module_name):
        qualified = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(qualified, source / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{package_name}.{module_name}"]


def test_cancelled_request_is_never_dispatched() -> None:
    runtime_module = load_addon_module("runtime")
    runtime = runtime_module.ListenerRuntime()
    pending = runtime_module.PendingRequest(
        request={"request_id": "cancelled"},
        event=threading.Event(),
        cancelled=True,
    )
    runtime._requests = queue.Queue()
    runtime._requests.put(pending)
    dispatched = False

    def dispatcher(_request):
        nonlocal dispatched
        dispatched = True
        return {}

    runtime.poll(dispatcher, lambda: None)

    assert dispatched is False
    assert pending.event.is_set()
