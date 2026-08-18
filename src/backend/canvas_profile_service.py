from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from src.backend.trading_runtime_service import trading_journal


CANVAS_DRAFT_PROFILE_RUN_ID = "canvas:draft-profile"
CANVAS_DRAFT_PROFILE_SCHEMA_VERSION = 1
MAX_CANVAS_PROFILE_BYTES = 5_000_000
_PROFILE_LOCK = threading.RLock()


class CanvasProfileConflictError(RuntimeError):
    pass


def editable_canvas_profile() -> dict[str, Any]:
    checkpoint = trading_journal().load_checkpoint(CANVAS_DRAFT_PROFILE_RUN_ID)
    state = dict(dict(checkpoint or {}).get("state") or {})
    profile = state.get("profile")
    return {
        "schema_version": CANVAS_DRAFT_PROFILE_SCHEMA_VERSION,
        "available": isinstance(profile, dict),
        "revision": int(state.get("revision") or 0),
        "content_hash": str(state.get("content_hash") or ""),
        "updated_at": str(state.get("updated_at") or ""),
        "profile": deepcopy(profile) if isinstance(profile, dict) else None,
    }


def save_editable_canvas_profile(
    profile: dict[str, Any], *, expected_revision: int | None = None
) -> dict[str, Any]:
    normalized = _validate_canvas_profile(profile)
    encoded = json.dumps(
        normalized, separators=(",", ":"), sort_keys=True, default=str
    ).encode("utf-8")
    if len(encoded) > MAX_CANVAS_PROFILE_BYTES:
        raise ValueError("Canvas profile exceeds the 5 MB persistence limit")
    content_hash = hashlib.sha256(encoded).hexdigest()
    with _PROFILE_LOCK:
        current = editable_canvas_profile()
        current_revision = int(current["revision"])
        if expected_revision is not None and expected_revision != current_revision:
            raise CanvasProfileConflictError(
                f"Canvas profile changed from revision {expected_revision} to {current_revision}"
            )
        if current.get("content_hash") == content_hash:
            return current
        now = datetime.now(UTC)
        state = {
            "schema_version": CANVAS_DRAFT_PROFILE_SCHEMA_VERSION,
            "revision": current_revision + 1,
            "content_hash": content_hash,
            "updated_at": now.isoformat(),
            "profile": normalized,
        }
        trading_journal().save_checkpoint(
            CANVAS_DRAFT_PROFILE_RUN_ID,
            content_hash,
            state,
            now,
        )
    return {
        "schema_version": CANVAS_DRAFT_PROFILE_SCHEMA_VERSION,
        "available": True,
        **deepcopy(state),
    }


def _validate_canvas_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise TypeError("Canvas profile must be an object")
    if int(profile.get("version") or 0) != 3:
        raise ValueError("Canvas profile version must be 3")
    canvases = profile.get("canvases")
    if not isinstance(canvases, list) or not canvases:
        raise ValueError("Canvas profile requires at least one Canvas")
    canvas_ids: set[str] = set()
    for row in canvases:
        if not isinstance(row, dict):
            raise ValueError("Canvas records must be objects")
        canvas_id = str(row.get("id") or "").strip()
        label = str(row.get("label") or "").strip()
        if not canvas_id or not label:
            raise ValueError("Every Canvas requires an id and label")
        if canvas_id in canvas_ids:
            raise ValueError(f"Duplicate Canvas id: {canvas_id}")
        canvas_ids.add(canvas_id)
    if "main" not in canvas_ids:
        raise ValueError("Canvas profile requires the main Canvas")
    workspace_states = profile.get("workspaceStates") or {}
    if not isinstance(workspace_states, dict):
        raise ValueError("Canvas workspaceStates must be an object")
    unknown_states = set(map(str, workspace_states)) - canvas_ids
    if unknown_states:
        raise ValueError(
            "Canvas profile contains workspace state for unknown Canvas ids: "
            + ", ".join(sorted(unknown_states))
        )
    for canvas_id, state in workspace_states.items():
        _validate_workspace_state(str(canvas_id), state)
    default_state = profile.get("defaultState")
    if default_state is not None:
        _validate_workspace_state("default", default_state)
    return deepcopy(profile)


def _validate_workspace_state(canvas_id: str, state: Any) -> None:
    if not isinstance(state, dict):
        raise ValueError(f"Canvas {canvas_id} workspace state must be an object")
    open_ids = state.get("openIds")
    layouts = state.get("layouts")
    instances = state.get("instances")
    if not isinstance(open_ids, list) or not isinstance(layouts, dict):
        raise ValueError(f"Canvas {canvas_id} requires openIds and layouts")
    if instances is not None and not isinstance(instances, dict):
        raise ValueError(f"Canvas {canvas_id} instances must be an object")
    if len(open_ids) != len(set(map(str, open_ids))):
        raise ValueError(f"Canvas {canvas_id} contains duplicate container instances")
    missing_layouts = {str(value) for value in open_ids} - set(map(str, layouts))
    if missing_layouts:
        raise ValueError(
            f"Canvas {canvas_id} is missing layouts for: "
            + ", ".join(sorted(missing_layouts))
        )
