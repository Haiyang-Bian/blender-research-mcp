"""Main-thread state and semantic command dispatch."""

from __future__ import annotations

import contextlib
import math
import os
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from typing import Any

import bpy

from .authoring_ops import (
    AuthoringOperationError,
    create_object,
    duplicate_object,
    inspect_scene,
    object_summary,
    unlink_object,
)
from .capture_model import CaptureBook, CaptureEvidence
from .context_ops import (
    ContextOperationError,
    capture_context,
    capture_transaction_context,
    capture_viewport,
    context_summary,
    inspect_geometry,
    inspect_object,
    raycast_capture,
    resolve_viewport,
    restore_context,
)
from .generation import has_persistent_scene_update
from .lookdev_model import LookdevModelError, normalize_material_value
from .lookdev_ops import (
    inspect_material,
    inspect_object_lookdev,
    material_affected_objects,
    material_socket_is_driven,
    material_socket_kind,
    material_socket_range,
    material_socket_readonly,
    material_socket_value,
    read_property,
    require_modifier,
    require_object,
    require_shape_key,
    resolve_material_socket,
    restore_delta,
    session_identity,
    shape_key_is_driven,
)
from .material_authoring_ops import (
    assign_material,
    assignment_result,
    bind_texture,
    clear_texture,
    create_material,
    image_summary,
    inspect_image,
    load_image,
    material_result,
)
from .mesh_attribute_transfer_ops import transfer_attribute
from .mesh_batch_ops import MeshBatchExecutionError, execute_mesh_batch
from .mesh_component_catalog_ops import (
    inspect_component_catalog,
    prepare_component_catalog,
    release_component_catalog,
    select_component_catalog,
)
from .mesh_component_map import (
    compose_component_map,
    inspect_component_map,
    release_component_map,
    remap_selection,
)
from .mesh_deform_ops import DEFORM_OPERATIONS, edit_mesh_deform
from .mesh_materialization_ops import materialize_mesh
from .mesh_ops import (
    MeshOperationError,
    adopt_mesh_snapshots_for_native_save,
    edit_mesh,
    finalize_mesh_snapshots,
    inspect_mesh,
    restore_mesh_snapshots,
    touch_mesh_for_test,
    validate_mesh_snapshot_guards,
)
from .mesh_query_ops import (
    derive_selection,
    inspect_selection,
    query_selection,
    release_selection,
)
from .mesh_resource_model import MeshResourceBook, MeshResourceError
from .mesh_separation_ops import extract_mesh, extract_preflight, separate_mesh
from .mesh_surface_ops import prepare_surface, query_surface, validate_mesh
from .mesh_topology_ops import TOPOLOGY_OPERATIONS, edit_mesh_topology
from .mesh_uv_ops import edit_uv, inspect_uv
from .mesh_weight_ops import (
    adopt_weight_snapshots_for_native_save,
    edit_weights,
    finalize_weight_snapshots,
    inspect_weights,
    restore_weight_snapshots,
    validate_weight_snapshot_guards,
)
from .modifier_ops import (
    adopt_modifier_delta_for_native_save,
    clear_modifier_pending_deletes,
    create_modifier,
    delete_modifier,
    finalize_modifier_delta,
    inspect_modifiers,
    move_modifier,
    restore_modifier_delta,
    set_modifier,
    set_modifier_state_compat,
    touch_modifier_for_test,
    validate_modifier_stack_guards,
    validate_restored_modifier_stacks,
)
from .object_settings_ops import apply_object_settings
from .project_ops import (
    ProjectOperationError,
    normalized_path,
    open_project,
    project_status,
    quit_application,
    save_project,
    transition_needs_save,
    validate_open_path,
    validate_save_path,
)
from .rig_ops import bind_rig, inspect_rig
from .runtime import ADDON_VERSION, ListenerRuntime
from .scene_organization_ops import (
    change_collection_link,
    change_object_parent,
    create_collection,
    inspect_collection,
    organization_result,
)
from .structural_ops import (
    adopt_structural_delta_for_native_save,
    finalize_structural_delta,
    refresh_structure_guard_if_present,
    restore_structural_delta,
    validate_structural_transaction,
)
from .transaction_model import (
    IdempotencyCache,
    MaterialInputDelta,
    MeshEditDelta,
    ModifierCreateDelta,
    ModifierDeleteDelta,
    ModifierMoveDelta,
    ModifierSettingsDelta,
    ModifierStateDelta,
    ShapeKeyDelta,
    StructuralDelta,
    Transaction,
    TransactionBook,
    TransactionModelError,
    WeightEditDelta,
    changed_context_paths,
    context_fingerprint,
    request_fingerprint,
    transaction_context_state,
    user_ui_context_state,
    values_equal,
)
from .wire import PROTOCOL_VERSION
from .world_render_ops import render_preview, render_save, set_scene_camera, set_world

CAPABILITIES = [
    "connection.ping",
    "context.get",
    "context.snapshot",
    "context.restore",
    "object.inspect",
    "scene.inspect",
    "collection.inspect",
    "object.geometry.inspect",
    "mesh.inspect",
    "mesh.uv.inspect",
    "mesh.weights.inspect",
    "mesh.selection.query",
    "mesh.selection.derive",
    "mesh.selection.inspect",
    "mesh.selection.release",
    "mesh.component_catalog.prepare",
    "mesh.component_catalog.inspect",
    "mesh.component_catalog.select",
    "mesh.component_catalog.release",
    "mesh.component_map.inspect",
    "mesh.component_map.release",
    "mesh.component_map.compose",
    "mesh.selection.remap",
    "mesh.surface.prepare",
    "mesh.surface.query",
    "mesh.validate",
    "object.lookdev.inspect",
    "modifier.inspect",
    "material.inspect",
    "image.inspect",
    "viewport.capture",
    "viewport.raycast",
    "transaction.begin",
    "transaction.commit",
    "transaction.rollback",
    "object.set",
    "object.transform",
    "object.create",
    "object.duplicate",
    "object.delete",
    "collection.create",
    "collection.link_object",
    "collection.unlink_object",
    "object.parent.set",
    "object.parent.clear",
    "object.visibility.set",
    "modifier.set_state",
    "modifier.create",
    "modifier.set",
    "modifier.move",
    "modifier.delete",
    "mesh.edit",
    "mesh.uv.edit",
    "mesh.weights.edit",
    "mesh.attribute.transfer",
    "mesh.extract.preflight",
    "mesh.extract",
    "mesh.materialize",
    "mesh.separate",
    "mesh.batch.execute",
    "rig.inspect",
    "rig.bind",
    "shape_key.set_value",
    "material.set_input",
    "material.create",
    "material.assign",
    "image.load",
    "material.texture.bind",
    "material.texture.clear",
    "world.set",
    "scene.camera.set",
    "render.preview",
    "render.save",
    "project.status",
    "project.save",
    "project.open",
    "project.reload",
    "application.quit",
]
CAPABILITY_VERSIONS = {
    "transport": 1,
    "context": 1,
    "viewport_capture": 3,
    "viewport_raycast": 1,
    "geometry_inspection": 1,
    "lookdev_inspection": 1,
    "transactions": 11,
    "object_transform_scale": 1,
    "object_transform": 1,
    "object_settings": 1,
    "scene_inspection": 1,
    "object_authoring": 1,
    "material_authoring": 1,
    "image_assets": 1,
    "world_authoring": 1,
    "render_preview": 1,
    "render_export": 1,
    "object_visibility": 1,
    "modifier_state": 1,
    "modifier_authoring": 1,
    "mesh_topology": 4,
    "mesh_selection": 1,
    "mesh_surface_query": 1,
    "mesh_deformation": 1,
    "mesh_validation": 2,
    "mesh_component_map": 3,
    "mesh_component_catalog": 1,
    "mesh_separation": 2,
    "mesh_batch": 2,
    "mesh_uv": 1,
    "mesh_weights": 1,
    "mesh_attribute_transfer": 1,
    "mesh_materialization": 1,
    "mesh_extraction": 1,
    "rig_binding": 1,
    "collection_authoring": 1,
    "object_parenting": 1,
    "shape_key_value": 1,
    "material_input": 1,
    "project_lifecycle": 1,
    "application_lifecycle": 1,
}
MUTATION_COMMANDS = {
    "transaction.begin",
    "transaction.commit",
    "transaction.rollback",
    "object.set",
    "object.transform",
    "object.create",
    "object.duplicate",
    "object.delete",
    "collection.create",
    "collection.link_object",
    "collection.unlink_object",
    "object.parent.set",
    "object.parent.clear",
    "object.visibility.set",
    "modifier.set_state",
    "modifier.create",
    "modifier.set",
    "modifier.move",
    "modifier.delete",
    "mesh.edit",
    "mesh.uv.edit",
    "mesh.weights.edit",
    "mesh.attribute.transfer",
    "mesh.extract",
    "mesh.materialize",
    "mesh.separate",
    "mesh.batch.execute",
    "rig.bind",
    "shape_key.set_value",
    "material.set_input",
    "material.create",
    "material.assign",
    "image.load",
    "material.texture.bind",
    "material.texture.clear",
    "world.set",
    "scene.camera.set",
    "render.preview",
    "render.save",
    "project.save",
    "project.open",
    "project.reload",
    "application.quit",
}
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


class AddonState:
    def __init__(self) -> None:
        self.runtime = ListenerRuntime()
        self.scene_generation = 0
        self.heartbeat = 0
        self.active_command = ""
        self.last_command_ms = 0.0
        self.last_error = ""
        self.last_capture_backend = "gpu_offscreen"
        self.snapshots: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.captures = CaptureBook(limit=32)
        self.mesh_resources = MeshResourceBook()
        self.transactions = TransactionBook()
        self.idempotency = IdempotencyCache()
        self._suppress_generation = 0
        self._disconnect_rollback_deadline: float | None = None
        self.pending_lifecycle_operation: dict[str, Any] | None = None
        self.last_lifecycle_operation: dict[str, Any] | None = None
        self.user_intent_revision = 0
        self.last_user_action: dict[str, Any] | None = None
        self._native_save_operation: dict[str, Any] | None = None
        self._managed_save_depth = 0

    def start(self) -> None:
        self.runtime.start()

    def stop(self) -> None:
        try:
            if self.transactions.active is not None:
                self._rollback_transaction(self.transactions.active)
        except Exception as exc:  # noqa: BLE001 - shutdown must still close sockets
            self.last_error = f"ROLLBACK_FAILED: {type(exc).__name__}: {exc}"
        finally:
            self.runtime.stop()
            self.snapshots.clear()
            self.captures.clear()
            self.mesh_resources.clear()
            clear_modifier_pending_deletes()

    def restart(self) -> None:
        self.stop()
        self.start()

    def tick(self) -> None:
        self.heartbeat += 1
        self._perform_pending_lifecycle_operation()
        self.runtime.poll(self.dispatch, self.on_disconnect)
        if self._disconnect_rollback_deadline is not None:
            if self.runtime.connected:
                self._disconnect_rollback_deadline = None
            elif time.monotonic() >= self._disconnect_rollback_deadline:
                self._disconnect_rollback_deadline = None
                transaction = self.transactions.active
                if transaction is not None:
                    try:
                        transaction_id = transaction.transaction_id
                        self._rollback_transaction(transaction)
                        self.idempotency.remove_transaction(transaction_id)
                        self.transactions.last_status = "rolled_back_disconnect"
                    except Exception as exc:  # noqa: BLE001 - timer must remain registered
                        transaction.status = "conflicted"
                        self.transactions.last_status = "conflicted"
                        code = getattr(exc, "code", type(exc).__name__)
                        self.last_error = f"{code}: {exc}"

    def on_disconnect(self) -> None:
        if self.transactions.active is not None:
            self._disconnect_rollback_deadline = time.monotonic() + 2.0

    def on_file_loaded(self) -> None:
        self.snapshots.clear()
        self.captures.clear()
        self.mesh_resources.clear()
        clear_modifier_pending_deletes()
        if self.transactions.active is not None:
            self.transactions.abandon("abandoned_file_load")
            self.last_error = "TRANSACTION_ABANDONED: a different blend file was loaded"
        self.idempotency = IdempotencyCache()
        self._disconnect_rollback_deadline = None
        self.scene_generation += 1

    def on_native_save_pre(self, filepath: str) -> None:
        """Accept the current visible state before Blender serializes a native save."""

        if self._managed_save_depth > 0:
            return
        self.user_intent_revision += 1
        operation = {
            "operation_id": str(uuid.uuid4()),
            "kind": "native_save",
            "status": "accepted",
            "path": str(filepath or bpy.data.filepath),
            "user_intent_revision": self.user_intent_revision,
        }
        transaction = self._adopt_active_transaction_for_native_save(operation)
        operation["transaction"] = transaction
        self._native_save_operation = operation
        self.last_user_action = operation
        self.last_lifecycle_operation = operation

    def on_native_save_post(self, filepath: str) -> None:
        if self._managed_save_depth > 0:
            return
        operation = self._native_save_operation
        if operation is None:
            return
        operation["status"] = "succeeded"
        operation["path"] = str(filepath or bpy.data.filepath)
        self._native_save_operation = None

    def on_native_save_failed(self, filepath: str) -> None:
        if self._managed_save_depth > 0:
            return
        operation = self._native_save_operation
        if operation is None:
            return
        operation["status"] = "failed"
        operation["path"] = str(filepath or bpy.data.filepath)
        self.last_error = "NATIVE_SAVE_FAILED: Blender did not write the requested file"
        self._native_save_operation = None

    def on_depsgraph_update(self, depsgraph: Any) -> None:
        if self._suppress_generation == 0 and has_persistent_scene_update(depsgraph):
            self.scene_generation += 1

    @contextlib.contextmanager
    def suppress_generation(self) -> Iterator[None]:
        self._suppress_generation += 1
        try:
            yield
        finally:
            try:
                if self._suppress_generation == 1 and self.active_command in MUTATION_COMMANDS:
                    view_layer = getattr(bpy.context, "view_layer", None)
                    if view_layer is not None:
                        view_layer.update()
            finally:
                self._suppress_generation -= 1

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        started = time.perf_counter()
        command = str(request.get("command", ""))
        self.active_command = command
        idempotency_key: str | None = None
        fingerprint: str | None = None
        try:
            if request.get("protocol") != PROTOCOL_VERSION:
                return self._error(
                    request_id,
                    "protocol_version",
                    "PROTOCOL_MISMATCH",
                    f"Protocol {request.get('protocol')} is not supported",
                )
            params = request.get("params", {})
            if not isinstance(params, dict):
                return self._error(
                    request_id,
                    "validation",
                    "PARAMS_INVALID",
                    "params must be a JSON object",
                )
            if command in MUTATION_COMMANDS:
                idempotency_key = request.get("idempotency_key")
                if not isinstance(idempotency_key, str) or not idempotency_key:
                    return self._error(
                        request_id,
                        "validation",
                        "IDEMPOTENCY_KEY_REQUIRED",
                        "Mutation commands require an idempotency_key",
                    )
                fingerprint = request_fingerprint(request)
                cached = self.idempotency.lookup(idempotency_key, fingerprint)
                if cached is not None:
                    cached["request_id"] = request_id
                    return cached
            result = self._handle(command, params, request)
            response = {
                "protocol": PROTOCOL_VERSION,
                "request_id": request_id,
                "ok": True,
                "scene_generation": self.scene_generation,
                "result": result,
            }
            if idempotency_key is not None and fingerprint is not None:
                self.idempotency.store(idempotency_key, fingerprint, response)
            return response
        except ContextOperationError as exc:
            self.last_error = f"{exc.code}: {exc}"
            return self._error(
                request_id,
                exc.kind,
                exc.code,
                str(exc),
                retryable=exc.retryable,
                details=exc.details,
            )
        except TransactionModelError as exc:
            self.last_error = f"{exc.code}: {exc}"
            kind = "conflict" if exc.code != "TRANSACTION_NOT_FOUND" else "not_found"
            return self._error(request_id, kind, exc.code, str(exc))
        except ProjectOperationError as exc:
            self.last_error = f"{exc.code}: {exc}"
            return self._error(
                request_id,
                exc.kind,
                exc.code,
                str(exc),
                retryable=exc.retryable,
                details=exc.details,
            )
        except AuthoringOperationError as exc:
            self.last_error = f"{exc.code}: {exc}"
            return self._error(
                request_id,
                exc.kind,
                exc.code,
                str(exc),
                details=exc.details,
            )
        except MeshOperationError as exc:
            self.last_error = f"{exc.code}: {exc}"
            return self._error(
                request_id,
                exc.kind,
                exc.code,
                str(exc),
                details=exc.details,
            )
        except MeshResourceError as exc:
            self.last_error = f"{exc.code}: {exc}"
            return self._error(
                request_id,
                exc.kind,
                exc.code,
                str(exc),
                details=exc.details,
            )
        except Exception as exc:  # noqa: BLE001 - dispatch boundary
            self.last_error = f"{type(exc).__name__}: {exc}"
            return self._error(
                request_id,
                "blender_api",
                "BLENDER_COMMAND_FAILED",
                f"Blender command failed: {type(exc).__name__}",
                details={"error_type": type(exc).__name__, "message": str(exc)},
            )
        finally:
            self.last_command_ms = (time.perf_counter() - started) * 1000
            self.active_command = ""

    def _handle(
        self,
        command: str,
        params: dict[str, Any],
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if command == "connection.hello":
            if params.get("expected_instance_id") != self.runtime.instance_id:
                raise ContextOperationError(
                    "INSTANCE_MISMATCH",
                    "Handshake instance does not match the running add-on",
                    kind="protocol_version",
                )
            if not (
                params.get("protocol_min") == PROTOCOL_VERSION
                and params.get("protocol_max") == PROTOCOL_VERSION
            ):
                raise ContextOperationError(
                    "PROTOCOL_MISMATCH",
                    "Client and add-on have no common protocol version",
                    kind="protocol_version",
                )
            return {
                "protocol": PROTOCOL_VERSION,
                "instance_id": self.runtime.instance_id,
                "blender_version": bpy.app.version_string,
                "addon_version": ADDON_VERSION,
                "capabilities": CAPABILITIES,
                "capability_versions": CAPABILITY_VERSIONS,
            }
        if command == "connection.ping":
            return {
                "protocol": PROTOCOL_VERSION,
                "instance_id": self.runtime.instance_id,
                "blender_version": bpy.app.version_string,
                "addon_version": ADDON_VERSION,
                "capabilities": CAPABILITIES,
                "capability_versions": CAPABILITY_VERSIONS,
                "capture_backend": self.last_capture_backend,
                "capture_focus_requirement": "none_when_window_exists",
                "heartbeat": self.heartbeat,
                "last_command_ms": self.last_command_ms,
                "user_intent_revision": self.user_intent_revision,
                "last_user_action": self.last_user_action,
            }
        if command == "_test.structure.touch":
            if os.environ.get("BLENDER_RESEARCH_MCP_TEST_HOOKS") != "1":
                raise ContextOperationError(
                    "COMMAND_NOT_FOUND",
                    f"Unsupported command: {command}",
                    kind="not_found",
                )
            object_name = params.get("object_name")
            obj = bpy.data.objects.get(str(object_name))
            if obj is None:
                raise ContextOperationError(
                    "OBJECT_NOT_FOUND",
                    f"Object does not exist: {object_name}",
                    kind="not_found",
                )
            action = params.get("action", "object_location")
            with self.suppress_generation():
                if action == "object_location":
                    obj.location.x = float(obj.location.x) + 0.25
                    result = {
                        "location": list(obj.location),
                    }
                elif action == "linked_duplicate":
                    name = params.get("name")
                    if not isinstance(name, str) or not name:
                        raise ContextOperationError(
                            "TEST_STRUCTURE_TOUCH_INVALID",
                            "linked_duplicate requires a non-empty name",
                            kind="validation",
                        )
                    if bpy.data.objects.get(name) is not None:
                        raise ContextOperationError(
                            "OBJECT_NAME_CONFLICT",
                            f"An object already uses the exact name: {name}",
                            kind="conflict",
                        )
                    duplicate = obj.copy()
                    duplicate.name = name
                    collection = (
                        obj.users_collection[0]
                        if obj.users_collection
                        else bpy.context.scene.collection
                    )
                    collection.objects.link(duplicate)
                    result = {
                        "linked_duplicate": duplicate.name,
                        "linked_duplicate_identity": session_identity("object", duplicate),
                        "data_users": int(obj.data.users) if obj.data is not None else None,
                    }
                else:
                    raise ContextOperationError(
                        "TEST_STRUCTURE_TOUCH_INVALID",
                        f"Unsupported structure touch action: {action}",
                        kind="validation",
                    )
                bpy.context.view_layer.update()
            return {
                "test_hook": "structure_touch",
                "action": action,
                "object_name": obj.name,
                "object_identity": session_identity("object", obj),
                **result,
            }
        if command == "_test.property.touch":
            if os.environ.get("BLENDER_RESEARCH_MCP_TEST_HOOKS") != "1":
                raise ContextOperationError(
                    "COMMAND_NOT_FOUND",
                    f"Unsupported command: {command}",
                    kind="not_found",
                )
            target = params.get("target")
            value = params.get("value")
            if (
                not isinstance(target, dict)
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
            ):
                raise ContextOperationError(
                    "TEST_PROPERTY_TOUCH_INVALID",
                    "target and a finite numeric value are required",
                    kind="validation",
                )
            value = float(value)
            if not math.isfinite(value):
                raise ContextOperationError(
                    "TEST_PROPERTY_TOUCH_INVALID",
                    "value must be finite",
                    kind="validation",
                )
            target_type = target.get("type")
            if target_type == "shape_key_value":
                _obj, property_target = require_shape_key(
                    str(target.get("object_name")),
                    str(target.get("expected_object_identity")),
                    str(target.get("shape_key_name")),
                    str(target.get("expected_shape_key_identity")),
                )
                attribute = "value"
            elif target_type == "material_input":
                _obj, _material, _node, property_target = resolve_material_socket(
                    str(target.get("object_name")),
                    str(target.get("expected_object_identity")),
                    int(target.get("material_slot_index")),
                    str(target.get("material_name")),
                    str(target.get("expected_material_identity")),
                    str(target.get("node_name")),
                    str(target.get("expected_node_identity")),
                    str(target.get("socket_identifier")),
                    str(target.get("expected_socket_identity")),
                )
                attribute = "default_value"
            else:
                raise ContextOperationError(
                    "TEST_PROPERTY_TOUCH_INVALID",
                    f"Unsupported test target: {target_type}",
                    kind="validation",
                )
            with self.suppress_generation():
                setattr(property_target, attribute, value)
                bpy.context.view_layer.update()
            return {
                "test_hook": "property_touch",
                "target_type": target_type,
                "value": value,
            }
        if command == "_test.modifier.touch":
            if os.environ.get("BLENDER_RESEARCH_MCP_TEST_HOOKS") != "1":
                raise ContextOperationError(
                    "COMMAND_NOT_FOUND",
                    f"Unsupported command: {command}",
                    kind="not_found",
                )
            with self.suppress_generation():
                return touch_modifier_for_test(params)
        if command == "_test.mesh.touch":
            if os.environ.get("BLENDER_RESEARCH_MCP_TEST_HOOKS") != "1":
                raise ContextOperationError(
                    "COMMAND_NOT_FOUND",
                    f"Unsupported command: {command}",
                    kind="not_found",
                )
            with self.suppress_generation():
                return touch_mesh_for_test(params)
        if command == "_test.context.touch":
            if os.environ.get("BLENDER_RESEARCH_MCP_TEST_HOOKS") != "1":
                raise ContextOperationError(
                    "COMMAND_NOT_FOUND",
                    f"Unsupported command: {command}",
                    kind="not_found",
                )
            viewport = resolve_viewport(
                str(params["viewport_id"]) if params.get("viewport_id") else None
            )
            region_3d = viewport.space.region_3d
            active_name = params.get("active_object")
            active = None
            if active_name is not None:
                active = bpy.data.objects.get(str(active_name))
                if active is None or active.name not in viewport.window.view_layer.objects:
                    raise ContextOperationError(
                        "OBJECT_NOT_FOUND",
                        f"Test UI object does not exist in the active View Layer: {active_name}",
                        kind="not_found",
                    )
            shading = str(params.get("shading", "WIREFRAME"))
            if shading not in {"WIREFRAME", "SOLID", "MATERIAL", "RENDERED"}:
                raise ContextOperationError(
                    "TEST_CONTEXT_TOUCH_INVALID",
                    f"Unsupported test shading: {shading}",
                    kind="validation",
                )
            with self.suppress_generation():
                region_3d.view_location = tuple(
                    float(value) + offset
                    for value, offset in zip(
                        region_3d.view_location,
                        (0.75, -0.5, 0.25),
                        strict=True,
                    )
                )
                region_3d.view_rotation = (0.9659258, 0.0, 0.0, 0.258819)
                region_3d.view_distance = max(0.1, float(region_3d.view_distance) * 0.72)
                region_3d.view_perspective = (
                    "ORTHO" if region_3d.view_perspective != "ORTHO" else "PERSP"
                )
                viewport.space.lens = min(250.0, float(viewport.space.lens) + 7.0)
                viewport.space.shading.type = shading
                viewport.space.overlay.show_overlays = bool(params.get("show_overlays", False))
                if active_name is not None:
                    for obj in viewport.window.view_layer.objects:
                        obj.select_set(False)
                    assert active is not None
                    active.select_set(True)
                    viewport.window.view_layer.objects.active = active
                region_3d.update()
            return {
                "test_hook": "context_touch",
                "context": capture_context(viewport.viewport_id),
            }
        if command == "_test.native_save":
            if os.environ.get("BLENDER_RESEARCH_MCP_TEST_HOOKS") != "1":
                raise ContextOperationError(
                    "COMMAND_NOT_FOUND",
                    f"Unsupported command: {command}",
                    kind="not_found",
                )
            path = params.get("path")
            if path is None:
                result = bpy.ops.wm.save_mainfile()
            else:
                result = bpy.ops.wm.save_as_mainfile(
                    filepath=str(path),
                    check_existing=False,
                )
            if "FINISHED" not in result:
                raise ContextOperationError(
                    "TEST_NATIVE_SAVE_FAILED",
                    f"Blender native save returned: {sorted(result)}",
                    kind="blender_api",
                )
            return {
                "test_hook": "native_save",
                "operator_result": sorted(result),
                "last_user_action": self.last_user_action,
            }
        if command == "context.get":
            with self.suppress_generation():
                return context_summary()
        if command == "project.status":
            return self._project_status()
        if command == "project.save":
            return self._save_project(params)
        if command == "project.open":
            return self._open_project(params)
        if command == "project.reload":
            return self._reload_project(params)
        if command == "application.quit":
            return self._quit_application(params)
        if command == "context.snapshot":
            with self.suppress_generation():
                snapshot = capture_context(params.get("viewport_id"))
            snapshot_id = str(uuid.uuid4())
            self.snapshots[snapshot_id] = snapshot
            self.snapshots.move_to_end(snapshot_id)
            while len(self.snapshots) > 32:
                self.snapshots.popitem(last=False)
            return {"snapshot_id": snapshot_id, "context": snapshot}
        if command == "context.restore":
            snapshot_id = params.get("snapshot_id")
            snapshot = self.snapshots.get(str(snapshot_id))
            if snapshot is None:
                raise ContextOperationError(
                    "SNAPSHOT_NOT_FOUND",
                    f"Context snapshot does not exist: {snapshot_id}",
                    kind="not_found",
                )
            with self.suppress_generation():
                restore_context(snapshot)
            return {"snapshot_id": snapshot_id, "restored": True, "context": snapshot}
        if command == "object.inspect":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            return inspect_object(object_name)
        if command == "scene.inspect":
            kinds = params.get("kinds")
            allowed_kinds = {
                "objects",
                "collections",
                "materials",
                "images",
                "world",
                "camera",
                "render",
            }
            if (
                not isinstance(kinds, list)
                or not kinds
                or len(kinds) > len(allowed_kinds)
                or any(not isinstance(kind, str) or kind not in allowed_kinds for kind in kinds)
                or len(set(kinds)) != len(kinds)
            ):
                raise AuthoringOperationError(
                    "SCENE_KINDS_INVALID",
                    "kinds must contain unique supported scene summary kinds",
                    kind="validation",
                )
            name_filter = params.get("name_filter")
            if name_filter is not None and (
                not isinstance(name_filter, str) or not name_filter or len(name_filter) > 255
            ):
                raise AuthoringOperationError(
                    "NAME_FILTER_INVALID",
                    "name_filter must be a non-empty string or null",
                    kind="validation",
                )
            limit = params.get("limit", 100)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
                raise AuthoringOperationError(
                    "SCENE_LIMIT_INVALID",
                    "limit must be an integer between 1 and 256",
                    kind="validation",
                )
            return inspect_scene(kinds, name_filter, limit)
        if command == "collection.inspect":
            name = params.get("collection_name")
            if not isinstance(name, str) or not name:
                raise AuthoringOperationError(
                    "COLLECTION_NAME_INVALID", "collection_name must be non-empty"
                )
            offset = params.get("offset", 0)
            limit = params.get("limit", 256)
            if isinstance(offset, bool) or not isinstance(offset, int):
                raise AuthoringOperationError(
                    "COLLECTION_PAGINATION_INVALID", "offset must be an integer"
                )
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise AuthoringOperationError(
                    "COLLECTION_PAGINATION_INVALID", "limit must be an integer"
                )
            return inspect_collection(name, offset, limit)
        if command == "object.geometry.inspect":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            with self.suppress_generation():
                return inspect_geometry(object_name)
        if command == "mesh.inspect":
            object_name = params.get("object_name")
            component = params.get("component", "summary")
            offset = params.get("offset", 0)
            limit = params.get("limit", 256)
            if not isinstance(object_name, str) or not object_name:
                raise MeshOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                )
            if not isinstance(component, str):
                raise MeshOperationError("MESH_COMPONENT_INVALID", "component must be a string")
            if isinstance(offset, bool) or not isinstance(offset, int):
                raise MeshOperationError("MESH_PAGINATION_INVALID", "offset must be an integer")
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise MeshOperationError("MESH_PAGINATION_INVALID", "limit must be an integer")
            with self.suppress_generation():
                result = inspect_mesh(object_name, component, offset, limit)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.uv.inspect":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise MeshOperationError("OBJECT_NAME_INVALID", "object_name must be non-empty")
            layer_name = params.get("layer_name")
            if layer_name is not None and (not isinstance(layer_name, str) or not layer_name):
                raise MeshOperationError(
                    "MESH_UV_LAYER_NAME_INVALID", "layer_name must be non-empty or null"
                )
            with self.suppress_generation():
                result = inspect_uv(
                    object_name,
                    layer_name,
                    str(params.get("component", "SUMMARY")),
                    int(params.get("offset", 0)),
                    int(params.get("limit", 256)),
                )
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.weights.inspect":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise MeshOperationError("OBJECT_NAME_INVALID", "object_name must be non-empty")
            group_name = params.get("group_name")
            if group_name is not None and (not isinstance(group_name, str) or not group_name):
                raise MeshOperationError(
                    "MESH_WEIGHT_GROUP_NAME_INVALID", "group_name must be non-empty or null"
                )
            with self.suppress_generation():
                result = inspect_weights(
                    object_name,
                    group_name,
                    str(params.get("component", "SUMMARY")),
                    int(params.get("offset", 0)),
                    int(params.get("limit", 256)),
                )
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.selection.query":
            with self.suppress_generation():
                result = query_selection(self.mesh_resources, self.captures, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.selection.derive":
            with self.suppress_generation():
                result = derive_selection(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.selection.inspect":
            with self.suppress_generation():
                result = inspect_selection(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.selection.release":
            result = release_selection(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.component_catalog.prepare":
            with self.suppress_generation():
                result = prepare_component_catalog(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.component_catalog.inspect":
            with self.suppress_generation():
                result = inspect_component_catalog(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.component_catalog.select":
            with self.suppress_generation():
                result = select_component_catalog(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.component_catalog.release":
            result = release_component_catalog(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.component_map.inspect":
            result = inspect_component_map(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.component_map.release":
            result = release_component_map(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.component_map.compose":
            with self.suppress_generation():
                result = compose_component_map(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.selection.remap":
            with self.suppress_generation():
                result = remap_selection(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.surface.prepare":
            with self.suppress_generation():
                result = prepare_surface(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.surface.query":
            with self.suppress_generation():
                result = query_surface(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "mesh.validate":
            with self.suppress_generation():
                result = validate_mesh(self.mesh_resources, params)
            result["scene_generation"] = self.scene_generation
            return result
        if command == "object.lookdev.inspect":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            with self.suppress_generation():
                return inspect_object_lookdev(object_name)
        if command == "modifier.inspect":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise AuthoringOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            with self.suppress_generation():
                return inspect_modifiers(object_name, self.scene_generation)
        if command == "material.inspect":
            object_name = params.get("object_name")
            material_slot_index = params.get("material_slot_index")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            if (
                isinstance(material_slot_index, bool)
                or not isinstance(material_slot_index, int)
                or not 0 <= material_slot_index < 64
            ):
                raise ContextOperationError(
                    "MATERIAL_SLOT_INDEX_INVALID",
                    "material_slot_index must be an integer between 0 and 63",
                    kind="validation",
                )
            with self.suppress_generation():
                return inspect_material(object_name, material_slot_index)
        if command == "image.inspect":
            image_name = params.get("image_name")
            if not isinstance(image_name, str) or not image_name:
                raise AuthoringOperationError(
                    "IMAGE_NAME_INVALID",
                    "image_name must be a non-empty string",
                    kind="validation",
                )
            return inspect_image(image_name)
        if command == "render.preview":
            self._require_scene_generation(request)
            with self.suppress_generation():
                return render_preview(params)
        if command == "render.save":
            self._require_scene_generation(request)
            with self.suppress_generation():
                return render_save(params)
        if command == "viewport.capture":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            view_reference: CaptureEvidence | None = None
            view_reference_capture_id = params.get("_view_reference_capture_id")
            if view_reference_capture_id is not None:
                if not isinstance(view_reference_capture_id, str) or not view_reference_capture_id:
                    raise ContextOperationError(
                        "CAPTURE_REFERENCE_INVALID",
                        "_view_reference_capture_id must be a non-empty string",
                        kind="validation",
                    )
                view_reference = self.captures.get(view_reference_capture_id)
                if view_reference is None:
                    raise ContextOperationError(
                        "CAPTURE_REFERENCE_NOT_FOUND",
                        f"Capture view reference does not exist: {view_reference_capture_id}",
                        kind="not_found",
                    )
            with self.suppress_generation():
                result, evidence_data = capture_viewport(
                    object_name,
                    str(params.get("view", "CURRENT")),
                    int(params.get("max_size", 800)),
                    params.get("viewport_id"),
                    str(params.get("display_mode", "CURRENT")),
                    str(params.get("overlays", "CURRENT")),
                    params.get("orbit"),
                    view_reference,
                )
            capture_id = str(uuid.uuid4())
            evidence = CaptureEvidence(
                capture_id=capture_id,
                scene_generation=self.scene_generation,
                **evidence_data,
            )
            self.captures.add(evidence)
            result["capture_id"] = capture_id
            result["capture_scene_generation"] = self.scene_generation
            self.last_capture_backend = str(result["backend"])
            return result
        if command == "viewport.raycast":
            capture_id = params.get("capture_id")
            if not isinstance(capture_id, str) or not capture_id:
                raise ContextOperationError(
                    "CAPTURE_ID_INVALID",
                    "capture_id must be a non-empty string",
                    kind="validation",
                )
            evidence = self.captures.get(capture_id)
            if evidence is None:
                raise ContextOperationError(
                    "CAPTURE_NOT_FOUND",
                    f"Capture evidence does not exist: {capture_id}",
                    kind="not_found",
                )
            if evidence.scene_generation != self.scene_generation:
                raise ContextOperationError(
                    "CAPTURE_STALE",
                    "The Blender scene changed after the capture was created",
                    kind="conflict",
                    retryable=True,
                    details={
                        "capture_scene_generation": evidence.scene_generation,
                        "current_scene_generation": self.scene_generation,
                    },
                )
            x = params.get("x")
            y = params.get("y")
            if (
                isinstance(x, bool)
                or not isinstance(x, (int, float))
                or isinstance(y, bool)
                or not isinstance(y, (int, float))
                or not 0.0 <= float(x) <= 1.0
                or not 0.0 <= float(y) <= 1.0
            ):
                raise ContextOperationError(
                    "RAYCAST_COORDINATE_INVALID",
                    "x and y must be finite normalized coordinates between 0 and 1",
                    kind="validation",
                )
            with self.suppress_generation():
                result = raycast_capture(evidence, float(x), float(y))
            result["scene_generation"] = self.scene_generation
            return result
        if command == "transaction.begin":
            self._require_scene_generation(request)
            with self.suppress_generation():
                snapshot = capture_context(params.get("viewport_id"))
            label = params.get("label")
            if label is not None and not isinstance(label, str):
                raise ContextOperationError(
                    "LABEL_INVALID",
                    "label must be a string or null",
                    kind="validation",
                )
            if isinstance(label, str) and len(label) > 200:
                raise ContextOperationError(
                    "LABEL_INVALID",
                    "label must not exceed 200 characters",
                    kind="validation",
                )
            transaction = self.transactions.begin(
                label=label,
                context_snapshot=snapshot,
                context_fingerprint=context_fingerprint(transaction_context_state(snapshot)),
                scene_generation=self.scene_generation,
            )
            return self._transaction_result(transaction)
        if command == "object.set":
            transaction = self._require_transaction(params, request)
            return self._set_object_settings(transaction, params)
        if command == "object.transform":
            transaction = self._require_transaction(params, request)
            return self._transform_object(transaction, params)
        if command == "object.create":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            definition = params.get("definition")
            if not isinstance(definition, dict):
                raise AuthoringOperationError(
                    "OBJECT_DEFINITION_INVALID",
                    "definition must be an object",
                    kind="validation",
                )
            with self.suppress_generation():
                obj, delta = create_object(transaction, definition)
                bpy.context.view_layer.update()
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "object": object_summary(obj),
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "object.duplicate":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                obj, delta = duplicate_object(
                    transaction,
                    source_name=str(params.get("source_name", "")),
                    expected_source_identity=self._required_identity(
                        params, "expected_source_identity"
                    ),
                    name=str(params.get("name", "")),
                    linked_data=params.get("linked_data") is True,
                    collection_name=params.get("collection_name"),
                    expected_collection_identity=params.get("expected_collection_identity"),
                    transform=params.get("transform"),
                )
                bpy.context.view_layer.update()
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "object": object_summary(obj),
                "linked_data": params.get("linked_data") is True,
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "object.delete":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                obj, delta = unlink_object(
                    transaction,
                    object_name=str(params.get("object_name", "")),
                    expected_object_identity=self._required_identity(
                        params, "expected_object_identity"
                    ),
                )
                before_commit = object_summary(obj)
                bpy.context.view_layer.update()
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "object": before_commit,
                "status": "unlinked_pending_commit",
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "collection.create":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                collection, delta = create_collection(transaction, params)
                bpy.context.view_layer.update()
            self._record_delta(transaction, delta)
            result = organization_result(
                transaction, changed=True, collection=collection
            )
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command in {"collection.link_object", "collection.unlink_object"}:
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                changed, delta, collection, obj = change_collection_link(
                    transaction,
                    params,
                    link=command == "collection.link_object",
                )
                bpy.context.view_layer.update()
            if delta is not None:
                self._record_delta(transaction, delta)
            result = organization_result(
                transaction,
                changed=changed,
                collection=collection,
                obj=obj,
            )
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command in {"object.parent.set", "object.parent.clear"}:
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                changed, delta, obj = change_object_parent(
                    transaction,
                    params,
                    clear=command == "object.parent.clear",
                )
                bpy.context.view_layer.update()
            if delta is not None:
                self._record_delta(transaction, delta)
            result = organization_result(transaction, changed=changed, obj=obj)
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command == "mesh.edit":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            previous_count = len(transaction.deltas)
            with self.suppress_generation():
                operation = params.get("operation")
                operation_type = operation.get("type") if isinstance(operation, dict) else None
                if operation_type in DEFORM_OPERATIONS:
                    result = edit_mesh_deform(
                        transaction, self.mesh_resources, self.captures, params
                    )
                elif operation_type in TOPOLOGY_OPERATIONS:
                    result = edit_mesh_topology(transaction, self.mesh_resources, params)
                else:
                    result = edit_mesh(transaction, params, self.mesh_resources)
                bpy.context.view_layer.update()
            if len(transaction.deltas) > previous_count:
                self.scene_generation += 1
                transaction.status = "active"
                transaction.context_fingerprint = self._current_context_fingerprint(transaction)
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command == "mesh.uv.edit":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            previous_count = len(transaction.deltas)
            with self.suppress_generation():
                result = edit_uv(transaction, self.mesh_resources, params)
                bpy.context.view_layer.update()
            if len(transaction.deltas) > previous_count:
                self.scene_generation += 1
                transaction.status = "active"
                transaction.context_fingerprint = self._current_context_fingerprint(transaction)
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command == "mesh.weights.edit":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            previous_count = len(transaction.deltas)
            with self.suppress_generation():
                result = edit_weights(transaction, self.mesh_resources, params)
                bpy.context.view_layer.update()
            if len(transaction.deltas) > previous_count:
                self.scene_generation += 1
                transaction.status = "active"
                transaction.context_fingerprint = self._current_context_fingerprint(transaction)
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command == "mesh.attribute.transfer":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            previous_count = len(transaction.deltas)
            with self.suppress_generation():
                result = transfer_attribute(transaction, self.mesh_resources, params)
                bpy.context.view_layer.update()
            if len(transaction.deltas) > previous_count:
                self.scene_generation += 1
                transaction.status = "active"
                transaction.context_fingerprint = self._current_context_fingerprint(transaction)
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command == "rig.inspect":
            return inspect_rig(
                str(params.get("object_name", "")),
                (
                    str(params["armature_object_name"])
                    if params.get("armature_object_name") is not None
                    else None
                ),
                int(params.get("offset", 0)),
                int(params.get("limit", 256)),
            )
        if command == "rig.bind":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            previous_count = len(transaction.deltas)
            with self.suppress_generation():
                result = bind_rig(transaction, params)
                bpy.context.view_layer.update()
            if len(transaction.deltas) > previous_count:
                self.scene_generation += 1
                transaction.status = "active"
                transaction.context_fingerprint = self._current_context_fingerprint(transaction)
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command == "mesh.extract.preflight":
            return extract_preflight(self.mesh_resources, params)
        if command == "mesh.extract":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            previous_count = len(transaction.deltas)
            with self.suppress_generation():
                result = extract_mesh(transaction, self.mesh_resources, params)
                bpy.context.view_layer.update()
            if len(transaction.deltas) > previous_count:
                self.scene_generation += 1
                transaction.status = "active"
                transaction.context_fingerprint = self._current_context_fingerprint(transaction)
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command == "mesh.materialize":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            previous_count = len(transaction.deltas)
            with self.suppress_generation():
                result = materialize_mesh(transaction, self.mesh_resources, params)
                bpy.context.view_layer.update()
            if len(transaction.deltas) > previous_count:
                self.scene_generation += 1
                transaction.status = "active"
                transaction.context_fingerprint = self._current_context_fingerprint(transaction)
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command == "mesh.separate":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            previous_count = len(transaction.deltas)
            with self.suppress_generation():
                result = separate_mesh(transaction, self.mesh_resources, params)
                bpy.context.view_layer.update()
            if len(transaction.deltas) > previous_count:
                self.scene_generation += 1
                transaction.status = "active"
                transaction.context_fingerprint = self._current_context_fingerprint(transaction)
            result.update(
                {
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command == "mesh.batch.execute":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            previous_count = len(transaction.deltas)
            try:
                with self.suppress_generation():
                    result = execute_mesh_batch(
                        transaction, self.mesh_resources, self.captures, params
                    )
                    bpy.context.view_layer.update()
            except MeshBatchExecutionError as exc:
                cause = exc.cause
                error_code = getattr(cause, "code", "MESH_BATCH_INVALID")
                error_kind = getattr(cause, "kind", "validation")
                batch_details = {
                    "batch_id": exc.batch_id,
                    "step_index": exc.step_index,
                    "step_type": exc.step_type,
                    "aliases": list(exc.aliases),
                    "underlying_code": error_code,
                    "underlying_details": dict(getattr(cause, "details", {})),
                }
                try:
                    rollback = self._rollback_transaction(transaction)
                except Exception as restore_error:
                    raise MeshOperationError(
                        "MESH_BATCH_RESTORE_FAILED",
                        "Mesh batch failed and the complete transaction could not be restored",
                        kind="conflict",
                        details={
                            **batch_details,
                            "failure": str(cause),
                            "restore_error_type": type(restore_error).__name__,
                            "restore_error": str(restore_error),
                        },
                    ) from restore_error
                raise MeshOperationError(
                    error_code,
                    str(cause),
                    kind=error_kind,
                    details={**batch_details, "rollback": rollback},
                ) from cause
            if len(transaction.deltas) > previous_count:
                self.scene_generation += 1
                transaction.status = "active"
                transaction.context_fingerprint = self._current_context_fingerprint(transaction)
            result.update(
                {
                    "scene_generation": self.scene_generation,
                    "status": transaction.status,
                    "delta_count": len(transaction.deltas),
                    "delta_kinds": transaction.delta_kinds(),
                }
            )
            return result
        if command == "object.visibility.set":
            transaction = self._require_transaction(params, request)
            return self._set_object_visibility(transaction, params)
        if command == "modifier.set_state":
            transaction = self._require_transaction(params, request)
            return self._set_modifier_state(transaction, params)
        if command in {"modifier.create", "modifier.set", "modifier.move", "modifier.delete"}:
            transaction = self._require_transaction(params, request)
            return self._run_modifier_write(transaction, command, params)
        if command == "shape_key.set_value":
            transaction = self._require_transaction(params, request)
            return self._set_shape_key_value(transaction, params)
        if command == "material.set_input":
            transaction = self._require_transaction(params, request)
            return self._set_material_input(transaction, params)
        if command == "material.create":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            definition = params.get("definition")
            if not isinstance(definition, dict):
                raise AuthoringOperationError(
                    "MATERIAL_DEFINITION_INVALID",
                    "definition must be an object",
                    kind="validation",
                )
            with self.suppress_generation():
                material, delta = create_material(transaction, definition)
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "material": material_result(material),
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "material.assign":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                obj, delta, slot_index = assign_material(transaction, params)
                bpy.context.view_layer.update()
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "assignment": assignment_result(obj, slot_index),
                "mode": params.get("mode"),
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "image.load":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                image, delta, reused = load_image(
                    transaction,
                    params.get("path"),
                    str(params.get("colorspace", "AUTO")),
                )
            if delta is not None:
                self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "image": image_summary(image),
                "reused": reused,
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "material.texture.bind":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                material, delta, binding = bind_texture(transaction, params)
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "material": material_result(material),
                "binding": binding,
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "material.texture.clear":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                material, delta, removed_links = clear_texture(transaction, params)
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "material": material_result(material),
                "channel": params.get("channel"),
                "removed_link_identities": removed_links,
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "world.set":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                _world, delta, world_result = set_world(transaction, params)
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "world": world_result,
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "scene.camera.set":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                camera, delta = set_scene_camera(
                    transaction,
                    str(params.get("camera_name", "")),
                    self._required_identity(params, "expected_camera_identity"),
                )
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "camera": object_summary(camera),
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "transaction.commit":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            result = self._transaction_result(transaction)
            finalized: list[dict[str, Any]] = []
            with self.suppress_generation():
                for delta in transaction.deltas:
                    item = finalize_modifier_delta(delta)
                    if item is not None:
                        finalized.append(item)
                finalized.extend(finalize_weight_snapshots(transaction))
                finalized.extend(finalize_mesh_snapshots(transaction))
                for delta in transaction.structural_deltas():
                    item = finalize_structural_delta(delta)
                    if item is not None:
                        finalized.append(item)
            if finalized:
                self.scene_generation += 1
            result["status"] = "committed"
            result["finalized"] = finalized
            self.transactions.finish(transaction, "committed")
            return result
        if command == "transaction.rollback":
            transaction = self._require_transaction(params, request)
            return self._rollback_transaction(transaction)
        raise ContextOperationError(
            "COMMAND_NOT_FOUND",
            f"Unsupported command: {command}",
            kind="not_found",
        )

    def _project_status(self) -> dict[str, Any]:
        transaction = self.transactions.active
        return project_status(
            self.scene_generation,
            self._transaction_result(transaction) if transaction is not None else None,
            self.last_lifecycle_operation,
        )

    def project_summary(self) -> dict[str, Any]:
        return self._project_status()

    def _commit_active_transaction_for_lifecycle(self) -> dict[str, Any] | None:
        transaction = self.transactions.active
        if transaction is None:
            return None
        self._validate_transaction_guards(transaction)
        result = self._transaction_result(transaction)
        finalized: list[dict[str, Any]] = []
        with self.suppress_generation():
            for delta in transaction.deltas:
                item = finalize_modifier_delta(delta)
                if item is not None:
                    finalized.append(item)
            finalized.extend(finalize_weight_snapshots(transaction))
            finalized.extend(finalize_mesh_snapshots(transaction))
            for delta in transaction.structural_deltas():
                item = finalize_structural_delta(delta)
                if item is not None:
                    finalized.append(item)
        if finalized:
            self.scene_generation += 1
        result["status"] = "committed"
        result["finalized"] = finalized
        self.transactions.finish(transaction, "committed")
        return result

    def _adopt_active_transaction_for_native_save(
        self,
        operation: dict[str, Any],
    ) -> dict[str, Any] | None:
        transaction = self.transactions.active
        if transaction is None:
            return None
        adopted: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        scene_changed = False
        try:
            with self.suppress_generation():
                for delta in transaction.deltas:
                    item = adopt_modifier_delta_for_native_save(
                        delta,
                        transaction.transaction_id,
                    )
                    if item is not None:
                        adopted.append(item)
                        scene_changed = scene_changed or item.get("action") == (
                            "finalized_native_save"
                        )
                adopted.extend(adopt_mesh_snapshots_for_native_save(transaction))
                adopted.extend(adopt_weight_snapshots_for_native_save(transaction))
                for delta in transaction.structural_deltas():
                    item = adopt_structural_delta_for_native_save(delta)
                    if item is not None:
                        adopted.append(item)
                        scene_changed = scene_changed or item.get("action") not in {
                            "preserved_user_state",
                        }
        except Exception as exc:  # noqa: BLE001 - native saving must remain authoritative
            code = getattr(exc, "code", type(exc).__name__)
            warnings.append(
                {
                    "code": "NATIVE_SAVE_ADOPTION_PARTIAL",
                    "cause": str(code),
                    "message": str(exc),
                }
            )
            self.last_error = f"NATIVE_SAVE_ADOPTION_PARTIAL: {code}: {exc}"
        finally:
            clear_modifier_pending_deletes()
        if scene_changed:
            self.scene_generation += 1
        result = self._transaction_result(transaction)
        result.update(
            {
                "status": "accepted_user_save",
                "adopted": adopted,
                "warnings": warnings,
            }
        )
        terminal_details = {
            "save_operation": {
                "operation_id": operation["operation_id"],
                "kind": operation["kind"],
                "path": operation["path"],
                "user_intent_revision": operation["user_intent_revision"],
            }
        }
        transaction_id = transaction.transaction_id
        self.transactions.finish(
            transaction,
            "accepted_user_save",
            details=terminal_details,
        )
        self.idempotency.remove_transaction(transaction_id)
        self._disconnect_rollback_deadline = None
        return result

    @contextlib.contextmanager
    def _managed_save(self) -> Iterator[None]:
        self._managed_save_depth += 1
        try:
            yield
        finally:
            self._managed_save_depth -= 1

    def _run_managed_save(self, path: str | None) -> dict[str, Any]:
        with self._managed_save():
            return save_project(path)

    @staticmethod
    def _optional_save_path(params: dict[str, Any], name: str) -> str | None:
        value = params.get(name)
        if value is None:
            return None
        return str(validate_save_path(value))

    @staticmethod
    def _boolean_param(params: dict[str, Any], name: str, default: bool) -> bool:
        value = params.get(name, default)
        if type(value) is not bool:
            raise ProjectOperationError(
                "PARAMS_INVALID",
                f"{name} must be a boolean",
                kind="validation",
            )
        return value

    def _save_project(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._optional_save_path(params, "path")
        before = self._project_status()
        transaction = self._commit_active_transaction_for_lifecycle()
        saved = self._run_managed_save(path)
        self.scene_generation += 1
        operation = {
            "operation_id": str(uuid.uuid4()),
            "kind": "save",
            "status": "succeeded",
            "path": saved["path"],
        }
        self.last_lifecycle_operation = operation
        return {
            "status": "saved",
            "operation_id": operation["operation_id"],
            "before": before,
            "after": self._project_status(),
            "transaction": transaction,
            "save": saved,
        }

    def _prepare_current_for_transition(
        self,
        *,
        save_current: bool,
        save_current_as: str | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not save_current:
            return None, {"status": "skipped", "reason": "save_current_false"}
        transaction = self._commit_active_transaction_for_lifecycle()
        current = self._project_status()
        if not transition_needs_save(current["is_dirty"], transaction):
            return transaction, {"status": "skipped", "reason": "clean"}
        if not current["filepath"] and save_current_as is None:
            raise ProjectOperationError(
                "CURRENT_PROJECT_UNTITLED",
                "The dirty current project is untitled; provide save_current_as",
            )
        saved = self._run_managed_save(save_current_as)
        self.scene_generation += 1
        return transaction, saved

    def _schedule_lifecycle_operation(
        self,
        *,
        kind: str,
        path: str | None,
        use_scripts: bool | None = None,
        load_ui: bool | None = None,
    ) -> dict[str, Any]:
        if self.pending_lifecycle_operation is not None:
            raise ProjectOperationError(
                "LIFECYCLE_OPERATION_PENDING",
                "Another lifecycle operation is already pending",
                kind="conflict",
                retryable=True,
                details={"operation": self.pending_lifecycle_operation},
            )
        operation = {
            "operation_id": str(uuid.uuid4()),
            "kind": kind,
            "status": "accepted",
            "path": path,
            "use_scripts": use_scripts,
            "load_ui": load_ui,
        }
        self.pending_lifecycle_operation = operation
        self.last_lifecycle_operation = dict(operation)
        return operation

    def _open_project(self, params: dict[str, Any]) -> dict[str, Any]:
        target = validate_open_path(params.get("path"))
        save_current = self._boolean_param(params, "save_current", True)
        save_current_as = self._optional_save_path(params, "save_current_as")
        use_scripts = self._boolean_param(params, "use_scripts", True)
        load_ui = self._boolean_param(params, "load_ui", True)
        before = self._project_status()
        transaction, saved = self._prepare_current_for_transition(
            save_current=save_current,
            save_current_as=save_current_as,
        )
        current = str(bpy.data.filepath or "")
        if current and normalized_path(current) == normalized_path(str(target)):
            operation = {
                "operation_id": str(uuid.uuid4()),
                "kind": "open",
                "status": "already_open",
                "path": str(target),
            }
            self.last_lifecycle_operation = operation
            return {
                "status": "already_open",
                "operation_id": operation["operation_id"],
                "path": str(target),
                "before": before,
                "after": self._project_status(),
                "transaction": transaction,
                "save": saved,
            }
        operation = self._schedule_lifecycle_operation(
            kind="open",
            path=str(target),
            use_scripts=use_scripts,
            load_ui=load_ui,
        )
        return {
            "status": "accepted",
            "operation_id": operation["operation_id"],
            "path": str(target),
            "before": before,
            "transaction": transaction,
            "save": saved,
        }

    def _reload_project(self, params: dict[str, Any]) -> dict[str, Any]:
        current = str(bpy.data.filepath or "")
        if not current:
            raise ProjectOperationError(
                "PROJECT_RELOAD_UNAVAILABLE",
                "The current project is untitled and cannot be reloaded",
            )
        target = validate_open_path(current)
        save_current = self._boolean_param(params, "save_current", False)
        use_scripts = self._boolean_param(params, "use_scripts", True)
        load_ui = self._boolean_param(params, "load_ui", True)
        before = self._project_status()
        transaction, saved = self._prepare_current_for_transition(
            save_current=save_current,
            save_current_as=None,
        )
        operation = self._schedule_lifecycle_operation(
            kind="reload",
            path=str(target),
            use_scripts=use_scripts,
            load_ui=load_ui,
        )
        return {
            "status": "accepted",
            "operation_id": operation["operation_id"],
            "path": str(target),
            "before": before,
            "transaction": transaction,
            "save": saved,
        }

    def _quit_application(self, params: dict[str, Any]) -> dict[str, Any]:
        save_current = self._boolean_param(params, "save_current", True)
        save_current_as = self._optional_save_path(params, "save_current_as")
        before = self._project_status()
        transaction, saved = self._prepare_current_for_transition(
            save_current=save_current,
            save_current_as=save_current_as,
        )
        operation = self._schedule_lifecycle_operation(kind="quit", path=None)
        return {
            "status": "accepted",
            "operation_id": operation["operation_id"],
            "before": before,
            "transaction": transaction,
            "save": saved,
        }

    def _perform_pending_lifecycle_operation(self) -> None:
        operation = self.pending_lifecycle_operation
        if operation is None:
            return
        self.pending_lifecycle_operation = None
        running = dict(operation)
        running["status"] = "running"
        self.last_lifecycle_operation = running
        self.active_command = f"lifecycle:{operation['kind']}"
        try:
            kind = str(operation["kind"])
            if kind in {"open", "reload"}:
                open_project(
                    str(operation["path"]),
                    use_scripts=bool(operation["use_scripts"]),
                    load_ui=bool(operation["load_ui"]),
                )
            elif kind == "quit":
                quit_application()
            else:
                raise ProjectOperationError(
                    "BLENDER_COMMAND_FAILED",
                    f"Unknown pending lifecycle operation: {kind}",
                    kind="internal",
                )
            succeeded = dict(operation)
            succeeded["status"] = "succeeded"
            self.last_lifecycle_operation = succeeded
        except ProjectOperationError as exc:
            failed = dict(operation)
            failed.update(
                {
                    "status": "failed",
                    "error": {
                        "kind": exc.kind,
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": exc.retryable,
                        "details": exc.details,
                    },
                }
            )
            self.last_lifecycle_operation = failed
            self.last_error = f"{exc.code}: {exc}"
        finally:
            self.active_command = ""

    def _require_scene_generation(self, request: dict[str, Any]) -> None:
        expected = request.get("expected_scene_generation")
        if not isinstance(expected, int) or expected < 0:
            raise ContextOperationError(
                "SCENE_GENERATION_REQUIRED",
                "Mutation commands require expected_scene_generation",
                kind="validation",
            )
        if expected != self.scene_generation:
            raise ContextOperationError(
                "STALE_SCENE",
                f"Expected scene generation {expected}, current is {self.scene_generation}",
                kind="conflict",
            )

    def _require_transaction(
        self,
        params: dict[str, Any],
        request: dict[str, Any],
    ) -> Transaction:
        self._require_scene_generation(request)
        transaction_id = params.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise ContextOperationError(
                "TRANSACTION_ID_INVALID",
                "transaction_id must be a non-empty string",
                kind="validation",
            )
        terminal = self.transactions.terminal(transaction_id)
        if terminal is not None and terminal.get("status") == "accepted_user_save":
            raise ContextOperationError(
                "TRANSACTION_ACCEPTED_BY_USER_SAVE",
                "The user saved and accepted the transaction's current visible state",
                kind="conflict",
                details=terminal,
            )
        return self.transactions.require(transaction_id)

    def _current_context_fingerprint(self, transaction: Transaction) -> str:
        del transaction
        with self.suppress_generation():
            current = capture_transaction_context()
        return context_fingerprint(transaction_context_state(current))

    def _current_transaction_context(self) -> dict[str, Any]:
        with self.suppress_generation():
            return transaction_context_state(capture_transaction_context())

    def _preserved_user_ui_changes(self, transaction: Transaction) -> list[str]:
        try:
            with self.suppress_generation():
                current = capture_context(transaction.context_snapshot.get("viewport_id"))
        except ContextOperationError:
            try:
                with self.suppress_generation():
                    current = capture_context()
            except ContextOperationError:
                return ["viewport.unavailable"]
        return changed_context_paths(
            user_ui_context_state(transaction.context_snapshot),
            user_ui_context_state(current),
        )

    def _validate_transaction_guards(self, transaction: Transaction) -> None:
        current_context = self._current_transaction_context()
        expected_context = transaction_context_state(transaction.context_snapshot)
        if context_fingerprint(current_context) != transaction.context_fingerprint:
            transaction.status = "conflicted"
            self.transactions.last_status = "conflicted"
            raise ContextOperationError(
                "CONTEXT_CONFLICT",
                "A write-relevant Blender context changed while the transaction was active",
                kind="conflict",
                details={
                    "changed_fields": changed_context_paths(expected_context, current_context),
                    "expected": expected_context,
                    "actual": current_context,
                },
            )
        for reference, expected in transaction.expected_properties().items():
            current = read_property(reference)
            if not values_equal(current, expected):
                transaction.status = "conflicted"
                self.transactions.last_status = "conflicted"
                raise ContextOperationError(
                    "PROPERTY_CONFLICT",
                    f"{reference.kind}.{reference.attribute} changed outside the transaction",
                    kind="conflict",
                    details={
                        "kind": reference.kind,
                        "target": list(reference.target),
                        "attribute": reference.attribute,
                        "expected": expected,
                        "actual": current,
                    },
                )
        validate_modifier_stack_guards(transaction)
        validate_mesh_snapshot_guards(transaction)
        validate_weight_snapshot_guards(transaction)
        validate_structural_transaction(transaction)

    def _set_object_settings(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        previous_count = len(transaction.deltas)
        with self.suppress_generation():
            result = apply_object_settings(transaction, params)
        if len(transaction.deltas) > previous_count:
            self.scene_generation += 1
            transaction.status = "active"
            transaction.context_fingerprint = self._current_context_fingerprint(transaction)
        result.update(
            {
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        )
        return result

    def _transform_object(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        object_name = params.get("object_name")
        if not isinstance(object_name, str) or not object_name:
            raise ContextOperationError(
                "OBJECT_NAME_INVALID",
                "object_name must be a non-empty string",
                kind="validation",
            )
        public_patches = {
            name: params.get(name)
            for name in ("location", "rotation_euler_degrees", "scale")
            if params.get(name) is not None
        }
        if not public_patches:
            raise ContextOperationError(
                "TRANSFORM_PATCH_INVALID",
                "location, rotation_euler_degrees, and/or scale is required",
                kind="validation",
            )
        object_identity = params.get("expected_object_identity")
        if "location" in public_patches or "rotation_euler_degrees" in public_patches:
            object_identity = self._required_identity(params, "expected_object_identity")
        if object_identity is not None:
            if not isinstance(object_identity, str) or not object_identity:
                raise ContextOperationError(
                    "TARGET_IDENTITY_REQUIRED",
                    "expected_object_identity must be a non-empty session identity",
                    kind="validation",
                )
            obj = require_object(object_name, object_identity)
        else:
            obj = bpy.data.objects.get(object_name)
            if obj is None:
                raise ContextOperationError(
                    "OBJECT_NOT_FOUND",
                    f"Object does not exist: {object_name}",
                    kind="not_found",
                )
        result = self._set_object_settings(
            transaction,
            {
                "object_name": object_name,
                "expected_object_identity": session_identity("object", obj),
                "patches": [{"type": "transform", **public_patches}],
            },
        )
        changed: dict[str, list[str]] = {}
        before: dict[str, dict[str, float]] = {}
        after: dict[str, dict[str, float]] = {}
        for item in result["changes"]:
            _prefix, public_channel, axis = item["path"].split(".")
            channel = (
                "rotation_euler" if public_channel == "rotation_euler_degrees" else public_channel
            )
            before_value = float(item["before"])
            after_value = float(item["after"])
            if channel == "rotation_euler":
                before_value = math.radians(before_value)
                after_value = math.radians(after_value)
            before.setdefault(channel, {})[axis] = before_value
            after.setdefault(channel, {})[axis] = after_value
            changed.setdefault(channel, []).append(axis)
        return {
            "transaction_id": result["transaction_id"],
            "object_name": object_name,
            "object_identity": result["object_identity"],
            "changed": {channel: sorted(axes) for channel, axes in changed.items()},
            "before": before,
            "after": after,
            "location": result["object"]["location"],
            "rotation_euler_degrees": result["object"]["rotation_euler_degrees"],
            "scale": result["object"]["scale"],
            "status": result["status"],
            "delta_count": result["delta_count"],
            "delta_kinds": result["delta_kinds"],
        }

    @staticmethod
    def _required_identity(params: dict[str, Any], name: str) -> str:
        value = params.get(name)
        if not isinstance(value, str) or not value:
            raise ContextOperationError(
                "TARGET_IDENTITY_REQUIRED",
                f"{name} must be a non-empty session identity",
                kind="validation",
            )
        return value

    @staticmethod
    def _require_mutable_object(obj: Any) -> None:
        if obj.library is not None and obj.override_library is None:
            raise ContextOperationError(
                "OBJECT_LINKED",
                f"Linked object cannot be modified: {obj.name}",
            )

    def _record_delta(self, transaction: Transaction, delta: Any) -> None:
        transaction.record(delta)
        self.scene_generation += 1
        transaction.status = "active"
        transaction.context_fingerprint = self._current_context_fingerprint(transaction)

    def _set_object_visibility(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        object_name = params.get("object_name")
        if not isinstance(object_name, str) or not object_name:
            raise ContextOperationError(
                "OBJECT_NAME_INVALID",
                "object_name must be a non-empty string",
                kind="validation",
            )
        object_identity = self._required_identity(params, "expected_object_identity")
        patch = params.get("visibility")
        allowed = {"hide_viewport", "hide_render"}
        if not isinstance(patch, dict) or not patch or set(patch) - allowed:
            raise ContextOperationError(
                "WRITE_PATCH_EMPTY",
                "visibility must set hide_viewport and/or hide_render",
                kind="validation",
            )
        if any(type(value) is not bool for value in patch.values()):
            raise ContextOperationError(
                "VISIBILITY_VALUE_INVALID",
                "visibility values must be booleans",
                kind="validation",
            )
        obj = require_object(object_name, object_identity)
        before = {attribute: bool(getattr(obj, attribute)) for attribute in patch}
        after = {attribute: bool(value) for attribute, value in patch.items()}
        result = self._set_object_settings(
            transaction,
            {
                "object_name": object_name,
                "expected_object_identity": object_identity,
                "patches": [{"type": "visibility", **patch}],
            },
        )
        changed = sorted(item["path"].removeprefix("visibility.") for item in result["changes"])
        return {
            "transaction_id": result["transaction_id"],
            "object_name": object_name,
            "object_identity": object_identity,
            "changed_fields": changed,
            "before": before,
            "after": after,
            "visibility": result["object"]["visibility"],
            "status": result["status"],
            "delta_count": result["delta_count"],
            "delta_kinds": result["delta_kinds"],
        }

    def _run_modifier_write(
        self,
        transaction: Transaction,
        command: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        handlers = {
            "modifier.create": create_modifier,
            "modifier.set": set_modifier,
            "modifier.move": move_modifier,
            "modifier.delete": delete_modifier,
        }
        previous_count = len(transaction.deltas)
        with self.suppress_generation():
            result = handlers[command](transaction, params)
        if len(transaction.deltas) > previous_count:
            self.scene_generation += 1
            transaction.status = "active"
            transaction.context_fingerprint = self._current_context_fingerprint(transaction)
        result.update(
            {
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        )
        return result

    def _set_modifier_state(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        object_name = params.get("object_name")
        modifier_name = params.get("modifier_name")
        if not isinstance(object_name, str) or not object_name:
            raise ContextOperationError(
                "OBJECT_NAME_INVALID",
                "object_name must be a non-empty string",
                kind="validation",
            )
        if not isinstance(modifier_name, str) or not modifier_name:
            raise ContextOperationError(
                "MODIFIER_NAME_INVALID",
                "modifier_name must be a non-empty string",
                kind="validation",
            )
        object_identity = self._required_identity(params, "expected_object_identity")
        modifier_identity = self._required_identity(params, "expected_modifier_identity")
        patch = params.get("state")
        allowed = {"show_viewport", "show_render"}
        if not isinstance(patch, dict) or not patch or set(patch) - allowed:
            raise ContextOperationError(
                "WRITE_PATCH_EMPTY",
                "state must set show_viewport and/or show_render",
                kind="validation",
            )
        if any(type(value) is not bool for value in patch.values()):
            raise ContextOperationError(
                "MODIFIER_STATE_INVALID",
                "modifier state values must be booleans",
                kind="validation",
            )
        obj, modifier = require_modifier(
            object_name,
            object_identity,
            modifier_name,
            modifier_identity,
        )
        self._require_mutable_object(obj)
        previous_count = len(transaction.deltas)
        with self.suppress_generation():
            before, after, changed = set_modifier_state_compat(
                transaction,
                obj,
                modifier,
                {attribute: bool(value) for attribute, value in patch.items()},
            )
        if len(transaction.deltas) > previous_count:
            self.scene_generation += 1
            transaction.status = "active"
            transaction.context_fingerprint = self._current_context_fingerprint(transaction)
        return {
            "transaction_id": transaction.transaction_id,
            "object_name": object_name,
            "object_identity": object_identity,
            "modifier_name": modifier_name,
            "modifier_identity": modifier_identity,
            "changed_fields": changed,
            "before": before,
            "after": after,
            "state": {
                "show_viewport": bool(modifier.show_viewport),
                "show_render": bool(modifier.show_render),
            },
            "status": transaction.status,
            "delta_count": len(transaction.deltas),
            "delta_kinds": transaction.delta_kinds(),
        }

    def _set_shape_key_value(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        transaction.ensure_capacity()
        object_name = params.get("object_name")
        shape_key_name = params.get("shape_key_name")
        raw_value = params.get("value")
        if not isinstance(object_name, str) or not object_name:
            raise ContextOperationError(
                "OBJECT_NAME_INVALID",
                "object_name must be a non-empty string",
                kind="validation",
            )
        if not isinstance(shape_key_name, str) or not shape_key_name:
            raise ContextOperationError(
                "SHAPE_KEY_NAME_INVALID",
                "shape_key_name must be a non-empty string",
                kind="validation",
            )
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ContextOperationError(
                "SHAPE_KEY_VALUE_INVALID",
                "shape key value must be a finite number",
                kind="validation",
            )
        value = float(raw_value)
        if not math.isfinite(value):
            raise ContextOperationError(
                "SHAPE_KEY_VALUE_INVALID",
                "shape key value must be a finite number",
                kind="validation",
            )
        object_identity = self._required_identity(params, "expected_object_identity")
        shape_key_identity = self._required_identity(params, "expected_shape_key_identity")
        obj, key_block = require_shape_key(
            object_name,
            object_identity,
            shape_key_name,
            shape_key_identity,
        )
        self._require_mutable_object(obj)
        if obj.type != "MESH":
            raise ContextOperationError(
                "OBJECT_LOOKDEV_UNSUPPORTED",
                f"Shape key writes only support MESH objects: {object_name}",
            )
        shape_keys = obj.data.shape_keys
        if key_block == shape_keys.reference_key:
            raise ContextOperationError(
                "SHAPE_KEY_BASIS_FORBIDDEN",
                "The Basis shape key cannot be assigned a preview value",
            )
        if shape_keys.library is not None or obj.data.library is not None:
            raise ContextOperationError(
                "OBJECT_LINKED",
                f"Linked shape key data cannot be modified: {object_name}",
            )
        if shape_key_is_driven(shape_keys, key_block):
            raise ContextOperationError(
                "SHAPE_KEY_DRIVEN",
                f"Shape key is controlled by a driver: {object_name}.{shape_key_name}",
            )
        minimum = float(key_block.slider_min)
        maximum = float(key_block.slider_max)
        if not minimum <= value <= maximum:
            raise ContextOperationError(
                "SHAPE_KEY_VALUE_OUT_OF_RANGE",
                f"Shape key value must be between {minimum} and {maximum}",
                kind="validation",
                details={"minimum": minimum, "maximum": maximum, "value": value},
            )
        before = float(key_block.value)
        changed = not values_equal(before, value)
        if changed:
            with self.suppress_generation():
                key_block.value = value
                bpy.context.view_layer.update()
            self._record_delta(
                transaction,
                ShapeKeyDelta(
                    object_name=object_name,
                    object_identity=object_identity,
                    shape_key_name=shape_key_name,
                    shape_key_identity=shape_key_identity,
                    before=before,
                    after=value,
                ),
            )
        return {
            "transaction_id": transaction.transaction_id,
            "object_name": object_name,
            "object_identity": object_identity,
            "shape_key_name": shape_key_name,
            "shape_key_identity": shape_key_identity,
            "changed": changed,
            "before": before,
            "after": value,
            "value": float(key_block.value),
            "slider_min": minimum,
            "slider_max": maximum,
            "status": transaction.status,
            "delta_count": len(transaction.deltas),
            "delta_kinds": transaction.delta_kinds(),
        }

    def _set_material_input(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        transaction.ensure_capacity()
        object_name = params.get("object_name")
        material_slot_index = params.get("material_slot_index")
        material_name = params.get("material_name")
        node_name = params.get("node_name")
        socket_identifier = params.get("socket_identifier")
        for parameter_name, value in {
            "object_name": object_name,
            "material_name": material_name,
            "node_name": node_name,
            "socket_identifier": socket_identifier,
        }.items():
            if not isinstance(value, str) or not value:
                raise ContextOperationError(
                    "MATERIAL_TARGET_INVALID",
                    f"{parameter_name} must be a non-empty string",
                    kind="validation",
                )
        if (
            isinstance(material_slot_index, bool)
            or not isinstance(material_slot_index, int)
            or not 0 <= material_slot_index < 64
        ):
            raise ContextOperationError(
                "MATERIAL_SLOT_INDEX_INVALID",
                "material_slot_index must be an integer between 0 and 63",
                kind="validation",
            )
        expected_material_users = params.get("expected_material_users")
        if (
            isinstance(expected_material_users, bool)
            or not isinstance(expected_material_users, int)
            or expected_material_users < 1
        ):
            raise ContextOperationError(
                "MATERIAL_USERS_INVALID",
                "expected_material_users must be a positive integer",
                kind="validation",
            )
        allow_shared = params.get("allow_shared", False)
        if type(allow_shared) is not bool:
            raise ContextOperationError(
                "MATERIAL_SHARED_FLAG_INVALID",
                "allow_shared must be a boolean",
                kind="validation",
            )
        object_identity = self._required_identity(params, "expected_object_identity")
        material_identity = self._required_identity(params, "expected_material_identity")
        node_identity = self._required_identity(params, "expected_node_identity")
        socket_identity = self._required_identity(params, "expected_socket_identity")
        obj, material, node, socket = resolve_material_socket(
            object_name,
            object_identity,
            str(material_slot_index),
            material_name,
            material_identity,
            node_name,
            node_identity,
            socket_identifier,
            socket_identity,
        )
        node_tree = material.node_tree
        if material.library is not None or node_tree is None or node_tree.library is not None:
            raise ContextOperationError(
                "MATERIAL_LINKED",
                f"Linked or unavailable material node data cannot be modified: {material_name}",
                kind="precondition",
            )
        actual_users = int(material.users)
        if actual_users != expected_material_users:
            raise ContextOperationError(
                "MATERIAL_USERS_CONFLICT",
                "Material user count changed after it was inspected",
                kind="conflict",
                details={"expected": expected_material_users, "actual": actual_users},
            )
        affected_objects = material_affected_objects(material)
        if actual_users > 1 and not allow_shared:
            raise ContextOperationError(
                "SHARED_MATERIAL_CONFIRMATION_REQUIRED",
                "Shared material writes require allow_shared=true and an exact user count",
                kind="precondition",
                details={
                    "material_users": actual_users,
                    "affected_objects": affected_objects,
                },
            )
        socket_kind = material_socket_kind(socket)
        if socket_kind is None or not hasattr(socket, "default_value"):
            raise ContextOperationError(
                "MATERIAL_SOCKET_UNSUPPORTED",
                f"Unsupported material input socket: {node_name}.{socket_identifier}",
                kind="precondition",
            )
        if socket.is_linked:
            raise ContextOperationError(
                "MATERIAL_SOCKET_LINKED",
                f"Linked material input cannot be assigned: {node_name}.{socket_identifier}",
                kind="precondition",
            )
        if material_socket_is_driven(node_tree, socket):
            raise ContextOperationError(
                "MATERIAL_SOCKET_DRIVEN",
                f"Driven material input cannot be assigned: {node_name}.{socket_identifier}",
                kind="precondition",
            )
        if material_socket_readonly(socket):
            raise ContextOperationError(
                "MATERIAL_SOCKET_READONLY",
                f"Read-only material input cannot be assigned: {node_name}.{socket_identifier}",
                kind="precondition",
            )
        minimum, maximum = material_socket_range(socket, socket_kind)
        try:
            after = normalize_material_value(
                socket_kind,
                params.get("value"),
                minimum=minimum,
                maximum=maximum,
            )
        except LookdevModelError as exc:
            raise ContextOperationError(
                exc.code,
                str(exc),
                kind="validation",
                details=exc.details,
            ) from exc
        before = material_socket_value(socket, socket_kind)
        changed = not values_equal(before, after)
        if changed:
            with self.suppress_generation():
                socket.default_value = after
                node_tree.update_tag()
                bpy.context.view_layer.update()
                refresh_structure_guard_if_present(transaction, "material", material)
            self._record_delta(
                transaction,
                MaterialInputDelta(
                    object_name=object_name,
                    object_identity=object_identity,
                    material_slot_index=material_slot_index,
                    material_name=material_name,
                    material_identity=material_identity,
                    node_name=node_name,
                    node_identity=node_identity,
                    socket_identifier=socket_identifier,
                    socket_identity=socket_identity,
                    socket_kind=socket_kind,
                    before=before,
                    after=after,
                ),
            )
        return {
            "transaction_id": transaction.transaction_id,
            "object_name": obj.name,
            "object_identity": object_identity,
            "material_slot_index": material_slot_index,
            "material_name": material.name,
            "material_identity": material_identity,
            "material_users": actual_users,
            "shared_material": actual_users > 1,
            "affected_objects": affected_objects,
            "node_name": node.name,
            "node_identity": node_identity,
            "socket_identifier": socket.identifier,
            "socket_identity": socket_identity,
            "socket_kind": socket_kind,
            "changed": changed,
            "before": before,
            "after": after,
            "value": material_socket_value(socket, socket_kind),
            "minimum": minimum,
            "maximum": maximum,
            "status": transaction.status,
            "delta_count": len(transaction.deltas),
            "delta_kinds": transaction.delta_kinds(),
        }

    def _rollback_transaction(self, transaction: Transaction) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        preserved_ui_changes = self._preserved_user_ui_changes(transaction)
        restored: list[dict[str, Any]] = []
        modifier_delta_types = (
            ModifierStateDelta,
            ModifierSettingsDelta,
            ModifierCreateDelta,
            ModifierMoveDelta,
            ModifierDeleteDelta,
        )
        with self.suppress_generation():
            for delta in reversed(transaction.deltas):
                if isinstance(delta, modifier_delta_types):
                    restored.append(restore_modifier_delta(delta))
            bpy.context.view_layer.update()
            validate_restored_modifier_stacks(transaction)
            restored.extend(restore_mesh_snapshots(transaction))
            restored.extend(restore_weight_snapshots(transaction))
            for delta in reversed(transaction.deltas):
                if isinstance(delta, modifier_delta_types):
                    continue
                if isinstance(delta, MeshEditDelta):
                    continue
                if isinstance(delta, WeightEditDelta):
                    continue
                if isinstance(delta, StructuralDelta):
                    restored.append(restore_structural_delta(delta))
                else:
                    restored.append(restore_delta(delta))
            bpy.context.view_layer.update()
        if transaction.deltas:
            self.scene_generation += 1
        result = {
            "transaction_id": transaction.transaction_id,
            "status": "rolled_back",
            "restored": restored,
            "context_restored": True,
            "user_ui_preserved": True,
            "preserved_ui_changes": preserved_ui_changes,
        }
        self.transactions.finish(transaction, "rolled_back")
        return result

    def _transaction_result(self, transaction: Transaction) -> dict[str, Any]:
        return {
            "transaction_id": transaction.transaction_id,
            "label": transaction.label,
            "status": transaction.status,
            "started_generation": transaction.started_generation,
            "delta_count": len(transaction.deltas),
            "delta_kinds": transaction.delta_kinds(),
        }

    def _error(
        self,
        request_id: Any,
        kind: str,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": False,
            "scene_generation": self.scene_generation,
            "error": {
                "kind": kind,
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            },
        }
