from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ReleaseConfig:
    model_id: str
    version: str
    checkpoint: Path
    role: str = "shadow"
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    bind: str
    device: str
    dtype: str
    runtime_root: Path
    qmd_http_url: str
    qmd_ws_url: str
    backend_url: str
    maximum_batch_size: int
    maximum_tickers: int
    maximum_batch_delay_ms: int
    prediction_history: int
    minimum_warm_1s_bars: int
    queue_capacity: int
    warm_concurrency: int
    releases: tuple[ReleaseConfig, ...]
    release_catalog: tuple[ReleaseConfig, ...]
    connect_qmd: bool
    operational_config_path: Path

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        bind = os.environ.get("BAR_GPT_BIND", "127.0.0.1:8805")
        runtime_root = Path(os.environ.get("BAR_GPT_RUNTIME_ROOT", r"D:\TradingML\runtimes\bar_gpt_service"))
        repository = Path(__file__).resolve().parents[4]
        resolved_runtime = runtime_root.resolve()
        try:
            resolved_runtime.relative_to(repository.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("BAR_GPT_RUNTIME_ROOT must be outside the source repository")
        release_catalog = _release_configs(os.environ)
        operational_config_path = Path(
            os.environ.get(
                "BAR_GPT_OPERATIONAL_CONFIG",
                str(resolved_runtime / "configuration" / "operational.json"),
            )
        ).expanduser().resolve()
        try:
            operational_config_path.relative_to(repository.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("BAR_GPT_OPERATIONAL_CONFIG must be outside the source repository")
        desired = _read_operational_config(operational_config_path)
        releases = _selected_releases(release_catalog, desired)
        value = lambda key, env_name, default: desired.get(key, os.environ.get(env_name, default))
        return cls(
            bind=bind,
            device=str(value("device", "BAR_GPT_DEVICE", "auto")).strip().lower(),
            dtype=str(value("dtype", "BAR_GPT_DTYPE", "bfloat16")).strip().lower(),
            runtime_root=resolved_runtime,
            qmd_http_url=os.environ.get("BAR_GPT_QMD_HTTP_URL", "http://127.0.0.1:8795").rstrip("/"),
            qmd_ws_url=os.environ.get("BAR_GPT_QMD_WS_URL", "ws://127.0.0.1:8795").rstrip("/"),
            backend_url=os.environ.get("BAR_GPT_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/"),
            maximum_batch_size=max(1, int(value("maximum_batch_size", "BAR_GPT_MAX_BATCH_SIZE", "64"))),
            maximum_tickers=max(1, int(value("maximum_tickers", "BAR_GPT_MAX_TICKERS", "500"))),
            maximum_batch_delay_ms=max(0, int(value("maximum_batch_delay_ms", "BAR_GPT_MAX_BATCH_DELAY_MS", "20"))),
            prediction_history=max(1, int(value("prediction_history", "BAR_GPT_PREDICTION_HISTORY", "2048"))),
            minimum_warm_1s_bars=max(1, int(value("minimum_warm_1s_bars", "BAR_GPT_MINIMUM_WARM_1S_BARS", "64"))),
            queue_capacity=max(1, int(value("queue_capacity", "BAR_GPT_QUEUE_CAPACITY", "4096"))),
            warm_concurrency=max(1, int(value("warm_concurrency", "BAR_GPT_WARM_CONCURRENCY", "4"))),
            releases=releases,
            release_catalog=release_catalog,
            connect_qmd=bool(desired.get("connect_qmd", _bool_env("BAR_GPT_CONNECT_QMD", True))),
            operational_config_path=operational_config_path,
        )


def _release_configs(environment: Any) -> tuple[ReleaseConfig, ...]:
    raw = str(environment.get("BAR_GPT_RELEASES_JSON", "")).strip()
    rows: list[dict[str, Any]] = []
    if raw:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("BAR_GPT_RELEASES_JSON must contain a JSON array")
        rows.extend(item for item in parsed if isinstance(item, dict))
    else:
        for version in ("v2", "v3"):
            checkpoint = str(environment.get(f"BAR_GPT_{version.upper()}_CHECKPOINT", "")).strip()
            if checkpoint:
                rows.append({
                    "model_id": f"bar_gpt_{version}",
                    "version": version,
                    "checkpoint": checkpoint,
                    "role": "champion" if version == "v2" else "shadow",
                })
    result = []
    for row in rows:
        version = str(row.get("version") or "").strip().lower()
        if version not in {"v2", "v3"}:
            raise ValueError(f"unsupported BarGPT release version {version!r}")
        checkpoint = Path(str(row.get("checkpoint") or "")).expanduser().resolve()
        result.append(ReleaseConfig(
            model_id=str(row.get("model_id") or f"bar_gpt_{version}").strip(),
            version=version,
            checkpoint=checkpoint,
            role=str(row.get("role") or "shadow").strip().lower(),
            enabled=bool(row.get("enabled", True)),
        ))
    identifiers = [row.model_id for row in result]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("BarGPT release model_id values must be unique")
    if sum(row.role == "champion" and row.enabled for row in result) > 1:
        raise ValueError("at most one enabled BarGPT release may be champion")
    return tuple(result)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_operational_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        raise ValueError(f"unsupported BarGPT operational configuration: {path}")
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ValueError(f"BarGPT operational configuration has no settings object: {path}")
    return dict(settings)


def _selected_releases(
    catalog: tuple[ReleaseConfig, ...], desired: dict[str, Any]
) -> tuple[ReleaseConfig, ...]:
    selected = desired.get("selected_release_ids")
    selected_ids = (
        {str(value) for value in selected}
        if isinstance(selected, list)
        else {row.model_id for row in catalog if row.enabled}
    )
    unknown = selected_ids - {row.model_id for row in catalog}
    if unknown:
        raise ValueError("BarGPT operational configuration references unknown releases: " + ",".join(sorted(unknown)))
    roles = desired.get("release_roles") if isinstance(desired.get("release_roles"), dict) else {}
    releases = tuple(
        ReleaseConfig(
            model_id=row.model_id,
            version=row.version,
            checkpoint=row.checkpoint,
            role=str(roles.get(row.model_id) or row.role),
            enabled=row.model_id in selected_ids,
        )
        for row in catalog
        if row.model_id in selected_ids
    )
    if sum(row.role == "champion" for row in releases) > 1:
        raise ValueError("at most one selected BarGPT release may be champion")
    if any(row.role not in {"champion", "shadow"} for row in releases):
        raise ValueError("BarGPT release roles must be champion or shadow")
    return releases
