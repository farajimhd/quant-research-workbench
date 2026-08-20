from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import OperationalConfigurationUpdate
from .models import release_summary


_LOCK = threading.RLock()


def configuration_snapshot(runtime: Any) -> dict[str, Any]:
    config = runtime.config
    stored = _read_stored(config.operational_config_path)
    effective = _effective_settings(config)
    desired = dict(stored.get("settings") or effective)
    loaded = {model_id: release_summary(release) for model_id, release in runtime.releases.items()}
    selected = set(desired.get("selected_release_ids") or [])
    roles = dict(desired.get("release_roles") or {})
    releases = []
    for release in config.release_catalog:
        evidence = loaded.get(release.model_id, {})
        releases.append({
            "release_id": release.model_id,
            "model_id": release.model_id,
            "version": release.version,
            "artifact_name": release.checkpoint.name,
            "selected": release.model_id in selected,
            "desired_role": str(roles.get(release.model_id) or release.role),
            "effective": release.model_id in loaded,
            "effective_role": evidence.get("role", ""),
            "checkpoint_hash": evidence.get("checkpoint_hash", ""),
            "contract_hash": evidence.get("contract_hash", ""),
            "parameter_count": evidence.get("parameter_count"),
            "context_bars": evidence.get("context_bars", {}),
            "horizons_us": evidence.get("horizons_us", []),
        })
    return {
        "schema_version": 1,
        "revision": int(stored.get("revision") or 0),
        "updated_at": str(stored.get("updated_at") or ""),
        "authority": "bar_gpt_service_operational_configuration_v1",
        "desired": desired,
        "effective": effective,
        "restart_required": desired != effective,
        "releases": releases,
        "runtime": {
            "status": runtime.health().get("status"),
            "scope_count": len(runtime.active_scopes()),
            "active_ticker_count": len(runtime.active_tickers()),
            "queue": runtime.health().get("queue", {}),
            "cache_count": len(runtime.caches),
        },
    }


def update_configuration(runtime: Any, request: OperationalConfigurationUpdate) -> dict[str, Any]:
    path: Path = runtime.config.operational_config_path
    with _LOCK:
        stored = _read_stored(path)
        revision = int(stored.get("revision") or 0)
        if request.expected_revision != revision:
            raise ValueError(
                f"BarGPT operational configuration revision changed: expected {request.expected_revision}, current {revision}"
            )
        catalog_ids = {row.model_id for row in runtime.config.release_catalog}
        selected = set(request.selected_release_ids)
        unknown = selected - catalog_ids
        if unknown:
            raise ValueError("unknown promoted BarGPT releases: " + ",".join(sorted(unknown)))
        unknown_roles = set(request.release_roles) - catalog_ids
        if unknown_roles:
            raise ValueError("roles reference unknown BarGPT releases: " + ",".join(sorted(unknown_roles)))
        selected_champions = [
            release_id for release_id in selected
            if request.release_roles.get(release_id) == "champion"
        ]
        if len(selected_champions) > 1:
            raise ValueError("at most one selected BarGPT release may be champion")
        settings = request.model_dump(exclude={"expected_revision"})
        settings["release_roles"] = {
            release_id: role for release_id, role in settings["release_roles"].items()
            if release_id in selected
        }
        payload = {
            "schema_version": 1,
            "revision": revision + 1,
            "updated_at": datetime.now(UTC).isoformat(),
            "settings": settings,
        }
        _atomic_write(path, payload)
    return configuration_snapshot(runtime)


def _effective_settings(config: Any) -> dict[str, Any]:
    return {
        "selected_release_ids": sorted(row.model_id for row in config.releases if row.enabled),
        "release_roles": {row.model_id: row.role for row in config.releases if row.enabled},
        "device": config.device,
        "dtype": config.dtype,
        "maximum_tickers": config.maximum_tickers,
        "maximum_batch_size": config.maximum_batch_size,
        "maximum_batch_delay_ms": config.maximum_batch_delay_ms,
        "queue_capacity": config.queue_capacity,
        "warm_concurrency": config.warm_concurrency,
        "minimum_warm_1s_bars": config.minimum_warm_1s_bars,
        "prediction_history": config.prediction_history,
        "connect_qmd": config.connect_qmd,
    }


def _read_stored(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported BarGPT operational configuration")
    return payload


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
