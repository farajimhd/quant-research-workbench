from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from dotenv import load_dotenv

from src.backend.application_registry import (
    DERIVED_FIELD_METHODS,
    DISCOVERY_FIELD_PRESENTATIONS,
    FIELD_DEFINITIONS,
    TEMPORAL_DERIVED_METHODS,
    _presentation_value_type,
)
from src.backend.qmd_gateway_client import qmd_catalogs
from src.backend.data_field_contracts import (
    atomic_field_catalog,
    build_column_catalog,
    build_data_field_catalog,
    compile_data_field_plan,
    data_field_output_index,
    interval_expression,
    migrate_rule_set_field_refs,
    normalize_interval_spec,
    validate_data_field_catalog,
)
from src.backend.trading_action_registry import (
    action_policy_rule_set_ids,
    default_action_policies,
    trading_action_definitions,
    validate_trading_actions,
)
from src.backend.trading_runtime_service import (
    get_strategy_definition,
    list_strategy_definitions,
    list_strategy_assignments,
    trading_journal,
)
from src.trading_runtime.portfolio import (
    PortfolioGroupPolicy,
    PortfolioPolicy,
    portfolio_policy_from_payload,
)
from src.trading_runtime.execution_policies import (
    DEFAULT_VERY_URGENT_PRICE_DISCRETION_TICKS,
    execution_policy_from_payload,
    protection_profile_from_payload,
)
from src.trading_runtime.strategy_engine import (
    STRATEGY_ID,
    STRATEGY_REVISION,
    default_entry_decision_rules,
    default_long_momentum_parameters,
)
from src.trading_runtime.strategy_registry import (
    installed_strategy_input_catalog,
    strategy_executor,
    strategy_executor_optional,
)
from src.trading_runtime.strategy_campaign import validate_campaign_policy
from src.trading_runtime.taxonomy import StrategyTaxonomy


CONFIGURATION_SCHEMA_VERSION = 43
MARKET_DISCOVERY_MATERIALIZATION_RUN_ID = "market-discovery:materialized-configuration"
_CONFIGURATION_BASE_CACHE_LOCK = threading.RLock()
_CONFIGURATION_BASE_CACHE: tuple[str, float, dict[str, Any] | None] = ("", 0.0, None)
_RUNTIME_SNAPSHOT_CACHE_LOCK = threading.RLock()
_RUNTIME_SNAPSHOT_CACHE: dict[tuple[str, str, str, str], dict[str, Any]] = {}
_MARKET_DISCOVERY_RUNTIME_CACHE_LOCK = threading.RLock()
_MARKET_DISCOVERY_RUNTIME_CACHE: tuple[float, dict[str, Any] | None] = (0.0, None)
CONFIGURATION_SECTIONS = {
    "strategy",
    "trading_actions",
    "market_discovery",
    "run_plans",
    "sessions",
    "portfolio",
    "oms",
    "accounts",
}
SUPPORTED_URGENCIES = {
    "passive_limit",
    "aggressive_limit",
    "market",
    "patient",
    "regular",
    "urgent",
    "very_urgent",
}
SUPPORTED_MODES = {"replay", "backtest", "backtest_debug", "paper", "live"}
ACTION_AUTHORITIES = {"disabled", "manual", "confirm", "automatic", "inherit"}
DISCOVERY_EXECUTION_SCOPES = {
    "universal_ingest",
    "core_scan",
    "watchlist",
    "signal_stream",
    "strategy_run",
    "request",
    "offline",
}
DISCOVERY_CONFIGURATION_POLICIES = {"locked", "configurable", "generated", "retired"}
_QMD_RUNTIME_CATALOG_CACHE: tuple[float, list[dict[str, Any]]] = (0.0, [])
QMD_CORE_SCANNER_FIELDS = {
    "core_bars": ["market.last_price", "market.volume", "indicator.vwap.execution_value"],
    "quote_mid_spread_bars": ["market.spread_bps"],
    "tape_rates": ["market.trade_rate_10s", "market.trade_rate_60s"],
    "nbbo_liquidity": ["market.spread_bps", "market.liquidity_score"],
    "reference_context": ["identity.company_name", "reference.market_cap"],
}
QMD_CORE_PRIMARY_SCANNER_FIELD = {
    "instrument-identity": "identity.symbol",
    "market-quality": "market.quality_state",
    "liquidity-rank": "market.liquidity_rank",
}


def _load_configuration_env() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for env_path in (Path.cwd() / ".env", repo_root / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)
    load_dotenv(override=False)


def _resolved_source_account_id(binding: dict[str, Any]) -> str:
    environment_key = str(binding.get("source_account_env") or "").strip()
    if environment_key:
        _load_configuration_env()
        return os.environ.get(environment_key, "").strip()
    return str(binding.get("source_account_id") or "").strip()


def _runtime_account_binding(binding: dict[str, Any]) -> dict[str, Any]:
    resolved = deepcopy(binding)
    if str(resolved.get("source_account_env") or "").strip():
        resolved["source_account_id"] = _resolved_source_account_id(resolved)
    return resolved


def public_configuration_revision(revision: dict[str, Any]) -> dict[str, Any]:
    """Remove runtime-only broker identities from a configuration response."""

    public = deepcopy(revision)

    def scrub(model: dict[str, Any]) -> None:
        for binding in dict(model.get("accounts") or {}).get("bindings") or []:
            if str(binding.get("source_account_env") or "").strip() or set(
                binding.get("modes") or []
            ).intersection({"paper", "live"}):
                binding["source_account_id"] = ""

    payload = public.get("payload")
    if isinstance(payload, dict):
        scrub(payload)
    configuration_model = public.get("configuration_model")
    if isinstance(configuration_model, dict):
        scrub(configuration_model)
    return public


def configuration_base() -> dict[str, Any]:
    global _CONFIGURATION_BASE_CACHE
    journal = trading_journal()
    approved = journal.approved_trading_configuration()
    cache_key = (
        f"approved:{approved.get('content_hash') or approved.get('revision_id') or ''}"
        if approved is not None
        else "default"
    )
    now = time.monotonic()
    cached_key, cached_until, cached = _CONFIGURATION_BASE_CACHE
    if cached is not None and cached_key == cache_key and now < cached_until:
        return deepcopy(cached)
    with _CONFIGURATION_BASE_CACHE_LOCK:
        cached_key, cached_until, cached = _CONFIGURATION_BASE_CACHE
        if cached is not None and cached_key == cache_key and now < cached_until:
            return deepcopy(cached)
        resolved = (
            _migrate_draft(deepcopy(dict(approved.get("payload") or {})))
            if approved is not None
            else _default_draft()
        )
        # Default authority may change with QMD catalog or assignment state, so
        # keep only a short shared snapshot. Published content is immutable.
        _CONFIGURATION_BASE_CACHE = (
            cache_key,
            now + (3600.0 if approved is not None else 5.0),
            deepcopy(resolved),
        )
        return resolved


def materialize_market_discovery(section: dict[str, Any]) -> dict[str, Any]:
    """Make a valid Market Discovery composition the live QMD execution authority.

    This deliberately materializes only Market Discovery. Strategy, Portfolio,
    OMS, account, Run Plan, and Canvas publication boundaries remain unchanged.
    """

    if not isinstance(section, dict):
        raise TypeError("Market Discovery materialization requires an object")
    discovery = deepcopy(section)
    core_scan_id = str(dict(discovery.get("core_scan") or {}).get("scan_id") or "qmd-core-scan")
    for stream in discovery.get("signal_streams") or []:
        stream.setdefault("source_type", "core_scan")
        stream.setdefault("source_id", str(stream.get("source_scan_id") or core_scan_id))
        if str(stream.get("source_type") or "core_scan") == "core_scan":
            stream["source_id"] = core_scan_id
            stream["source_scan_id"] = core_scan_id
    _normalize_market_discovery_interval_specs(discovery)
    discovery["data_field_plan"] = compile_data_field_plan(discovery)
    _validate_market_discovery(discovery, runtime_only=True)
    encoded = json.dumps(
        discovery, separators=(",", ":"), sort_keys=True, default=str
    ).encode("utf-8")
    content_hash = hashlib.sha256(encoded).hexdigest()
    current = trading_journal().load_checkpoint(
        MARKET_DISCOVERY_MATERIALIZATION_RUN_ID
    )
    current_state = dict(dict(current or {}).get("state") or {})
    if (
        str(current_state.get("content_hash") or "") == content_hash
        and isinstance(current_state.get("market_discovery"), dict)
    ):
        return current_state
    materialized_at = datetime.now().astimezone()
    state = {
        "schema_version": 1,
        "configuration_schema_version": CONFIGURATION_SCHEMA_VERSION,
        "content_hash": content_hash,
        "market_discovery": discovery,
        "materialized_at": materialized_at.isoformat(),
    }
    trading_journal().save_checkpoint(
        MARKET_DISCOVERY_MATERIALIZATION_RUN_ID,
        content_hash,
        state,
        materialized_at,
    )
    global _MARKET_DISCOVERY_RUNTIME_CACHE
    with _MARKET_DISCOVERY_RUNTIME_CACHE_LOCK:
        _MARKET_DISCOVERY_RUNTIME_CACHE = (0.0, None)
    from src.backend.canvas_preview_service import clear_scanner_snapshot_cache
    from src.backend.real_live_trading_service import clear_real_live_scanner_snapshot_cache

    clear_scanner_snapshot_cache()
    clear_real_live_scanner_snapshot_cache()
    return state


def market_discovery_runtime_configuration() -> dict[str, Any]:
    """Overlay the last valid materialized discovery section on the approved base."""

    global _MARKET_DISCOVERY_RUNTIME_CACHE
    now = time.monotonic()
    cached_until, cached = _MARKET_DISCOVERY_RUNTIME_CACHE
    if cached is not None and now < cached_until:
        return deepcopy(cached)
    with _MARKET_DISCOVERY_RUNTIME_CACHE_LOCK:
        cached_until, cached = _MARKET_DISCOVERY_RUNTIME_CACHE
        if cached is not None and now < cached_until:
            return deepcopy(cached)
        base = configuration_base()
        checkpoint = trading_journal().load_checkpoint(
            MARKET_DISCOVERY_MATERIALIZATION_RUN_ID
        )
        section = dict(dict(checkpoint or {}).get("state") or {}).get(
            "market_discovery"
        )
        if isinstance(section, dict):
            # Materialized user composition remains authoritative, while the
            # normal migration boundary supplies newly registered protected
            # system definitions. Without this merge, an older checkpoint can
            # permanently hide a later built-in stream from Canvas.
            migration_input = deepcopy(base)
            migration_input["market_discovery"] = deepcopy(section)
            migrated_section = _migrate_draft(migration_input)
            base["market_discovery"] = migrated_section["market_discovery"]
        # Materialization explicitly invalidates this cache above. Keeping the
        # immutable runtime configuration until that event prevents every
        # Canvas poll wave from re-reading the shared journal and contending
        # with live signal writes at a fixed 30-second cadence.
        _MARKET_DISCOVERY_RUNTIME_CACHE = (float("inf"), deepcopy(base))
        return base


def market_discovery_presentation_configuration() -> dict[str, Any]:
    """Return only the discovery metadata Canvas needs to render and configure lists.

    The full configuration contains executable Data Field recipes, Atomic Field
    lineage, and calculation plans that are intentionally not browser table
    metadata. Canvas consumes the materialized runtime composition and a narrow
    presentation projection instead of rebuilding or retransmitting that book.
    """

    configuration = market_discovery_runtime_configuration()
    discovery = dict(configuration.get("market_discovery") or {})
    core_scan = dict(discovery.get("core_scan") or {})
    calculations = []
    for row in core_scan.get("calculations") or []:
        calculations.append(
            {
                key: deepcopy(row.get(key))
                for key in (
                    "capability_id",
                    "enabled",
                    "execution_scope",
                    "scanner_columns",
                    "system_required",
                )
                if key in row
            }
        )
    columns = []
    for row in discovery.get("column_catalog") or []:
        columns.append(
            {
                key: deepcopy(row.get(key))
                for key in (
                    "column_id",
                    "description",
                    "name",
                    "presentation_value_type",
                    "provenance",
                    "semantic_type",
                    "source_id",
                    "source_kind",
                    "unit",
                    "value_type",
                )
                if key in row
            }
        )
    run_plans = dict(configuration.get("run_plans") or {})
    return {
        "schema_version": int(configuration.get("schema_version") or 0),
        "market_discovery": {
            "core_scan": {
                key: deepcopy(core_scan.get(key))
                for key in ("scan_id", "name", "description", "columns")
                if key in core_scan
            }
            | {"calculations": calculations},
            "column_catalog": columns,
            "watchlists": deepcopy(discovery.get("watchlists") or []),
            "signal_streams": deepcopy(discovery.get("signal_streams") or []),
        },
        "run_plans": {
            "plans": deepcopy(run_plans.get("plans") or []),
            "universes": deepcopy(run_plans.get("universes") or []),
        },
    }


def market_discovery_materialization_status() -> dict[str, Any]:
    checkpoint = trading_journal().load_checkpoint(
        MARKET_DISCOVERY_MATERIALIZATION_RUN_ID
    )
    state = dict(dict(checkpoint or {}).get("state") or {})
    return {
        "schema_version": 1,
        "materialized": bool(state.get("market_discovery")),
        "content_hash": str(state.get("content_hash") or ""),
        "materialized_at": str(state.get("materialized_at") or ""),
        "configuration_schema_version": int(
            state.get("configuration_schema_version") or 0
        ),
    }


def configuration_revisions() -> list[dict[str, Any]]:
    return trading_journal().trading_configuration_revisions()


def configuration_candidates() -> list[dict[str, Any]]:
    return trading_journal().trading_configuration_candidates()


def configuration_candidate(candidate_id: str = "", *, required: bool = False) -> dict[str, Any] | None:
    result = trading_journal().trading_configuration_candidate(candidate_id)
    if result is None and required:
        raise ValueError("No immutable Test Candidate exists. Create one from Configuration first.")
    return result


def approved_configuration(*, required: bool = False) -> dict[str, Any] | None:
    result = trading_journal().approved_trading_configuration()
    if result is None and required:
        raise ValueError("No approved trading configuration exists. Publish one from Configuration > Approved Releases.")
    return result


def approved_canvas_profile() -> dict[str, Any]:
    """Project the published Canvas default without exposing the full release."""

    approved = approved_configuration()
    if approved is None:
        return {
            "schema_version": 1,
            "available": False,
            "revision_id": "",
            "configuration_revision": 0,
            "content_hash": "",
            "canvas_revision": "",
            "profile": {},
        }
    canvas = dict(dict(approved.get("payload") or {}).get("canvas") or {})
    profile = deepcopy(dict(canvas.get("profile") or {}))
    result = {
        "schema_version": 1,
        "available": bool(profile),
        "revision_id": str(approved.get("revision_id") or ""),
        "configuration_revision": int(approved.get("revision") or 0),
        "content_hash": str(approved.get("content_hash") or ""),
        "canvas_revision": str(canvas.get("revision") or ""),
        "profile": profile,
    }
    return result


def publish_configuration(
    *,
    label: str,
    canvas_revision: str,
    canvas_profile: dict[str, Any],
    configuration: dict[str, Any],
    run_plan_id: str = "",
    strategy_profile_id: str = "",
) -> dict[str, Any]:
    runtime_candidate, payload, content_hash = _build_configuration_release(
        canvas_revision=canvas_revision,
        canvas_profile=canvas_profile,
        configuration=configuration,
        run_plan_id=run_plan_id,
        strategy_profile_id=strategy_profile_id,
    )
    normalized_label = label.strip()
    if not normalized_label:
        raise ValueError("An approval label is required")
    existing = configuration_revisions()
    if existing and existing[0]["content_hash"] == content_hash:
        materialize_market_discovery(runtime_candidate["market_discovery"])
        return existing[0]
    revision = int(existing[0]["revision"]) + 1 if existing else 1
    published = trading_journal().publish_trading_configuration(
        revision_id=str(uuid4()),
        revision=revision,
        label=normalized_label,
        content_hash=content_hash,
        payload=payload,
    )
    materialize_market_discovery(runtime_candidate["market_discovery"])
    return published


def create_test_candidate(
    *,
    label: str,
    canvas_revision: str,
    canvas_profile: dict[str, Any],
    configuration: dict[str, Any],
    run_plan_id: str = "",
    strategy_profile_id: str = "",
) -> dict[str, Any]:
    normalized_label = label.strip()
    if not normalized_label:
        raise ValueError("A Test Candidate label is required")
    _, payload, content_hash = _build_configuration_release(
        canvas_revision=canvas_revision,
        canvas_profile=canvas_profile,
        configuration=configuration,
        run_plan_id=run_plan_id,
        strategy_profile_id=strategy_profile_id,
    )
    existing = configuration_candidates()
    same = next((row for row in existing if row["content_hash"] == content_hash), None)
    if same is not None:
        return same
    candidate_revision = int(existing[0]["candidate_revision"]) + 1 if existing else 1
    return trading_journal().save_trading_configuration_candidate(
        candidate_id=str(uuid4()),
        candidate_revision=candidate_revision,
        label=normalized_label,
        content_hash=content_hash,
        payload=payload,
    )


def _build_configuration_release(
    *,
    canvas_revision: str,
    canvas_profile: dict[str, Any],
    configuration: dict[str, Any],
    run_plan_id: str = "",
    strategy_profile_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if not isinstance(configuration, dict):
        raise TypeError("A release requires the complete session configuration")
    # Compare immutable published profiles only after both sides have passed
    # through the same canonical migration. Persisted releases may predate
    # additive lifecycle fields such as empty groups/operator declarations;
    # comparing raw history with a migrated candidate falsely reports those
    # semantic no-ops as an in-place strategy mutation.
    base_configuration = _migrate_draft(deepcopy(configuration_base()))
    draft_candidate = _without_timestamp(_migrate_draft(deepcopy(configuration)))
    _assert_published_profiles_unchanged(base_configuration, draft_candidate)
    _refresh_builtin_system_strategy_profiles(draft_candidate)
    profiles = list(dict(draft_candidate["strategy"]).get("profiles") or [])
    run_plans = list(dict(draft_candidate["run_plans"]).get("plans") or [])
    selected_run_plan = next(
        (
            row
            for row in run_plans
            if str(row.get("run_plan_id") or "") == run_plan_id.strip()
        ),
        None,
    )
    if run_plan_id.strip() and selected_run_plan is None:
        raise ValueError(f"Publishing references an unknown Run Plan: {run_plan_id}")
    if selected_run_plan is None and strategy_profile_id.strip():
        selected_run_plan = next(
            (
                row
                for row in run_plans
                if str(row.get("profile_id") or "") == strategy_profile_id.strip()
            ),
            None,
        )
    if selected_run_plan is None:
        selected_run_plan = next(
            (row for row in run_plans if bool(row.get("enabled", True))),
            run_plans[0] if run_plans else None,
        )
    if selected_run_plan is None:
        raise ValueError("Publishing requires a selected Run Plan")
    active_profile_id = str(dict(draft_candidate["strategy"]).get("active_profile_id") or "")
    first_user_draft_id = next(
        (
            str(row.get("profile_id") or "")
            for row in profiles
            if str(row.get("publication_status") or "draft") == "draft"
            and str(row.get("origin") or "user") == "user"
        ),
        "",
    )
    selected_profile_id = (
        str(selected_run_plan.get("profile_id") or "")
        or strategy_profile_id.strip()
        or active_profile_id
        or first_user_draft_id
        or str(dict(draft_candidate["strategy"]).get("default_profile_id") or "")
    )
    selected_profile = next(
        (row for row in profiles if str(row.get("profile_id")) == selected_profile_id),
        None,
    )
    if selected_profile is None:
        raise ValueError("Publishing requires a selected Strategy")
    referenced_profile_ids = {
        str(row.get("profile_id") or "")
        for row in run_plans
        if bool(row.get("enabled", True))
    }
    for profile in profiles:
        if str(profile.get("profile_id") or "") in referenced_profile_ids:
            profile["publication_status"] = "published"
            profile["editable"] = False
    draft_candidate["strategy"]["active_profile_id"] = selected_profile_id
    _validate_draft(draft_candidate, require_runtime_ready=False)

    # Publishing freezes every Run Plan and its referenced graph without replacing
    # the reusable Portfolio, OMS, account, or discovery catalogs.
    runtime_candidate = deepcopy(draft_candidate)
    _compile_run_plans(runtime_candidate)
    _validate_draft(runtime_candidate)
    payload = {
        **runtime_candidate,
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "canvas": {
            "revision": canvas_revision.strip(),
            "profile": deepcopy(canvas_profile),
            "execution_authority": False,
        },
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    content_hash = hashlib.sha256(encoded).hexdigest()
    return runtime_candidate, payload, content_hash


def _refresh_builtin_system_strategy_profiles(model: dict[str, Any]) -> None:
    """Advance built-in profiles only at the new-release boundary.

    Migration and runtime resolution must preserve immutable historical
    profiles. After the published-profile integrity check succeeds, however,
    a newly created Test Candidate must pin the currently installed built-in
    executor rather than copying an obsolete system revision forever.
    """

    defaults = _default_draft()
    installed = {
        str(profile.get("profile_id") or ""): deepcopy(profile)
        for profile in defaults["strategy"].get("profiles") or []
        if str(profile.get("origin") or "") == "system"
    }
    profiles = list(dict(model.get("strategy") or {}).get("profiles") or [])
    model["strategy"]["profiles"] = [
        deepcopy(installed[profile_id])
        if (
            (profile_id := str(profile.get("profile_id") or "")) in installed
            and str(profile.get("origin") or "") == "system"
        )
        else profile
        for profile in profiles
    ]


def replay_configuration_snapshot(
    run_plan_id: str = "", *, candidate_id: str = ""
) -> dict[str, Any]:
    candidate = configuration_candidate(candidate_id)
    if candidate is not None:
        return candidate_runtime_configuration_snapshot(
            "replay",
            candidate_id=str(candidate["candidate_id"]),
            run_plan_id=run_plan_id,
        )
    approved = approved_configuration()
    if approved is None:
        return candidate_runtime_configuration_snapshot(
            "replay", candidate_id=candidate_id, run_plan_id=run_plan_id
        )
    if candidate_id and candidate_id != str(approved.get("revision_id") or ""):
        raise ValueError("The selected Replay configuration no longer exists")
    return approved_runtime_configuration_snapshot("replay", run_plan_id=run_plan_id)


def candidate_session_configuration_snapshot(
    mode: str,
    *,
    candidate_id: str = "",
    session_profile_id: str = "",
    execution_route_id: str = "",
) -> dict[str, Any]:
    """Pin a strategy-free simulated session to an immutable Test Candidate."""

    if mode not in {"replay", "backtest", "backtest_debug"}:
        raise ValueError(f"Test Candidates cannot authorize {mode}")
    candidate = configuration_candidate(candidate_id, required=True)
    assert candidate is not None
    model = _migrate_draft(deepcopy(candidate["payload"]))
    _validate_draft(model)
    resolved = resolve_session_configuration(
        model,
        mode=mode,
        session_profile_id=session_profile_id,
        execution_route_id=execution_route_id,
    )
    return {
        "revision_id": candidate["candidate_id"],
        "revision": candidate["candidate_revision"],
        "label": candidate["label"],
        "content_hash": candidate["content_hash"],
        "approved_at": "",
        "created_at": candidate["created_at"],
        "release_state": "test_candidate",
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "mode": mode,
        "execution_mode": "manual",
        "session_profile_id": str(resolved["session_profile"]["session_profile_id"]),
        "execution_route_id": str(resolved["execution_routes"][0]["execution_route_id"]),
        "configuration_model": model,
        "payload": resolved,
    }


def approved_session_configuration_snapshot(
    mode: str,
    *,
    session_profile_id: str = "",
    execution_route_id: str = "",
) -> dict[str, Any]:
    """Pin a strategy-free Session Profile for manual or semi-automatic operation."""

    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported trading configuration mode: {mode}")
    approved = approved_configuration(required=True)
    assert approved is not None
    model = _migrate_draft(deepcopy(approved["payload"]))
    _validate_draft(model)
    resolved = resolve_session_configuration(
        model,
        mode=mode,
        session_profile_id=session_profile_id,
        execution_route_id=execution_route_id,
    )
    return {
        "revision_id": approved["revision_id"],
        "revision": approved["revision"],
        "label": approved["label"],
        "content_hash": approved["content_hash"],
        "approved_at": approved["approved_at"],
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "mode": mode,
        "execution_mode": "manual",
        "session_profile_id": str(resolved["session_profile"]["session_profile_id"]),
        "execution_route_id": str(resolved["execution_routes"][0]["execution_route_id"]),
        "configuration_model": model,
        "payload": resolved,
    }


def backtest_configuration_snapshot(
    run_plan_id: str = "", *, candidate_id: str = ""
) -> dict[str, Any]:
    if candidate_id or approved_configuration() is None:
        return candidate_runtime_configuration_snapshot(
            "backtest", candidate_id=candidate_id, run_plan_id=run_plan_id
        )
    return approved_runtime_configuration_snapshot("backtest", run_plan_id=run_plan_id)


def backtest_debug_configuration_snapshot(
    run_plan_id: str = "", *, candidate_id: str = ""
) -> dict[str, Any]:
    if candidate_id or approved_configuration() is None:
        return candidate_runtime_configuration_snapshot(
            "backtest_debug", candidate_id=candidate_id, run_plan_id=run_plan_id
        )
    return approved_runtime_configuration_snapshot("backtest_debug", run_plan_id=run_plan_id)


def candidate_runtime_configuration_snapshot(
    mode: str, *, candidate_id: str = "", run_plan_id: str = ""
) -> dict[str, Any]:
    candidate = configuration_candidate(candidate_id, required=True)
    assert candidate is not None
    cache_key = (
        mode,
        str(candidate["candidate_id"]),
        run_plan_id,
        str(candidate["content_hash"]),
    )
    with _RUNTIME_SNAPSHOT_CACHE_LOCK:
        cached = _RUNTIME_SNAPSHOT_CACHE.get(cache_key)
        if cached is not None:
            return deepcopy(cached)
        snapshot = _runtime_configuration_snapshot(
            candidate,
            mode=mode,
            run_plan_id=run_plan_id,
            revision_id=str(candidate["candidate_id"]),
            revision=int(candidate["candidate_revision"]),
            timestamp=str(candidate["created_at"]),
            release_state="test_candidate",
        )
        _RUNTIME_SNAPSHOT_CACHE[cache_key] = snapshot
        selected_run_plan_id = str(snapshot.get("run_plan_id") or "")
        if selected_run_plan_id:
            _RUNTIME_SNAPSHOT_CACHE[
                (
                    mode,
                    str(candidate["candidate_id"]),
                    selected_run_plan_id,
                    str(candidate["content_hash"]),
                )
            ] = snapshot
        return deepcopy(snapshot)


def approved_runtime_configuration_snapshot(
    mode: str, *, run_plan_id: str = ""
) -> dict[str, Any]:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported trading configuration mode: {mode}")
    approved = approved_configuration(required=True)
    assert approved is not None
    return _runtime_configuration_snapshot(
        approved,
        mode=mode,
        run_plan_id=run_plan_id,
        revision_id=str(approved["revision_id"]),
        revision=int(approved["revision"]),
        timestamp=str(approved["approved_at"]),
        release_state="approved",
    )


def _runtime_configuration_snapshot(
    release: dict[str, Any],
    *,
    mode: str,
    run_plan_id: str,
    revision_id: str,
    revision: int,
    timestamp: str,
    release_state: str,
) -> dict[str, Any]:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported trading configuration mode: {mode}")
    payload = dict(release["payload"])
    model = (
        deepcopy(payload)
        if release_state == "test_candidate"
        and int(payload.get("schema_version") or 0) == CONFIGURATION_SCHEMA_VERSION
        else _migrate_draft(deepcopy(payload))
    )
    _validate_draft(model)
    runtimes = resolve_runtime_configurations(model, mode=mode)
    if not runtimes:
        raise ValueError(f"No enabled Strategy Run Plan supports {mode}")
    selected = next(
        (
            runtime
            for runtime in runtimes
            if str(runtime["run_plan"].get("run_plan_id") or "") == run_plan_id
        ),
        runtimes[0] if not run_plan_id else None,
    )
    if selected is None:
        raise ValueError(f"No enabled Strategy Run Plan named {run_plan_id} supports {mode}")
    runtime_payload = deepcopy(selected)
    if dict(model.get("canvas") or {}).get("profile"):
        # Returned for optional presentation attachment only.
        runtime_payload["canvas"] = deepcopy(model["canvas"])
    return {
        "revision_id": revision_id,
        "revision": revision,
        "label": release["label"],
        "content_hash": release["content_hash"],
        "approved_at": timestamp if release_state == "approved" else "",
        "created_at": timestamp,
        "release_state": release_state,
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "mode": mode,
        "run_plan_id": runtime_payload["run_plan"]["run_plan_id"],
        "available_run_plans": [
            {
                "run_plan_id": str(runtime["run_plan"]["run_plan_id"]),
                "name": str(runtime["run_plan"].get("name") or ""),
                "strategy_id": str(runtime["strategy"]["strategy_id"]),
                "strategy_revision": int(runtime["strategy"]["revision"]),
                "profile_id": str(runtime["strategy"].get("profile_id") or ""),
            }
            for runtime in runtimes
        ],
        "configuration_model": model,
        "payload": runtime_payload,
    }


def effective_configuration_snapshot(
    *,
    mode: str,
    configuration: dict[str, Any] | None = None,
    use_approved: bool = False,
) -> dict[str, Any]:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported trading configuration mode: {mode}")
    revision = approved_configuration(required=True) if use_approved else None
    model = (
        deepcopy(dict(revision.get("payload") or {}))
        if revision
        else deepcopy(configuration) if configuration is not None
        else configuration_base()
    )
    model = _migrate_draft(model)
    _validate_draft(model, require_runtime_ready=False)
    runtimes = resolve_runtime_configurations(
        model,
        mode=mode,
        resolve_broker_ids=False,
    )
    policies = {
        str(row.get("policy_id") or ""): portfolio_policy_from_payload(dict(row))
        for row in dict(model["portfolio"]).get("policies") or []
    }
    accounts = []
    for binding in dict(model["accounts"]).get("bindings") or []:
        if mode not in set(binding.get("modes") or []):
            continue
        policy = policies.get(str(binding.get("portfolio_policy_id") or ""))
        accounts.append({
            **deepcopy(binding),
            "policy_identity": policy.identity if policy else "",
            "run_plan_ids": [
                str(runtime["run_plan"]["run_plan_id"])
                for runtime in runtimes
                if any(
                    str(row.get("account_key")) == str(binding.get("account_key"))
                    for row in runtime["accounts"]["bindings"]
                )
            ],
        })
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "mode": mode,
        "source": "approved_release" if revision else "draft",
        "revision_id": str(revision.get("revision_id") or "") if revision else "",
        "runtime_count": len(runtimes),
        "accounts": accounts,
        "runtimes": runtimes,
    }


def resolve_runtime_configurations(
    model: dict[str, Any],
    *,
    mode: str,
    resolve_broker_ids: bool = True,
) -> list[dict[str, Any]]:
    migrated = _migrate_draft(model)
    enabled_route_ids = {
        str(row.get("execution_route_id") or "")
        for row in dict(migrated["sessions"]).get("execution_routes") or []
        if bool(row.get("enabled", True))
    }
    eligible = [
        row
        for row in dict(migrated["sessions"]).get("strategy_deployments") or []
        if bool(row.get("enabled", True))
        and mode in set(row.get("modes") or [])
        and bool({str(value) for value in row.get("execution_route_ids") or []} & enabled_route_ids)
    ]
    eligible.sort(
        key=lambda row: (
            str(row.get("strategy_deployment_id") or ""),
        )
    )
    return [
        resolve_runtime_configuration(
            migrated,
            mode=mode,
            deployment_id=str(row["strategy_deployment_id"]),
            resolve_broker_ids=resolve_broker_ids,
        )
        for row in eligible
    ]


def resolve_session_configuration(
    model: dict[str, Any],
    *,
    mode: str,
    session_profile_id: str = "",
    execution_route_id: str = "",
    resolve_broker_ids: bool = True,
) -> dict[str, Any]:
    """Resolve manual/semi-automatic execution without requiring a Strategy."""

    model = _migrate_draft(model)
    sessions = dict(model["sessions"])
    profiles = [
        row for row in sessions.get("profiles") or []
        if bool(row.get("enabled", True)) and mode in set(row.get("modes") or [])
    ]
    profile = next(
        (row for row in profiles if str(row.get("session_profile_id") or "") == session_profile_id),
        profiles[0] if profiles and not session_profile_id else None,
    )
    if profile is None:
        raise ValueError(f"No enabled Session Profile supports {mode}")
    if not bool(dict(profile.get("manual_authority") or {}).get("enabled", False)):
        raise ValueError(f"Session Profile {profile.get('name')} does not permit manual execution")
    route_ids = {str(value) for value in profile.get("execution_route_ids") or []}
    routes = [
        row for row in sessions.get("execution_routes") or []
        if str(row.get("execution_route_id") or "") in route_ids
        and str(row.get("session_profile_id") or "") == str(profile.get("session_profile_id") or "")
        and mode in set(row.get("modes") or [])
        and bool(row.get("enabled", True))
        and bool(row.get("manual_enabled", False))
    ]
    profile_default_route_id = str(profile.get("default_execution_route_id") or "")
    requested_route_id = execution_route_id or (
        profile_default_route_id
        if any(str(row.get("execution_route_id") or "") == profile_default_route_id for row in routes)
        else str(routes[0].get("execution_route_id") or "") if routes else ""
    )
    selected_routes = [
        row for row in routes
        if not requested_route_id or str(row.get("execution_route_id") or "") == requested_route_id
    ]
    if not selected_routes:
        raise ValueError(f"Session Profile {profile.get('name')} requires an enabled Execution Route")
    selected_account_keys = {str(row.get("account_key") or "") for row in selected_routes}
    bindings = [
        _runtime_account_binding(dict(row)) if resolve_broker_ids else deepcopy(dict(row))
        for row in dict(model["accounts"]).get("bindings") or []
        if str(row.get("account_key") or "") in selected_account_keys
        and bool(row.get("enabled", True))
        and mode in set(row.get("modes") or [])
    ]
    if {str(row.get("account_key") or "") for row in bindings} != selected_account_keys:
        raise ValueError(f"Session Profile {profile.get('name')} selected an account unavailable for {mode}")
    selected_mandate_ids = {str(row.get("portfolio_mandate_id") or "") for row in selected_routes}
    mandates = [
        deepcopy(row) for row in dict(model["portfolio"]).get("mandates") or []
        if str(row.get("mandate_id") or "") in selected_mandate_ids
        and bool(row.get("enabled", True))
    ]
    oms_ids = {str(row.get("oms_profile_id") or "") for row in selected_routes}
    if len(oms_ids) != 1:
        raise ValueError("One manual Session cannot mix OMS profiles across execution routes")
    oms_profile = next(
        (
            row for row in dict(model["oms"]).get("profiles") or []
            if str(row.get("profile_id") or "") == next(iter(oms_ids))
        ),
        None,
    )
    if oms_profile is None:
        raise ValueError("Execution Route references an unknown OMS profile")
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "execution_principal": {
            "kind": "session",
            "id": str(profile["session_profile_id"]),
        },
        "session_profile": deepcopy(profile),
        "execution_routes": deepcopy(selected_routes),
        "portfolio": {
            "policies": deepcopy(dict(model["portfolio"]).get("policies") or []),
            "groups": deepcopy(dict(model["portfolio"]).get("groups") or []),
            "mandates": mandates,
        },
        "oms": {
            **deepcopy(dict(oms_profile.get("settings") or {})),
            "profile_id": str(oms_profile.get("profile_id") or ""),
            "profile_revision": int(oms_profile.get("revision") or 1),
            "execution_policies": deepcopy(dict(model["oms"]).get("execution_policies") or []),
            "protection_profiles": deepcopy(dict(model["oms"]).get("protection_profiles") or []),
        },
        "accounts": {"bindings": bindings},
    }


def resolve_runtime_configuration(
    model: dict[str, Any],
    *,
    mode: str,
    run_plan_id: str = "",
    deployment_id: str = "",
    resolve_broker_ids: bool = True,
) -> dict[str, Any]:
    """Resolve a Strategy Deployment through its Session Profile and Execution Routes."""

    model = _migrate_draft(model)
    sessions = dict(model["sessions"])
    enabled_route_ids = {
        str(row.get("execution_route_id") or "")
        for row in sessions.get("execution_routes") or []
        if bool(row.get("enabled", True))
    }
    deployments = [
        row for row in sessions.get("strategy_deployments") or []
        if bool(row.get("enabled", True))
        and mode in set(row.get("modes") or [])
        and bool({str(value) for value in row.get("execution_route_ids") or []} & enabled_route_ids)
    ]
    requested_id = deployment_id or run_plan_id
    deployment = next(
        (
            row for row in deployments
            if str(row.get("strategy_deployment_id") or "") == requested_id
            or str(row.get("run_plan_id") or "") == requested_id
        ),
        deployments[0] if deployments and not requested_id else None,
    )
    if deployment is None:
        raise ValueError(f"No enabled Strategy Deployment supports {mode}")
    run_plans = list(dict(model["run_plans"]).get("plans") or [])
    run_plan = next(
        (row for row in run_plans if str(row.get("run_plan_id") or "") == str(deployment.get("run_plan_id") or "")),
        None,
    )
    if run_plan is None:
        raise ValueError(f"Strategy Deployment {deployment.get('strategy_deployment_id')} references an unknown Run Plan")
    session_profile = next(
        (
            row for row in sessions.get("profiles") or []
            if str(row.get("session_profile_id") or "") == str(deployment.get("session_profile_id") or "")
        ),
        None,
    )
    if session_profile is None:
        raise ValueError(f"Strategy Deployment {deployment.get('strategy_deployment_id')} references an unknown Session Profile")
    if not bool(session_profile.get("enabled", True)) or mode not in set(session_profile.get("modes") or []):
        raise ValueError(f"Strategy Deployment {deployment.get('strategy_deployment_id')} requires an enabled Session Profile for {mode}")
    selected_route_ids = {str(value) for value in deployment.get("execution_route_ids") or []}
    execution_routes = [
        row for row in sessions.get("execution_routes") or []
        if str(row.get("execution_route_id") or "") in selected_route_ids
        and str(row.get("session_profile_id") or "") == str(session_profile.get("session_profile_id") or "")
        and mode in set(row.get("modes") or [])
        and bool(row.get("enabled", True))
    ]
    if not execution_routes:
        raise ValueError(f"Strategy Deployment {deployment.get('strategy_deployment_id')} requires an enabled Execution Route")
    profiles = {
        str(row["profile_id"]): row
        for row in dict(model["strategy"]).get("profiles") or []
    }
    profile = profiles.get(str(run_plan.get("profile_id") or ""))
    if profile is None:
        raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} references an unknown Strategy Profile")
    profile_rule_sets = _profile_rule_sets(
        profile, dict(model["market_discovery"])
    )
    profile_action_policies = _profile_action_policies(
        profile, dict(model["trading_actions"])
    )
    oms_profiles = {
        str(row["profile_id"]): row
        for row in dict(model["oms"]).get("profiles") or []
    }
    oms_profile_ids = {str(row.get("oms_profile_id") or "") for row in execution_routes}
    if len(oms_profile_ids) != 1:
        raise ValueError("One Strategy Deployment cannot mix OMS profiles across execution routes")
    oms = oms_profiles.get(next(iter(oms_profile_ids)))
    if oms is None:
        raise ValueError(f"Execution Route references an unknown OMS profile")
    selected_mandate_ids = {str(value) for value in deployment.get("portfolio_mandate_ids") or []}
    mandates = [
        row for row in dict(model["portfolio"]).get("mandates") or []
        if str(row.get("mandate_id") or "") in selected_mandate_ids
        and bool(row.get("enabled", True))
    ]
    account_keys = {str(row.get("account_key") or "") for row in execution_routes}
    bindings = [
        (
            _runtime_account_binding(dict(row))
            if resolve_broker_ids
            else deepcopy(dict(row))
        )
        for row in dict(model["accounts"]).get("bindings") or []
        if str(row.get("account_key")) in account_keys
        and bool(row.get("enabled", True))
        and mode in set(row.get("modes") or [])
    ]
    if {str(row.get("account_key") or "") for row in bindings} != account_keys:
        raise ValueError(f"Strategy Deployment {deployment.get('strategy_deployment_id')} selected an account unavailable for {mode}")
    mandate_by_account = {str(row["account_key"]): row for row in mandates}
    assignment_mode = str(mandates[0].get("assignment_mode") or "single") if mandates else "single"
    total_weight = sum(float(row.get("allocation_weight") or 1.0) for row in mandates)
    for binding in bindings:
        mandate = mandate_by_account[str(binding["account_key"])]
        allocation = float(mandate.get("maximum_cash_fraction", 1.0))
        if assignment_mode == "weighted":
            allocation *= float(mandate.get("allocation_weight") or 1.0) / max(total_weight, 1e-12)
        binding["strategy_allocation"] = allocation
        binding["run_plan_assignment_mode"] = assignment_mode
        binding["allocation_weight"] = float(mandate.get("allocation_weight") or 1.0)
    runtime_assignments = [
        deepcopy(row) for row in run_plan.get("runtime_assignments") or []
        if str(row.get("account_key")) in account_keys
    ]
    universes = {
        str(row["universe_id"]): row
        for row in dict(model["run_plans"]).get("universes") or []
    }
    universe = universes.get(str(run_plan.get("universe_id") or ""))
    if universe is None:
        raise ValueError(
            f"Run Plan {run_plan.get('run_plan_id')} references an unknown Watch Universe"
        )
    universe = deepcopy(universe)
    if str(universe.get("source") or "") == "watchlist":
        universe = _resolve_watchlist_universe(universe, mode=mode)
    elif str(universe.get("source") or "") == "signal_stream":
        universe = _resolve_signal_stream_universe(
            universe,
            mode=mode,
            configuration=model,
        )
    if str(universe.get("source") or "") in {"watchlist", "signal_stream"}:
        if mode in {"live", "paper"}:
            from src.backend.real_live_trading_service import tradable_symbol_map

            identities = tradable_symbol_map(list(universe.get("symbols") or []))
            existing_pairs = {
                (
                    str(row.get("account_key") or ""),
                    str(row.get("ticker") or "").upper(),
                )
                for row in runtime_assignments
            }
            for account_key in sorted(account_keys):
                for ticker in universe.get("symbols") or []:
                    identity = identities.get(str(ticker).upper(), {})
                    conid = int(identity.get("ibkr_conid") or 0)
                    if not conid or (account_key, str(ticker).upper()) in existing_pairs:
                        continue
                    runtime_assignments.append(
                        {
                            "assignment_id": f"{run_plan['run_plan_id']}:{account_key}:{str(ticker).upper()}",
                            "account_key": account_key,
                            "ticker": str(ticker).upper(),
                            "conid": conid,
                            "status": "watching",
                            "permissions": {},
                            "parameters": {},
                            "source": f"{str(universe.get('source') or 'watchlist')}_runtime",
                        }
                    )
    for assignment in runtime_assignments:
        ticker = str(assignment.get("ticker") or "").upper()
        side = str(
            dict(dict(profile.get("lifecycle") or {}).get("trading_behavior") or {}).get("side")
            or "long"
        )
        assignment.setdefault(
            "campaign_id",
            f"{run_plan['run_plan_id']}:{ticker}:{side}",
        )
        policy = _effective_campaign_policy(run_plan)
        existing_permissions = dict(assignment.get("permissions") or {})
        assignment["permissions"] = {
            **existing_permissions,
            "observe": True,
            "enter": str(policy.get("initial_entry_authority")) != "disabled",
            "reenter": str(policy.get("reentry_authority")) != "disabled",
            "exit": str(policy.get("exit_authority")) != "disabled",
        }
        assignment["strategy_id"] = str(profile["definition_id"])
        assignment["strategy_revision"] = int(profile["definition_revision"])
        assignment["profile_id"] = str(profile["profile_id"])
        assignment["run_plan_id"] = str(run_plan["run_plan_id"])
        assignment["deployment_id"] = str(deployment["strategy_deployment_id"])
        assignment["universe_id"] = str(run_plan["universe_id"])
        assignment["book_id"] = str(run_plan["book_id"])
        assignment["side"] = side
        assignment["campaign_policy"] = deepcopy(policy)
        assignment["resolved_parameters"] = merged_assignment_parameters(
            {
                "strategy": {"parameters": _parameters_with_action_policies(profile, profile_rule_sets, profile_action_policies)},
                "oms": {
                    "settings": deepcopy(dict(oms.get("settings") or {})),
                    "execution_policies": deepcopy(
                        dict(model["oms"]).get("execution_policies") or []
                    ),
                    "protection_profiles": deepcopy(
                        dict(model["oms"]).get("protection_profiles") or []
                    ),
                },
                "campaign_policy": deepcopy(policy),
            },
            assignment,
        )
    discovery = dict(model["market_discovery"])
    selected_stream_ids = {
        str(value) for value in run_plan.get("signal_stream_ids") or [] if str(value)
    }
    selected_streams = [
        deepcopy(row)
        for row in discovery.get("signal_streams") or []
        if str(row.get("signal_stream_id") or "") in selected_stream_ids
    ]
    activation_rule_ids = {
        str(value)
        for stream in selected_streams
        for value in stream.get("inclusion_rule_sets") or []
        if str(value)
    }
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "run_plan": deepcopy(run_plan),
        "deployment": {**deepcopy(deployment), "deployment_id": str(deployment["strategy_deployment_id"])},
        "session_profile": deepcopy(session_profile),
        "execution_routes": deepcopy(execution_routes),
        "universe": universe,
        "campaign_policy": deepcopy(
            _effective_campaign_policy(run_plan)
        ),
        "account_topology": {
            "mode": assignment_mode,
            "legs": [
                {
                    "account_key": str(row.get("account_key") or ""),
                    "allocation_weight": float(row.get("allocation_weight") or 1.0),
                    "maximum_cash_fraction": float(row.get("maximum_cash_fraction") or 0),
                    "maximum_action_authority": str(row.get("maximum_action_authority") or "manual"),
                }
                for row in mandates
            ],
        },
        "strategy_profile": deepcopy(profile),
        "signal_activation": {
            "signal_streams": selected_streams,
            "rule_sets": [
                deepcopy(row)
                for row in discovery.get("rule_sets") or []
                if str(row.get("rule_set_id") or "") in activation_rule_ids
            ],
            "data_fields": deepcopy(discovery.get("data_fields") or []),
            "column_catalog": deepcopy(discovery.get("column_catalog") or []),
            "data_field_plan": deepcopy(discovery.get("data_field_plan") or {}),
        },
        "strategy": {
            "strategy_id": profile["definition_id"],
            "revision": int(profile["definition_revision"]),
            "name": profile["name"],
            "profile_id": profile["profile_id"],
            "profile_revision": int(profile.get("revision") or 1),
            "parameters": _parameters_with_action_policies(profile, profile_rule_sets, profile_action_policies),
            "action_definitions": deepcopy(dict(model["trading_actions"]).get("definitions") or []),
            "action_policies": deepcopy(profile_action_policies),
        },
        "assignments": runtime_assignments,
        "portfolio": {
            "policies": deepcopy(dict(model["portfolio"]).get("policies") or []),
            "groups": deepcopy(dict(model["portfolio"]).get("groups") or []),
            "mandates": deepcopy(mandates),
        },
        "oms": {
            **deepcopy(dict(oms.get("settings") or {})),
            "profile_id": str(oms.get("profile_id") or ""),
            "profile_revision": int(oms.get("revision") or 1),
            "execution_policies": deepcopy(
                dict(model["oms"]).get("execution_policies") or []
            ),
            "protection_profiles": deepcopy(
                dict(model["oms"]).get("protection_profiles") or []
            ),
        },
        "accounts": {"bindings": bindings},
    }


def _resolve_watchlist_universe(
    universe: dict[str, Any], *, mode: str
) -> dict[str, Any]:
    result = deepcopy(universe)
    if mode not in {"live", "paper"}:
        result["symbols"] = []
        result["resolved"] = False
        result["resolution_status"] = "historical_membership_required"
        return result
    from src.backend.watchlist_runtime_service import WATCHLIST_RUNTIME

    runtime = WATCHLIST_RUNTIME.snapshot()
    watchlist_ids = [
        str(value)
        for value in result.get("scanner_view_ids")
        or [result.get("scanner_view_id")]
        if str(value or "")
    ]
    snapshots = [
        row
        for row in runtime.get("watchlists") or []
        if str(row.get("watchlist_id") or "") in set(watchlist_ids)
    ]
    result["symbols"] = sorted(
        {
            str(row.get("ticker") or "").upper()
            for snapshot in snapshots
            for row in dict(snapshot).get("members") or []
            if str(row.get("ticker") or "").strip()
        }
    )
    result["resolved"] = len(snapshots) == len(watchlist_ids)
    result["resolved_at"] = runtime.get("as_of")
    result["resolution_status"] = "ready" if result["resolved"] else "awaiting_watchlist_snapshot"
    return result


def _resolve_signal_stream_universe(
    universe: dict[str, Any], *, mode: str, configuration: dict[str, Any]
) -> dict[str, Any]:
    result = deepcopy(universe)
    if mode not in {"live", "paper"}:
        result["symbols"] = []
        result["resolved"] = False
        result["resolution_status"] = "historical_signal_occurrences_required"
        return result
    from src.backend.signal_stream_runtime_service import SIGNAL_STREAM_RUNTIME

    stream_ids = {
        str(value)
        for value in result.get("signal_stream_ids") or []
        if str(value)
    }
    snapshot = SIGNAL_STREAM_RUNTIME.snapshot(
        trading_journal(),
        configuration=configuration,
    )
    result["symbols"] = sorted({
        str(row.get("ticker") or "").upper()
        for row in snapshot.get("occurrences") or []
        if str(row.get("signal_stream_id") or "") in stream_ids
        and str(row.get("ticker") or "").strip()
    })
    configured = {
        str(row.get("signal_stream_id") or "")
        for row in snapshot.get("signal_streams") or []
    }
    result["resolved"] = bool(stream_ids) and stream_ids <= configured
    result["resolved_at"] = snapshot.get("as_of")
    result["resolution_status"] = (
        "ready" if result["resolved"] else "awaiting_signal_stream_snapshot"
    )
    return result


def _default_strategy_lifecycle(parameters: dict[str, Any]) -> dict[str, Any]:
    rules = deepcopy(
        dict(parameters.get("entry_rules") or default_entry_decision_rules(parameters))
    )
    reentry = dict(parameters.get("reentry") or {})
    final_exit = dict(parameters.get("final_exit") or {})
    initial_rules = {
        "opportunity": deepcopy(dict(rules.get("trigger") or {})),
        "confirmation": deepcopy(dict(rules.get("confirmation") or {})),
        "blockers": deepcopy(dict(rules.get("veto") or {})),
    }
    lifecycle = {
        "phase_modes": {
            "initial_entry": "automatic",
            "manage": "automatic",
            "reentry": "automatic",
            "exit": "automatic",
        },
        "trading_behavior": {
            "side": "long",
            "eligible_sessions": ["regular"],
        },
        "initial_entry": {
            **deepcopy(initial_rules),
            "action_id": "position.enter_long",
            "capital_request": _default_capital_request("mandate_fraction", 0.20),
            "order_intent": _default_order_intent("adaptive_urgent"),
            "add_steps": [
                {
                    "step_id": "confirmed-position-add",
                    "action_id": "position.add_long",
                    "name": "Confirmed position add",
                    "enabled": True,
                    "rules": _single_rule_stage(
                        "bullish-structure-add",
                        "Bullish structure continuation",
                        "indicator.structure.bullish_choch",
                        "1s",
                        "is_true",
                    ),
                    "capital_request": _default_capital_request(
                        "mandate_fraction", 0.50
                    ),
                    "order_intent": _default_order_intent("adaptive_urgent"),
                    "maximum_uses": 2,
                }
            ],
        },
        "reentry": {
            "action_id": "position.enter_long",
            "enabled": bool(reentry.get("enabled", True)),
            "cooldown_ms": int(reentry.get("cooldown_ms") or 0),
            "maximum_attempts": int(reentry.get("maximum_attempts") or 0),
            "unlimited_attempts": bool(reentry.get("unlimited_attempts", False)),
            "require_new_confirmation": bool(
                reentry.get("require_new_confirmation", True)
            ),
            "pullback_reclaim": deepcopy(
                dict(reentry.get("pullback_reclaim") or {})
            ),
            "rules": deepcopy(initial_rules),
            "capital_request": _default_capital_request("mandate_fraction", 0.20),
            "order_intent": _default_order_intent("adaptive_urgent"),
        },
        "exit": {"rule_sets": _default_exit_rule_sets(final_exit)},
    }
    return lifecycle


def _default_capital_request(mode: str, value: float) -> dict[str, Any]:
    return {
        "mode": mode,
        "value": value,
        "allow_replacement": False,
    }


def _default_order_intent(policy: str) -> dict[str, Any]:
    return {
        "execution_policy": policy,
        "protection_profile": "hybrid-single",
        "partial_fill_policy": "complete_remainder",
        "deadline_ms": 750,
    }


def _single_rule_stage(
    group_id: str,
    label: str,
    source_id: str,
    timeframe: str,
    comparator: str,
    *,
    value: float | None = None,
) -> dict[str, Any]:
    return {
        "operator": "any",
        "groups": [{
            "group_id": group_id,
            "label": label,
            "operator": "all",
            "required_score": 1.0,
            "enabled": True,
            "conditions": [{
                "condition_id": f"{group_id}-condition",
                "left_source_id": source_id,
                "left_timeframe": timeframe,
                "comparator": comparator,
                "right_source_id": "",
                "right_timeframe": "",
                "value": value,
                "enabled": True,
            }],
        }],
    }


def _rule_set_identifier(context: str, group_id: str, existing: set[str]) -> str:
    base = "-".join(
        part for part in "".join(
            character.lower() if character.isalnum() else " "
            for character in f"{context}-{group_id}"
        ).split() if part
    ) or "rule-set"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    existing.add(candidate)
    return candidate


def _catalog_stage_rules(
    stage: dict[str, Any],
    context: str,
    catalog: list[dict[str, Any]],
    existing: set[str],
) -> dict[str, Any]:
    if isinstance(stage.get("expression"), dict):
        return deepcopy(stage)
    children: list[dict[str, Any]] = []
    for index, raw_group in enumerate(stage.get("groups") or []):
        group = deepcopy(dict(raw_group))
        group_id = str(group.pop("group_id", "") or f"rule-{index + 1}")
        rule_set_id = _rule_set_identifier(
            context,
            group_id,
            existing,
        )
        label = str(group.pop("label", "") or f"Rule set {index + 1}")
        condition_count = len(group.get("conditions") or [])
        context_label = context.replace("-", " ")
        catalog.append({
            "rule_set_id": rule_set_id,
            "name": label,
            "description": _strategy_rule_set_description(
                group_id, label, context_label, condition_count
            ),
            **group,
        })
        children.append({"kind": "rule_set", "rule_set_id": rule_set_id})
    return {
        "expression": {
            "kind": "operator",
            "operator": "and" if str(stage.get("operator") or "any") == "all" else "or",
            "children": children,
        }
    }


def _strategy_rule_set_description(
    group_id: str,
    label: str,
    context_label: str,
    condition_count: int,
) -> str:
    descriptions = {
        "break-structure": "Requires last price to clear the confirmed structural high by the configured breakout buffer.",
        "break-vwap": "Requires last price to clear current VWAP by the configured breakout buffer.",
        "bullish-choch": "Requires QMD to publish a true bullish change-of-character event on the configured breakout timeframe.",
        "price-volume-expansion": "Requires the QMD price-and-volume expansion score to meet the configured entry minimum.",
        "company-news": "Requires the causal company-news score to meet the configured entry minimum.",
        "qmd-alignment": "Requires both QMD flow-and-structure score and confidence to meet their configured confirmation minimums.",
        "vwap-confirmation": "Requires last price at or above VWAP while the VWAP slope meets its configured minimum.",
        "macd-confirmation": "Requires the MACD line at or above its signal line and a positive MACD histogram.",
        "flow-price-divergence": "Blocks entry when the QMD flow-price divergence score reaches the configured veto threshold.",
        "liquidity-dislocation": "Blocks entry when the QMD liquidity-dislocation score reaches the configured veto threshold.",
        "bullish-structure-add": "Allows a position add only after QMD publishes a new true bullish change-of-character event.",
        "lose-entry-structure": "Passes when last price falls below the confirmed structural high used by the entry thesis.",
        "adverse-qmd-score": "Passes when the QMD flow-and-structure score falls to or below the configured adverse threshold.",
        "qmd-confidence": "Requires QMD flow-and-structure confidence to meet the configured exit-evidence minimum.",
        "adverse-macd-line": "Passes when the MACD line falls below its signal line.",
        "adverse-macd-histogram": "Passes when the MACD histogram is negative.",
    }
    if group_id in descriptions:
        return descriptions[group_id]
    return (
        f"{label} is reusable {context_label} evidence. It passes only when the "
        f"{condition_count} condition{'s' if condition_count != 1 else ''} below "
        f"{'evaluate' if condition_count != 1 else 'evaluates'} true."
    )


def _migrate_profile_rule_catalog(profile: dict[str, Any]) -> None:
    catalog = list(deepcopy(profile.get("rule_set_catalog") or []))
    existing = {
        str(rule_set.get("rule_set_id") or "")
        for rule_set in catalog
        if str(rule_set.get("rule_set_id") or "")
    }
    lifecycle = dict(profile.get("lifecycle") or {})
    initial = dict(lifecycle.get("initial_entry") or {})
    for stage_name in ("opportunity", "confirmation", "blockers"):
        initial[stage_name] = _catalog_stage_rules(
            dict(initial.get(stage_name) or {}),
            f"initial-entry-{stage_name}",
            catalog,
            existing,
        )
    for step in initial.get("add_steps") or []:
        step["rules"] = _catalog_stage_rules(
            dict(step.get("rules") or {}),
            f"add-{step.get('step_id') or 'step'}",
            catalog,
            existing,
        )
    lifecycle["initial_entry"] = initial
    reentry = dict(lifecycle.get("reentry") or {})
    reentry_rules = dict(reentry.get("rules") or {})
    for stage_name in ("opportunity", "confirmation", "blockers"):
        reentry_rules[stage_name] = _catalog_stage_rules(
            dict(reentry_rules.get(stage_name) or {}),
            f"reentry-{stage_name}",
            catalog,
            existing,
        )
    reentry["rules"] = reentry_rules
    lifecycle["reentry"] = reentry
    exit_section = dict(lifecycle.get("exit") or {})
    for route in exit_section.get("rule_sets") or []:
        route["rules"] = _catalog_stage_rules(
            dict(route.get("rules") or {}),
            f"exit-{route.get('rule_set_id') or 'route'}",
            catalog,
            existing,
        )
    lifecycle["exit"] = exit_section
    canonical_by_fingerprint: dict[str, str] = {}
    remapped_ids: dict[str, str] = {}
    deduplicated: list[dict[str, Any]] = []
    for rule_set in catalog:
        fingerprint = json.dumps(
            {
                "name": str(rule_set.get("name") or ""),
                "enabled": bool(rule_set.get("enabled", True)),
                "operator": str(rule_set.get("operator") or "all"),
                "required_score": float(rule_set.get("required_score") or 1),
                "conditions": rule_set.get("conditions") or [],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        rule_set_id = str(rule_set.get("rule_set_id") or "")
        canonical_id = canonical_by_fingerprint.get(fingerprint)
        if canonical_id:
            remapped_ids[rule_set_id] = canonical_id
            continue
        canonical_by_fingerprint[fingerprint] = rule_set_id
        remapped_ids[rule_set_id] = rule_set_id
        deduplicated.append(rule_set)

    origin = str(profile.get("origin") or "user")
    atomic = origin == "system"
    for rule_set in deduplicated:
        rule_set_id = str(rule_set.get("rule_set_id") or "rule-set")
        readable_name = " ".join(part.capitalize() for part in rule_set_id.replace("_", "-").split("-") if part)
        rule_set["name"] = str(rule_set.get("name") or readable_name or "Rule set")
        rule_set["description"] = str(
            rule_set.get("description")
            or f"Reusable strategy evidence composed from {len(rule_set.get('conditions') or [])} registered condition(s)."
        )
        rule_set["scope"] = str(rule_set.get("scope") or "strategy")
        rule_set["origin"] = str(rule_set.get("origin") or origin)
        rule_set["atomic"] = bool(rule_set.get("atomic", atomic))
        rule_set["editable"] = bool(rule_set.get("editable", not rule_set["atomic"]))
        rule_set["protected"] = bool(rule_set.get("protected", rule_set["atomic"]))
        rule_set["revision"] = max(1, int(rule_set.get("revision") or 1))
        rule_set["publication_status"] = str(
            rule_set.get("publication_status")
            or ("published" if rule_set["atomic"] else "draft")
        )
        _normalize_rule_set_conditions(rule_set)

    def remap_expression(expression: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(expression)
        if str(result.get("kind") or "") == "rule_set":
            rule_set_id = str(result.get("rule_set_id") or "")
            result["rule_set_id"] = remapped_ids.get(rule_set_id, rule_set_id)
            return result
        result["children"] = [
            remap_expression(dict(child))
            for child in result.get("children") or []
        ]
        return result

    for stage in (
        *(initial.get(name) or {} for name in ("opportunity", "confirmation", "blockers")),
        *(step.get("rules") or {} for step in initial.get("add_steps") or []),
        *(reentry_rules.get(name) or {} for name in ("opportunity", "confirmation", "blockers")),
        *(route.get("rules") or {} for route in exit_section.get("rule_sets") or []),
    ):
        stage["expression"] = remap_expression(dict(stage.get("expression") or {}))
    profile["rule_set_catalog"] = deduplicated
    profile["lifecycle"] = lifecycle


def _expression_rule_set_ids(expression: dict[str, Any]) -> set[str]:
    if str(expression.get("kind") or "") == "rule_set":
        rule_set_id = str(expression.get("rule_set_id") or "")
        return {rule_set_id} if rule_set_id else set()
    result: set[str] = set()
    for child in expression.get("children") or []:
        result.update(_expression_rule_set_ids(dict(child)))
    return result


def _profile_rule_set_ids(lifecycle: dict[str, Any]) -> list[str]:
    initial = dict(lifecycle.get("initial_entry") or {})
    reentry_rules = dict(dict(lifecycle.get("reentry") or {}).get("rules") or {})
    exit_section = dict(lifecycle.get("exit") or {})
    stages = [
        *(dict(initial.get(name) or {}) for name in ("opportunity", "confirmation", "blockers")),
        *(dict(step.get("rules") or {}) for step in initial.get("add_steps") or []),
        *(dict(reentry_rules.get(name) or {}) for name in ("opportunity", "confirmation", "blockers")),
        *(dict(route.get("rules") or {}) for route in exit_section.get("rule_sets") or []),
    ]
    referenced: set[str] = set()
    for stage in stages:
        referenced.update(
            _expression_rule_set_ids(dict(stage.get("expression") or {}))
        )
    return sorted(referenced)


def _profile_rule_sets(
    profile: dict[str, Any], market_discovery: dict[str, Any]
) -> list[dict[str, Any]]:
    catalog = {
        str(rule_set.get("rule_set_id") or ""): rule_set
        for rule_set in market_discovery.get("rule_sets") or []
        if str(rule_set.get("rule_set_id") or "")
    }
    references = _profile_rule_set_ids(dict(profile.get("lifecycle") or {}))
    return [deepcopy(catalog[rule_set_id]) for rule_set_id in references if rule_set_id in catalog]


def _profile_action_policies(
    profile: dict[str, Any], trading_actions: dict[str, Any]
) -> list[dict[str, Any]]:
    catalog = {
        str(policy.get("policy_id") or ""): policy
        for policy in trading_actions.get("policies") or []
    }
    return [
        deepcopy(catalog[policy_id])
        for policy_id in profile.get("action_policy_ids") or []
        if str(policy_id) in catalog
    ]


def _materialize_rule_stage(
    stage: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expression = deepcopy(dict(stage.get("expression") or {}))
    return {
        "expression": expression,
        "rule_sets": [
            deepcopy(catalog[rule_set_id])
            for rule_set_id in sorted(_expression_rule_set_ids(expression))
            if rule_set_id in catalog
        ],
    }


def _default_exit_rule_stage(mechanism: str) -> dict[str, Any]:
    if mechanism == "failed_breakout":
        stage = _single_rule_stage(
            "lose-entry-structure",
            "Lose entry structure",
            "market.last_price",
            "1s",
            "less_than",
        )
        condition = stage["groups"][0]["conditions"][0]
        condition["right_source_id"] = "indicator.structure.swing_high"
        condition["right_timeframe"] = "1s"
        return stage
    if mechanism == "bearish_qmd_macd":
        return _single_rule_stage(
            "adverse-qmd",
            "Adverse QMD momentum",
            "indicator.flow_structure.score",
            "100ms",
            "less_or_equal",
            value=-0.35,
        )
    return {"operator": "any", "groups": []}


def _default_exit_rule_sets(final_exit: dict[str, Any]) -> list[dict[str, Any]]:
    failed = {
        "rule_set_id": "failed-entry-thesis",
        "name": "Failed entry thesis",
        "summary": "Exit when price loses the configured entry reference during its validity window.",
        "enabled": bool(final_exit.get("exit_on_failed_breakout", True)),
        "rules": _default_exit_rule_stage("failed_breakout"),
        "timing": {"active_after_ms": 0, "expires_after_ms": 60_000},
        "action": "close",
        "action_id": "position.exit_long",
        "position_fraction": 1.0,
        "order_intent": _default_order_intent("adaptive_urgent"),
    }
    adverse = {
        "rule_set_id": "adverse-momentum",
        "name": "Adverse momentum",
        "summary": "Exit when the configured QMD and MACD evidence turns against the position.",
        "enabled": bool(final_exit.get("bearish_momentum_enabled", True)),
        "rules": {
            "operator": "all",
            "groups": [
                _single_rule_stage(
                    "adverse-qmd-score",
                    "Adverse QMD score",
                    "indicator.flow_structure.score",
                    "100ms",
                    "less_or_equal",
                    value=float(final_exit.get("qmd_score") or -0.35),
                )["groups"][0],
                _single_rule_stage(
                    "qmd-confidence",
                    "QMD confidence",
                    "indicator.flow_structure.confidence",
                    "100ms",
                    "greater_or_equal",
                    value=float(final_exit.get("qmd_confidence") or 0.55),
                )["groups"][0],
                _single_rule_stage(
                    "adverse-macd-line",
                    "MACD line below signal",
                    "indicator.macd.line",
                    "5s",
                    "less_than",
                )["groups"][0],
                _single_rule_stage(
                    "adverse-macd-histogram",
                    "Negative MACD histogram",
                    "indicator.macd.histogram",
                    "5s",
                    "less_than",
                    value=0,
                )["groups"][0],
            ],
        },
        "timing": {"active_after_ms": 0, "expires_after_ms": 0},
        "action": "close",
        "action_id": "position.exit_long",
        "position_fraction": 1.0,
        "order_intent": _default_order_intent("adaptive_urgent"),
    }
    adverse["rules"]["groups"][2]["conditions"][0].update({
        "right_source_id": "indicator.macd.signal",
        "right_timeframe": "5s",
        "value": None,
    })
    if not bool(final_exit.get("require_macd_bearish", True)):
        adverse["rules"]["groups"] = adverse["rules"]["groups"][:2]
    return [failed, adverse]


def _parameters_without_lifecycle(parameters: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(parameters)
    for key in ("entry_rules", "re_evaluation", "strategy_behavior", "reentry", "final_exit", "exit_routes", "sizing", "add", "execution"):
        result.pop(key, None)
    return result


def _migrate_lifecycle_v5(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    result = deepcopy(lifecycle)
    defaults = _default_strategy_lifecycle(parameters)
    behavior = result.setdefault("trading_behavior", {})
    behavior.setdefault("side", "long")
    if behavior.get("side") == "both":
        behavior["side"] = "long"
    initial = result.setdefault("initial_entry", {})
    for stage_name in ("opportunity", "confirmation", "blockers"):
        initial.setdefault(stage_name, deepcopy(defaults["initial_entry"][stage_name]))
    legacy_sizing = dict(parameters.get("sizing") or {})
    initial.setdefault(
        "capital_request",
        _default_capital_request(
            str(legacy_sizing.get("request_mode") or "mandate_fraction"),
            float(legacy_sizing.get("request_value") or 0.20),
        ),
    )
    initial.setdefault("order_intent", deepcopy(defaults["initial_entry"]["order_intent"]))
    initial.setdefault("add_steps", deepcopy(defaults["initial_entry"]["add_steps"]))
    reentry = result.setdefault("reentry", {})
    reentry.pop("reuse_initial_entry", None)
    reentry.setdefault("rules", {
        stage_name: deepcopy(initial[stage_name])
        for stage_name in ("opportunity", "confirmation", "blockers")
    })
    reentry.setdefault("capital_request", deepcopy(initial["capital_request"]))
    reentry.setdefault("order_intent", deepcopy(initial["order_intent"]))
    reentry.setdefault("enabled", True)
    reentry.setdefault("cooldown_ms", 0)
    reentry.setdefault("maximum_attempts", 0)
    reentry.setdefault("unlimited_attempts", False)
    reentry.setdefault("require_new_confirmation", True)
    exit_config = result.setdefault("exit", {})
    if "routes" not in exit_config and "rule_sets" not in exit_config:
        exit_config["rule_sets"] = deepcopy(defaults["exit"]["rule_sets"])
    _normalize_lifecycle_action_ids(result)
    return result


def _normalize_lifecycle_action_ids(lifecycle: dict[str, Any]) -> None:
    """Attach the registered broker-neutral action used by each lifecycle route."""

    side = str(dict(lifecycle.get("trading_behavior") or {}).get("side") or "long")
    suffix = "short" if side == "short" else "long"
    initial = lifecycle.setdefault("initial_entry", {})
    initial.setdefault("action_id", f"position.enter_{suffix}")
    for step in initial.get("add_steps") or []:
        step.setdefault("action_id", f"position.add_{suffix}")
    reentry = lifecycle.setdefault("reentry", {})
    reentry.setdefault("action_id", f"position.enter_{suffix}")
    for route in dict(lifecycle.setdefault("exit", {})).get("rule_sets") or []:
        action = str(route.get("action") or "close")
        route.setdefault(
            "action_id",
            f"position.{'reduce' if action == 'reduce' else 'exit'}_{suffix}",
        )


def _migrate_rule_stage_v6(stage: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(stage)
    result["operator"] = (
        str(result.get("operator"))
        if str(result.get("operator")) in {"all", "any"}
        else "all"
    )
    result.pop("minimum_score", None)
    for group in result.get("groups") or []:
        legacy_weight = float(group.pop("weight", 1.0) or 1.0)
        if str(group.get("operator") or "") not in {"all", "any", "score"}:
            group["operator"] = "all"
        group.setdefault("required_score", max(0.01, min(1.0, legacy_weight)))
    return result


def _migrate_lifecycle_v6(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    result = _migrate_lifecycle_v5(lifecycle, parameters)
    initial = dict(result.get("initial_entry") or {})
    for key in ("opportunity", "confirmation", "blockers"):
        initial[key] = _migrate_rule_stage_v6(dict(initial.get(key) or {}))
    for step in initial.get("add_steps") or []:
        step["rules"] = _migrate_rule_stage_v6(dict(step.get("rules") or {}))
    result["initial_entry"] = initial
    reentry = dict(result.get("reentry") or {})
    reentry_rules = dict(reentry.get("rules") or {})
    for key in ("opportunity", "confirmation", "blockers"):
        reentry_rules[key] = _migrate_rule_stage_v6(
            dict(reentry_rules.get(key) or {})
        )
    reentry["rules"] = reentry_rules
    result["reentry"] = reentry

    exit_config = dict(result.get("exit") or {})
    if not isinstance(exit_config.get("rule_sets"), list):
        migrated: list[dict[str, Any]] = []
        for route in sorted(
            list(exit_config.get("routes") or []),
            key=lambda row: -int(row.get("priority") or 0),
        ):
            mechanism = str(route.get("mechanism") or "")
            if mechanism == "protective_stop":
                continue
            rules = _migrate_rule_stage_v6(dict(route.get("rules") or {}))
            settings = dict(route.get("settings") or {})
            if mechanism == "bearish_qmd_macd":
                defaults = _default_exit_rule_sets({
                    "qmd_score": settings.get("qmd_score", -0.35),
                    "qmd_confidence": settings.get("qmd_confidence", 0.55),
                    "require_macd_bearish": settings.get(
                        "require_macd_bearish", True
                    ),
                    "bearish_momentum_enabled": route.get("enabled", True),
                })[1]
                rules = defaults["rules"]
            migrated.append({
                "rule_set_id": str(
                    route.get("route_id") or f"exit-rule-{len(migrated) + 1}"
                ),
                "name": str(route.get("name") or "Exit rule set"),
                "summary": str(route.get("summary") or ""),
                "enabled": bool(route.get("enabled", True)),
                "rules": rules,
                "timing": {
                    "active_after_ms": 0,
                    "expires_after_ms": (
                        60_000 if mechanism == "failed_breakout" else 0
                    ),
                },
                "action": str(route.get("action") or "close"),
                "position_fraction": float(
                    route.get("position_fraction") or 1.0
                ),
                "order_intent": deepcopy(
                    route.get("order_intent")
                    or _default_order_intent("adaptive_urgent")
                ),
            })
        exit_config["rule_sets"] = (
            migrated
            or deepcopy(
                _default_exit_rule_sets(
                    dict(parameters.get("final_exit") or {})
                )
            )
        )
    exit_config.pop("routes", None)
    for rule_set in exit_config.get("rule_sets") or []:
        rule_set["rules"] = _migrate_rule_stage_v6(
            dict(rule_set.get("rules") or {})
        )
        rule_set.setdefault(
            "timing", {"active_after_ms": 0, "expires_after_ms": 0}
        )
        rule_set.setdefault("action", "close")
        rule_set.setdefault("position_fraction", 1.0)
        rule_set.setdefault(
            "order_intent", _default_order_intent("adaptive_urgent")
        )
    result["exit"] = exit_config
    return result


def _normalize_smart_order_intent(intent: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(intent)
    result.pop("time_in_force", None)
    result.pop("outside_rth", None)
    return result


def _migrate_lifecycle_v7(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    result = _migrate_lifecycle_v6(lifecycle, parameters)
    initial = dict(result.get("initial_entry") or {})
    initial["order_intent"] = _normalize_smart_order_intent(
        dict(initial.get("order_intent") or {})
    )
    for step in initial.get("add_steps") or []:
        step["order_intent"] = _normalize_smart_order_intent(
            dict(step.get("order_intent") or {})
        )
    result["initial_entry"] = initial
    reentry = dict(result.get("reentry") or {})
    reentry["order_intent"] = _normalize_smart_order_intent(
        dict(reentry.get("order_intent") or {})
    )
    result["reentry"] = reentry
    exit_config = dict(result.get("exit") or {})
    for rule_set in exit_config.get("rule_sets") or []:
        rule_set["order_intent"] = _normalize_smart_order_intent(
            dict(rule_set.get("order_intent") or {})
        )
    result["exit"] = exit_config
    return result


def _migrate_lifecycle_v10(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    result = _migrate_lifecycle_v7(lifecycle, parameters)
    behavior = dict(result.get("trading_behavior") or {})
    legacy_trigger = str(behavior.pop("evaluation_trigger", "") or "indicator_update")
    result["trading_behavior"] = behavior
    result.setdefault(
        "re_evaluation",
        {
            "rule_sets": [
                {
                    "rule_set_id": f"{legacy_trigger.replace('_', '-')}-events",
                    "name": legacy_trigger.replace("_", " ").title(),
                    "enabled": True,
                    "event_type": legacy_trigger,
                    "source_id": "",
                    "campaign_states": ["flat", "position_open"],
                }
            ]
        },
    )
    return result


def _migrate_lifecycle_v13(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    had_phase_modes = isinstance(lifecycle.get("phase_modes"), dict)
    legacy_reentry_enabled = bool(
        dict(lifecycle.get("reentry") or {}).get("enabled", True)
    )
    result = _migrate_lifecycle_v10(lifecycle, parameters)
    modes = result.setdefault("phase_modes", {})
    for phase_name in ("initial_entry", "manage", "reentry", "exit"):
        if str(modes.get(phase_name) or "") not in {"automatic", "manual"}:
            modes[phase_name] = "automatic"
    if not had_phase_modes and not legacy_reentry_enabled:
        modes["reentry"] = "manual"
    result["reentry"]["enabled"] = modes["reentry"] == "automatic"
    return result


def _migrate_lifecycle_v14(
    lifecycle: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    result = _migrate_lifecycle_v13(lifecycle, parameters)
    result.setdefault("trading_behavior", {}).pop("adopt_manual_positions", None)
    return result


def _default_campaign_policy() -> dict[str, Any]:
    return {
        "initial_entry_authority": "confirm",
        "reentry_authority": "confirm",
        "exit_authority": "automatic",
        "protective_exit_authority": "automatic",
        "maximum_reentries": 3,
        "reentry_cooldown_ms": 1_000,
        "maximum_initial_watch_ms": 0,
        "session_end_behavior": "keep_watching",
        "retain_ticker_while_paused": True,
    }


def _default_action_authority() -> dict[str, str]:
    return {
        "default": "confirm",
        "initial_entry": "inherit",
        "add": "inherit",
        "reentry": "inherit",
        "strategic_exit": "automatic",
        "protective_exit": "automatic",
        "emergency_exit": "automatic",
    }


def _default_safety_supervisor() -> dict[str, Any]:
    return {
        "enabled_by_environment": {
            "replay": True,
            "backtest": True,
            "backtest_debug": True,
            "paper": True,
            "live": True,
        }
    }


def _effective_campaign_policy(run_plan: dict[str, Any]) -> dict[str, Any]:
    lifecycle = dict(
        run_plan.get("campaign_lifecycle")
        or run_plan.get("campaign_policy")
        or {}
    )
    authority = dict(run_plan.get("action_authority") or {})
    default = str(authority.get("default") or "confirm")

    def resolved(key: str, legacy_key: str, fallback: str) -> str:
        value = str(authority.get(key) or lifecycle.get(legacy_key) or "inherit")
        if value == "inherit":
            value = default
        return value or fallback

    return {
        **_default_campaign_policy(),
        **lifecycle,
        "default_action_authority": default,
        "initial_entry_authority": resolved(
            "initial_entry", "initial_entry_authority", "confirm"
        ),
        "add_authority": resolved("add", "add_authority", "confirm"),
        "reentry_authority": resolved("reentry", "reentry_authority", "confirm"),
        "exit_authority": resolved(
            "strategic_exit", "exit_authority", "automatic"
        ),
        "protective_exit_authority": "automatic",
        "emergency_exit_authority": "automatic",
    }


def _default_universe(runtime_assignments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "universe_id": "configured-watch-universe",
        "name": "Configured watch universe",
        "description": "Symbols explicitly approved for strategy evaluation.",
        "source": "configured_symbols",
        "symbols": sorted(
            {
                str(row.get("ticker") or "").upper()
                for row in runtime_assignments
                if str(row.get("ticker") or "").strip()
            }
        ),
        "scanner_view_id": "",
        "enabled": True,
    }


def _qmd_family_capabilities() -> list[dict[str, Any]]:
    """Project only the QMD-owned runtime catalog into configuration records.

    Saved configurations carry their own immutable capability rows and remain
    reviewable during a QMD outage. New/default configuration never invents a
    second backend-owned availability catalog when QMD authority is unavailable.
    """

    return _qmd_runtime_capabilities()


def _qmd_runtime_capabilities() -> list[dict[str, Any]]:
    """Project the QMD-owned runtime catalog into configuration UI records.

    QMD Gateway is the sole availability authority. A missing or invalid
    catalog produces no rows, so callers cannot mistake a backend snapshot for
    current QMD behavior.
    """

    global _QMD_RUNTIME_CATALOG_CACHE
    now = time.monotonic()
    expires_at, cached = _QMD_RUNTIME_CATALOG_CACHE
    if now < expires_at:
        return deepcopy(cached)
    try:
        catalogs = qmd_catalogs()
    except Exception:
        _QMD_RUNTIME_CATALOG_CACHE = (now + 5.0, [])
        return []
    capability_rows = [
        dict(row)
        for row in catalogs.get("capability_catalog") or []
        if isinstance(row, dict) and str(row.get("key") or "").strip()
    ]
    if not capability_rows:
        _QMD_RUNTIME_CATALOG_CACHE = (now + 5.0, [])
        return []
    indicators = {
        str(row.get("key") or ""): dict(row)
        for row in catalogs.get("indicator_catalog") or []
        if isinstance(row, dict)
    }
    signals = {
        str(row.get("key") or ""): dict(row)
        for row in catalogs.get("signal_catalog") or []
        if isinstance(row, dict)
    }
    tier_by_scope = {
        "universal_ingest": "universal",
        "core_scan": "core",
        "watchlist": "watchlist",
        "strategy_run": "strategy",
        "request": "request",
        "offline": "offline",
    }
    result: list[dict[str, Any]] = []
    for row in capability_rows:
        key = str(row["key"])
        kind = str(row.get("kind") or "")
        indicator = indicators.get(key, {})
        signal = signals.get(key, {})
        detail = indicator or signal
        scope = str(row.get("execution_scope") or "watchlist")
        policy = str(row.get("configuration_policy") or "generated")
        status = str(row.get("implementation_status") or "unknown")
        priority = str(
            detail.get("priority")
            or ("p0" if scope in {"universal_ingest", "core_scan"} else "p2")
        )
        timeframes = list(
            detail.get("typical_timeframes")
            or detail.get("working_timeframes")
            or []
        )
        capability_id = (
            f"qmd.primitive.{key.replace('_', '-')}"
            if kind == "primitive"
            else f"qmd.signal.{key}"
            if kind == "market_observation"
            else f"qmd.family.{key}"
        )
        category = str(detail.get("category") or "QMD runtime")
        capability_type = (
            "system"
            if kind == "primitive"
            else "signal"
            if kind == "market_observation"
            else "market_data"
            if category in {"candles", "core"} and key in {"core_bars", "quote_mid_spread_bars"}
            else "reference"
            if category in {"reference_context", "session"}
            else "indicator"
        )
        description = str(
            detail.get("rationale")
            or detail.get("input_basis")
            or f"QMD runtime capability {row.get('label') or key}."
        )
        system_required = policy == "locked"
        enabled = status in {"implemented", "reference_only"} and scope not in {"offline"}
        result.append({
            "capability_id": capability_id,
            "capability_key": key,
            "name": str(row.get("label") or key),
            "description": description,
            "calculation": description,
            "category": category.replace("_", " ").title(),
            "provider": "QMD",
            "owner": str(row.get("producer") or "qmd"),
            "output_type": "family",
            "capability_type": capability_type,
            "priority": priority,
            "availability": status,
            "inputs": list(row.get("inputs") or []),
            # QMD owns the concrete family outputs. Scanner projections such
            # as current last price and session volume are registered
            # separately below and must not replace interval-bar outputs.
            "fields": list(row.get("outputs") or []),
            "timeframes": timeframes,
            "selected_timeframes": timeframes,
            "enabled": enabled,
            "configurable": policy == "configurable" and status in {"implemented", "reference_only", "strategy_specific"},
            "system_required": system_required,
            "tier": tier_by_scope.get(scope, "watchlist"),
            "execution_scope": scope,
            "allowed_scopes": list(row.get("allowed_scopes") or []),
            "configuration_policy": policy,
            "implementation_status": status,
            "operational_status": str(row.get("operational_status") or "unknown"),
            "coverage_status": "runtime_catalog",
            "cost_class": str(row.get("cost_class") or "unknown"),
            "stateful": bool(row.get("stateful")),
            "implementation_version": int(row.get("implementation_version") or 1),
            "cadence": str(row.get("cadence") or "service_owned"),
            "warm_up_bars": (
                int(row["warm_up_bars"])
                if row.get("warm_up_bars") is not None
                else None
            ),
            "persistence_policy": str(row.get("persistence_policy") or "no_default"),
            "consumers": list(row.get("allowed_scopes") or []),
            "catalog_authority": "qmd_runtime_catalog",
        })
    _QMD_RUNTIME_CATALOG_CACHE = (now + 60.0, deepcopy(result))
    return result


def _universal_ingest_capabilities() -> list[dict[str, Any]]:
    """Locked primitives applied to every accepted market event.

    These are integrity and distribution responsibilities, not optional
    Scanner indicators. Keeping them explicit prevents expensive analytical
    families from being justified as part of universal ingestion.
    """

    rows = [
        (
            "event-validation-encoding",
            "Canonical event validation and encoding",
            "Validate source fields and encode the canonical compact event without changing source evidence.",
            ["Massive quote/trade event", "condition and exchange references"],
            ["canonical compact event", "rejection reason"],
        ),
        (
            "point-in-time-source-identity",
            "Point-in-time source identity",
            "Preserve the event's source ticker and identity interval needed for causal downstream resolution.",
            ["source ticker", "event timestamp", "identity intervals"],
            ["stable source identity", "identity validity evidence"],
        ),
        (
            "event-order-sequence",
            "Event ordering and sequencing",
            "Assign deterministic arrival/order evidence and retain bounded reordering state.",
            ["source sequence", "SIP timestamp", "arrival timestamp"],
            ["ordered event", "sequence gap state", "continuation cursor"],
        ),
        (
            "nbbo-trade-state",
            "Current NBBO and eligible-trade state",
            "Maintain only the current quote/trade state required by Core Scan, bars, and live consumers.",
            ["canonical quotes", "canonical trades", "aggregation rules"],
            ["current NBBO", "last eligible trade", "market state revision"],
        ),
        (
            "freshness-quality",
            "Freshness and market-data quality",
            "Track stale, crossed, locked, gap, halt, and source-quality state without inventing replacement values.",
            ["ordered canonical events", "market clock"],
            ["freshness", "quality flags", "degradation reason"],
        ),
        (
            "compact-persistence-fanout",
            "Compact persistence and bounded fanout",
            "Persist accepted compact events and publish bounded downstream notifications with explicit durability lag.",
            ["accepted compact event", "coverage checkpoint"],
            ["q_live event row", "coverage update", "live event notification"],
        ),
    ]
    return [
        {
            "capability_id": f"qmd.primitive.{key}",
            "name": name,
            "description": description,
            "calculation": description,
            "category": "Universal ingest primitives",
            "provider": "QMD",
            "output_type": "system",
            "capability_type": "system",
            "priority": "p0",
            "availability": "implemented",
            "inputs": inputs,
            "fields": fields,
            "timeframes": [],
            "selected_timeframes": [],
            "enabled": True,
            "configurable": False,
            "system_required": True,
            "tier": "universal",
            "execution_scope": "universal_ingest",
            "allowed_scopes": ["universal_ingest"],
            "configuration_policy": "locked",
            "cost_class": "minimal",
            "stateful": key in {"event-order-sequence", "nbbo-trade-state", "freshness-quality"},
        }
        for key, name, description, inputs, fields in rows
    ]


def _normalize_discovery_capability_contract(row: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(row)
    tier = str(normalized.get("tier") or "core")
    scope_by_tier = {
        "universal": "universal_ingest",
        "core": "core_scan",
        "watchlist": "watchlist",
        "strategy": "strategy_run",
        "request": "request",
        "offline": "offline",
    }
    execution_scope = str(normalized.get("execution_scope") or scope_by_tier.get(tier, "request"))
    allowed_by_scope = {
        "universal_ingest": ["universal_ingest"],
        "core_scan": ["core_scan", "watchlist", "signal_stream", "strategy_run", "request", "offline"],
        "watchlist": ["watchlist", "signal_stream", "strategy_run", "request", "offline"],
        "signal_stream": ["signal_stream", "strategy_run", "request", "offline"],
        "strategy_run": ["strategy_run", "request", "offline"],
        "request": ["request", "offline"],
        "offline": ["offline"],
    }
    availability = str(normalized.get("availability") or "implemented")
    normalized.update({
        "tier": tier,
        "execution_scope": execution_scope,
        "allowed_scopes": list(normalized.get("allowed_scopes") or allowed_by_scope[execution_scope]),
        "configuration_policy": str(
            normalized.get("configuration_policy")
            or (
                "locked"
                if bool(normalized.get("system_required"))
                else "configurable"
                if bool(normalized.get("configurable"))
                else "generated"
            )
        ),
        "implementation_status": availability,
        "operational_status": str(
            normalized.get("operational_status")
            or (
                "integration_pending"
                if availability == "integration_pending"
                else "planned"
                if availability == "planned_realtime"
                else "ready"
            )
        ),
        "coverage_status": str(normalized.get("coverage_status") or "unknown"),
        "cost_class": str(
            normalized.get("cost_class")
            or {"p0": "low", "p1": "medium", "p2": "high", "p3": "offline"}.get(
                str(normalized.get("priority") or "p2"), "high"
            )
        ),
        "stateful": bool(normalized.get("stateful", execution_scope != "offline")),
        "owner": str(normalized.get("owner") or normalized.get("provider") or "unknown"),
        "implementation_version": max(1, int(normalized.get("implementation_version") or 1)),
        "cadence": str(normalized.get("cadence") or "service_owned"),
        "persistence_policy": str(normalized.get("persistence_policy") or "not_registered"),
        "consumers": list(normalized.get("consumers") or normalized.get("allowed_scopes") or []),
    })
    return normalized


def _strategy_input_capability_type(source_id: str) -> str:
    """Classify strategy inputs by semantic authority, not provider name."""

    if source_id == "market.last_price":
        return "market_data"
    if source_id.startswith("market."):
        return "reference"
    if source_id == "indicator.structure.bullish_choch":
        return "signal"
    if source_id.startswith("signal."):
        return "signal"
    if source_id.startswith("indicator."):
        return "indicator"
    return "system"


def _pending_text_intelligence_label_capabilities() -> list[dict[str, Any]]:
    """Expose future causal label signals without claiming runtime support.

    TODO(text-intelligence-label-events): After the canonical News and SEC label
    event contracts are finalized, connect their validated event projections to
    StrategyObservation.news_labeled/sec_labeled, retain label provenance in
    source_signal_ids, and promote these rows to ``implemented``. Do not derive
    either flag from process health or from the mere arrival of source text.
    """

    rows = []
    fields = {field.field_id: field for field in FIELD_DEFINITIONS}
    for source_id, name, runtime_field, inputs, description in [
        (
            "signal.news_labeled",
            "News labeled",
            "news_labeled",
            ["causally available company-news event", "validated Text Intelligence news label"],
            "Turns true only on a news event for which Text Intelligence publishes a valid point-in-time label. Missing, failed, stale, or unavailable labeling remains false.",
        ),
        (
            "signal.sec_labeled",
            "SEC labeled",
            "sec_labeled",
            ["causally available SEC filing event", "validated Text Intelligence SEC label"],
            "Turns true only on an SEC event for which Text Intelligence publishes a valid point-in-time label. Missing, failed, stale, or unavailable labeling remains false.",
        ),
    ]:
        field = fields[source_id]
        rows.append({
            "capability_id": source_id,
            "name": name,
            "description": description,
            "category": "Text Intelligence labels",
            "provider": field.owner,
            "owner": field.owner,
            "source_path": field.source_path,
            "query_plan_id": field.query_plan_id,
            "available_at": field.available_at,
            "output_type": "boolean",
            "capability_type": "signal",
            "priority": "p1",
            "availability": field.status,
            "inputs": inputs,
            "fields": [runtime_field],
            "calculation": description,
            "timeframes": ["event"],
            "selected_timeframes": ["event"],
            "enabled": False,
            "configurable": False,
            "system_required": False,
            "tier": "watchlist",
        })
    return rows


def _discovery_reference_capabilities() -> list[dict[str, Any]]:
    """Point-in-time scanner fields used by reusable Watchlist templates."""

    rows = [
        ("market.change_actual", "Session price change", "market_data", "currency", "market.change_actual", "Last price minus the split-adjusted completed previous-session close.", "session_change.v1: QMD bars + Reference Gateway stock splits", ["session"]),
        ("market.change_pct", "Session change", "market_data", "percent", "market.change_pct", "Last price divided by the split-adjusted completed previous-session close, minus one, expressed as a percentage.", "session_change.v1: QMD bars + Reference Gateway stock splits", ["session"]),
        ("market.volume", "Session volume", "market_data", "shares", "market.volume", "Cumulative eligible trade size for the current session.", "QMD eligible trades", ["session"]),
        ("market.session_dollar_volume", "Session dollar volume", "market_data", "currency", "market.session_dollar_volume", "Cumulative eligible trade notional since the 04:00 New York session boundary.", "QMD eligible trades", ["session"]),
        ("market.relative_volume", "Relative volume", "indicator", "multiple", "market.relative_volume", "Current cumulative session volume divided by the point-in-time 20-session baseline for the same elapsed session interval.", "QMD volume + 20-session baseline", ["session"]),
        ("reference.market_cap", "Market capitalization", "reference", "currency", "reference.market_cap", "Latest point-in-time provider market capitalization available before evaluation.", "DB-managed market snapshot", ["1d"]),
        ("reference.float_shares", "Public float", "reference", "shares", "reference.float_shares", "Tradable share supply from DB-managed reference data, with the SEC public-float estimate available as a provenance-preserving fallback.", "DB reference + SEC facts", ["1d"]),
        ("reference.short_interest", "Short interest", "reference", "shares", "reference.short_interest", "Open short positions from the latest exchange settlement report published before evaluation.", "DB-managed short-interest history", ["settlement"]),
        ("reference.short_interest_pct", "Short interest of float", "reference", "percent", "reference.short_interest_pct", "Reported short interest divided by the point-in-time public float; unavailable denominators remain unavailable.", "Short interest + public float", ["settlement"]),
        ("reference.days_to_cover", "Days to cover", "reference", "days", "reference.days_to_cover", "Reported short interest divided by the reporting source's average daily volume.", "DB-managed short-interest history", ["settlement"]),
        ("fundamental.trajectory_score", "Fundamental trajectory", "reference", "score", "fundamental.trajectory_score", "Composite 0-100 trajectory score derived from causally available SEC profitability, cash generation, balance-sheet, growth, and share-base evidence.", "SEC XBRL fact service", ["filing"]),
        ("fundamental.quality_score", "Fundamental data quality", "reference", "score", "fundamental.quality_score", "0-100 coverage and comparability score for the SEC facts supporting the fundamental trajectory.", "SEC XBRL fact service", ["filing"]),
        ("fundamental.revenue_change", "Comparable revenue change", "reference", "currency", "fundamental.revenue_change", "Latest comparable revenue minus prior comparable revenue.", "SEC XBRL fact service", ["filing"]),
        ("fundamental.revenue_growth_pct", "Comparable revenue change %", "reference", "percent", "fundamental.revenue_growth_pct", "Comparable revenue change divided by the absolute prior-period revenue.", "SEC XBRL fact service", ["filing"]),
        ("fundamental.earnings_change", "Comparable earnings change", "reference", "currency", "fundamental.earnings_change", "Latest comparable net income minus prior comparable net income.", "SEC XBRL fact service", ["filing"]),
        ("fundamental.earnings_growth_pct", "Comparable earnings change %", "reference", "percent", "fundamental.earnings_growth_pct", "Comparable net-income change divided by the absolute prior-period net income.", "SEC XBRL fact service", ["filing"]),
        ("fundamental.share_change", "Comparable share-count change", "reference", "shares", "fundamental.share_change", "Latest comparable weighted-average basic shares minus prior comparable shares.", "SEC XBRL fact service", ["filing"]),
        ("fundamental.share_growth_pct", "Comparable share-count change %", "reference", "percent", "fundamental.share_growth_pct", "Comparable basic-share change divided by the absolute prior-period share count.", "SEC XBRL fact service", ["filing"]),
        ("event.ipo.days_to_event", "IPO event distance", "event", "days", "event.ipo.days_to_event", "Signed calendar days from evaluation to a point-in-time IPO event; negative values are recent IPOs and positive values are upcoming IPOs.", "DB-managed corporate-event calendar", ["event"]),
        ("event.split.days_to_event", "Split event distance", "event", "days", "event.split.days_to_event", "Signed calendar days from evaluation to the latest published stock-split execution date.", "DB-managed stock-split history", ["event"]),
    ]
    core_ids = {
        "market.change_actual",
        "market.change_pct",
        "market.volume",
        "reference.market_cap",
        "reference.float_shares",
    }
    required_ids = {"market.change_actual", "market.change_pct", "market.volume"}
    registered = {field.field_id: field for field in FIELD_DEFINITIONS}
    capabilities = []
    for capability_id, name, capability_type, output_type, field_id, calculation, provider, timeframes in rows:
        field = registered.get(field_id)
        availability = field.status if field is not None else "implemented"
        runnable = availability == "implemented"
        capabilities.append({
            "capability_id": capability_id,
            "name": name,
            "description": calculation,
            "category": "Scanner enrichment" if capability_id in core_ids else "Watchlist fields",
            "provider": field.owner if field is not None else provider,
            "owner": field.owner if field is not None else provider,
            "source_path": field.source_path if field is not None else "qmd://core-scanner",
            "query_plan_id": field.query_plan_id if field is not None else "qmd.runtime-capability-catalog",
            "available_at": field.available_at if field is not None else "qmd event/bar clock",
            "output_type": output_type,
            "capability_type": capability_type,
            "priority": "p0" if capability_id in required_ids else "p1",
            "availability": availability,
            "inputs": [field.source_path if field is not None else provider],
            "fields": [field_id],
            "calculation": calculation,
            "timeframes": timeframes,
            "selected_timeframes": timeframes,
            "enabled": runnable,
            "configurable": runnable and capability_id not in required_ids,
            "system_required": capability_id in required_ids,
            "tier": "core" if capability_id in core_ids else "watchlist",
        })
    return capabilities


def _market_discovery_classifications() -> list[dict[str, Any]]:
    """Reusable, non-overlapping classification definitions for Watchlist rules."""

    return [
        {"classification_id": "price.penny", "group": "Price", "name": "Penny Stocks", "description": "Last price is positive and below $1. This category is independent of market capitalization.", "minimum": 0, "maximum": 1, "unit": "usd", "source_id": "market.last_price"},
        {"classification_id": "market_cap.small", "group": "Market capitalization", "name": "Small Caps", "value": "Small Cap", "description": "Market capitalization is positive and below $2 billion. This consolidated bucket intentionally includes micro- and nano-cap issuers.", "minimum": 0, "maximum": 2_000_000_000, "unit": "usd", "source_id": "reference.market_cap"},
        {"classification_id": "market_cap.mid", "group": "Market capitalization", "name": "Mid Caps", "value": "Mid Cap", "description": "Market capitalization is at least $2 billion and below $10 billion.", "minimum": 2_000_000_000, "maximum": 10_000_000_000, "unit": "usd", "source_id": "reference.market_cap"},
        {"classification_id": "market_cap.large", "group": "Market capitalization", "name": "Large Caps", "value": "Large Cap", "description": "Market capitalization is at least $10 billion.", "minimum": 10_000_000_000, "maximum": None, "unit": "usd", "source_id": "reference.market_cap"},
        *[
            {"classification_id": identifier, "group": "Public float", "name": name, "description": description, "minimum": minimum, "maximum": maximum, "unit": "shares", "source_id": "reference.float_shares"}
            for identifier, name, description, minimum, maximum in [
                ("float.tiny", "Tiny", "Public float below 0.5 million shares.", 0, 500_000),
                ("float.extra_small", "Extra Small", "Public float from 0.5 million up to 2 million shares.", 500_000, 2_000_000),
                ("float.small", "Small", "Public float from 2 million up to 5 million shares.", 2_000_000, 5_000_000),
                ("float.medium", "Medium", "Public float from 5 million up to 10 million shares.", 5_000_000, 10_000_000),
                ("float.medium_plus", "Medium+", "Public float from 10 million up to 20 million shares.", 10_000_000, 20_000_000),
                ("float.large", "Large", "Public float from 20 million up to 50 million shares.", 20_000_000, 50_000_000),
                ("float.extra_large", "Extra Large", "Public float from 50 million up to 100 million shares.", 50_000_000, 100_000_000),
                ("float.broad", "Broad Float", "Public float of at least 100 million shares.", 100_000_000, None),
            ]
        ],
    ]


def _watchlist_condition(
    condition_id: str,
    source_id: str,
    comparator: str,
    value: float | bool | str,
    *,
    interval: str = "",
) -> dict[str, Any]:
    """Register a typed Watchlist operand, adding an interval only for bar fields."""

    condition = {
        "condition_id": condition_id,
        "left_source_id": source_id,
        "comparator": comparator,
        "right_source_id": "",
        "value": value,
        "enabled": True,
    }
    if interval:
        condition["left_interval"] = normalize_interval_spec(interval)
    return condition


def _watchlist_rule(
    rule_set_id: str,
    name: str,
    description: str,
    conditions: list[dict[str, Any]],
    *,
    operator: str = "all",
    enabled: bool = True,
    implementation_status: str = "implemented",
) -> dict[str, Any]:
    return {
        "rule_set_id": rule_set_id,
        "name": name,
        "description": description,
        "enabled": enabled,
        "operator": operator,
        "required_score": 1.0,
        "conditions": conditions,
        "scope": "watchlist",
        "origin": "system",
        "atomic": True,
        "editable": False,
        "protected": True,
        "revision": 1,
        "publication_status": "published",
        "implementation_status": implementation_status,
    }


def _normalize_data_rule_set_metadata(rule_set: dict[str, Any], *, atomic: bool) -> None:
    rule_set_id = str(rule_set.get("rule_set_id") or "rule-set")
    readable_name = " ".join(
        part.capitalize()
        for part in rule_set_id.replace("_", "-").split("-")
        if part
    )
    rule_set["name"] = str(rule_set.get("name") or readable_name or "Rule set")
    rule_set["description"] = str(
        rule_set.get("description")
        or f"Reusable data rule composed from {len(rule_set.get('conditions') or [])} registered condition(s)."
    )
    rule_set["scope"] = str(rule_set.get("scope") or "shared")
    rule_set["origin"] = "system" if atomic else str(rule_set.get("origin") or "user")
    rule_set["atomic"] = atomic
    rule_set["editable"] = not atomic
    rule_set["protected"] = atomic
    rule_set["revision"] = max(1, int(rule_set.get("revision") or 1))
    rule_set["publication_status"] = str(
        rule_set.get("publication_status") or ("published" if atomic else "draft")
    )


RULE_COMPARATOR_ALIASES = {
    "equal": "equals",
    "greater_than_or_equal": "greater_or_equal",
    "less_than_or_equal": "less_or_equal",
}
RULE_SET_COMPARATORS = {
    "above_by_bps",
    "equals",
    "greater_or_equal",
    "greater_than",
    "is_true",
    "less_or_equal",
    "less_than",
    "not_equals",
}


def _normalize_rule_set_conditions(rule_set: dict[str, Any]) -> None:
    for condition in rule_set.get("conditions") or []:
        comparator = str(condition.get("comparator") or "")
        condition["comparator"] = RULE_COMPARATOR_ALIASES.get(
            comparator, comparator
        )


def _default_watchlist_rule_sets() -> list[dict[str, Any]]:
    categories = [
        _watchlist_rule("watchlist-penny-stocks", "Sub-dollar price band", "Passes when last price is positive and below $1.", [_watchlist_condition("penny-positive", "market.last_price", "greater_than", 0), _watchlist_condition("penny-under-one", "market.last_price", "less_than", 1)]),
        _watchlist_rule("watchlist-small-caps", "Small-cap market value band", "Passes when market capitalization is positive and below $2 billion.", [_watchlist_condition("small-cap-positive", "reference.market_cap", "greater_than", 0), _watchlist_condition("small-cap-maximum", "reference.market_cap", "less_than", 2_000_000_000)]),
        _watchlist_rule("watchlist-mid-caps", "Mid-cap market value band", "Passes when market capitalization is at least $2 billion and below $10 billion.", [_watchlist_condition("mid-cap-minimum", "reference.market_cap", "greater_or_equal", 2_000_000_000), _watchlist_condition("mid-cap-maximum", "reference.market_cap", "less_than", 10_000_000_000)]),
        _watchlist_rule("watchlist-large-caps", "Large-cap market value band", "Passes when market capitalization is at least $10 billion.", [_watchlist_condition("large-cap-minimum", "reference.market_cap", "greater_or_equal", 10_000_000_000)]),
    ]
    float_rules = []
    for row in _market_discovery_classifications():
        if row["group"] != "Public float":
            continue
        conditions = [_watchlist_condition(f"{row['classification_id']}-minimum", "reference.float_shares", "greater_or_equal", row["minimum"])]
        if row["maximum"] is not None:
            conditions.append(_watchlist_condition(f"{row['classification_id']}-maximum", "reference.float_shares", "less_than", row["maximum"]))
        float_name = str(row["name"])
        if not float_name.endswith("Float"):
            float_name = f"{float_name} Float"
        float_rules.append(_watchlist_rule(f"watchlist-{row['classification_id'].replace('.', '-')}", float_name, row["description"], conditions))
    return [
        *categories,
        *float_rules,
        _watchlist_rule("watchlist-positive-gainer", "Positive session gainer", "Requires a positive percentage change from the split-adjusted completed previous-session close.", [_watchlist_condition("positive-session-change", "market.change_pct", "greater_than", 0)]),
        _watchlist_rule("watchlist-relative-volume-gainer", "Elevated relative volume", "Requires current volume to exceed the aligned 20-session baseline.", [_watchlist_condition("relative-volume-over-baseline", "market.relative_volume", "greater_than", 1)]),
        _watchlist_rule("watchlist-price-or-volume-squeeze", "Session Price or Volume Expansion", "Passes when session return from previous close reaches 5% or aligned 20-session relative volume reaches 3x.", [_watchlist_condition("squeeze-session-price", "market.change_pct", "greater_or_equal", 5), _watchlist_condition("squeeze-volume", "market.relative_volume", "greater_or_equal", 3)], operator="any"),
        _watchlist_rule(
            "watchlist-squeeze-early-impulse-100ms",
            "Bullish squeeze early impulse",
            "Earliest high-sensitivity candidate: the 100 ms close advances at least 0.05% from the prior 100 ms bar while both trade count and volume increase. Use in the all-market scanner, then confirm in an enriched Watchlist.",
            [
                _watchlist_condition("squeeze-early-price", "price_change_1_bar_pct", "greater_or_equal", 0.05, interval="100ms"),
                _watchlist_condition("squeeze-early-trades", "trade_count_change", "greater_than", 0, interval="100ms"),
                _watchlist_condition("squeeze-early-volume", "volume_change", "greater_than", 0, interval="100ms"),
            ],
        ),
        _watchlist_rule(
            "watchlist-squeeze-acceleration-1s",
            "Bullish squeeze acceleration",
            "Fast acceleration candidate: the 1-second close gains at least 0.20% while trade and share-volume rates are each at least 1.5 times their preceding 1-second bar.",
            [
                _watchlist_condition("squeeze-acceleration-price", "price_change_1_bar_pct", "greater_or_equal", 0.20, interval="1s"),
                _watchlist_condition("squeeze-acceleration-trades", "trade_rate_ratio", "greater_or_equal", 1.5, interval="1s"),
                _watchlist_condition("squeeze-acceleration-volume", "volume_rate_ratio", "greater_or_equal", 1.5, interval="1s"),
            ],
        ),
        _watchlist_rule(
            "watchlist-squeeze-confirmation-10s",
            "Bullish squeeze confirmation",
            "Lower-noise confirmation: the 10-second close gains at least 0.75% while trade count reaches 1.5 times and volume reaches 2 times their preceding 10-second bars.",
            [
                _watchlist_condition("squeeze-confirmation-price", "price_change_1_bar_pct", "greater_or_equal", 0.75, interval="10s"),
                _watchlist_condition("squeeze-confirmation-trades", "trade_count_ratio", "greater_or_equal", 1.5, interval="10s"),
                _watchlist_condition("squeeze-confirmation-volume", "volume_ratio", "greater_or_equal", 2.0, interval="10s"),
            ],
        ),
        _watchlist_rule(
            "watchlist-squeeze-buy-pressure-1s",
            "Bullish squeeze buy-pressure confirmation",
            "Enriched Watchlist confirmation: the 1-second close gains at least 0.10% and buyer-initiated volume exceeds seller-initiated volume in that bar.",
            [
                _watchlist_condition("squeeze-buy-pressure-price", "price_change_1_bar_pct", "greater_or_equal", 0.10, interval="1s"),
                _watchlist_condition("squeeze-buy-pressure-delta", "buy_sell_volume_delta", "greater_than", 0, interval="1s"),
            ],
        ),
        _watchlist_rule(
            "signal-price-squeeze-5m",
            "Exact 5% squeeze milestone",
            "Triggers when an active bullish squeeze episode reaches 5% above its event-time anchor. Five minutes is the episode expiry, not the measurement bar.",
            [
                _watchlist_condition(
                    "price-squeeze-move-from-anchor",
                    "signal.squeeze_move_pct",
                    "greater_or_equal",
                    5.0,
                ),
            ],
        ),
        _watchlist_rule(
            "signal-market-halt",
            "Trading halt opened",
            "Triggers once when QMD observes an active exchange halt condition for a security and rearms after trading resumes.",
            [_watchlist_condition("market-halt-active", "market.is_halted", "is_true", True)],
        ),
        _watchlist_rule(
            "strategy-squeeze-volume-spread-quality",
            "Squeeze volume and spread quality",
            "Latches liquidity admission for $2-$50 securities after at least $1,000,000 session dollar volume, 100,000 session shares, sustained one-minute and fast ten-second trade rates, and spread no wider than 60 basis points. Admission remains valid for the squeeze campaign; every order still requires a current fast trade rate and executable spread. The cross-sectional liquidity score is presentation-only.",
            [
                _watchlist_condition("squeeze-price-floor", "market.last_price", "greater_or_equal", 2.0),
                _watchlist_condition("squeeze-price-ceiling", "market.last_price", "less_or_equal", 50.0),
                _watchlist_condition("squeeze-session-dollar-volume", "market.session_dollar_volume", "greater_or_equal", 1_000_000.0),
                _watchlist_condition("squeeze-session-share-volume", "market.volume", "greater_or_equal", 100_000.0),
                _watchlist_condition("squeeze-trade-rate", "market.trade_rate_10s", "greater_or_equal", 1.0),
                _watchlist_condition("squeeze-sustained-trade-rate", "market.trade_rate_60s", "greater_or_equal", 0.5),
                _watchlist_condition(
                    "squeeze-spread-quality",
                    "market.spread_bps",
                    "less_or_equal",
                    60.0,
                ),
            ],
        ),
        _watchlist_rule(
            "strategy-squeeze-unified-resistance-break",
            "Unified structural resistance breakout",
            "Uses QMD's persistent event-native Unified Structural Level Book. A fresh causal resistance-zone break is latched for the watched campaign, then remains eligible while price holds above that accepted boundary.",
            [{
                **_watchlist_condition(
                    "squeeze-price-over-unified-resistance",
                    "market.last_price",
                    "above_by_bps",
                    0.0,
                    interval="1s",
                ),
                "right_source_id": "indicator.structure.unified_resistance_upper",
                "right_interval": normalize_interval_spec("1s"),
            }],
        ),
        _watchlist_rule(
            "strategy-squeeze-above-vwap-1s",
            "Price above causal one-second execution VWAP",
            "Requires the latest eligible trade price to be strictly above the causal session VWAP of volume-eligible trades inside a prevailing NBBO no more than one second old.",
            [{
                **_watchlist_condition(
                    "squeeze-price-above-vwap",
                    "market.last_price",
                    "greater_than",
                    0.0,
                    interval="1s",
                ),
                "right_source_id": "indicator.vwap.execution_value",
                "right_interval": normalize_interval_spec("1s"),
            }],
        ),
        _watchlist_rule(
            "strategy-squeeze-macd-open-1s",
            "Positive one-second MACD open",
            "Requires exactly: causal one-second MACD line above its signal line and MACD line above zero. The signal line may remain below zero.",
            [
                {
                    **_watchlist_condition(
                        "squeeze-macd-line-above-signal",
                        "indicator.macd.line",
                        "greater_than",
                        0.0,
                        interval="1s",
                    ),
                    "right_source_id": "indicator.macd.signal",
                    "right_interval": normalize_interval_spec("1s"),
                },
                _watchlist_condition(
                    "squeeze-macd-line-positive",
                    "indicator.macd.line",
                    "greater_than",
                    0.0,
                    interval="1s",
                ),
            ],
        ),
        _watchlist_rule(
            "strategy-live-spread-quality",
            "Executable spread quality",
            "Allows immediate event-driven entry only while the latest quoted spread is no wider than 60 basis points.",
            [_watchlist_condition("live-spread-quality", "market.spread_bps", "less_or_equal", 60.0)],
        ),
        _watchlist_rule("watchlist-vwap-breakout", "VWAP breakout", "Requires last price to trade at least 5 basis points above current executable VWAP.", [{**_watchlist_condition("vwap-breakout-price", "market.last_price", "above_by_bps", 5), "right_source_id": "indicator.vwap.execution_value"}]),
        _watchlist_rule("watchlist-news-bullish", "Bullish AI-reviewed news", "Requires a persisted issuer-specific AI review with forecast relevance and positive language implication.", [_watchlist_condition("news-ai-forecast-eligible", "news.llm.forecast_eligible", "is_true", True), _watchlist_condition("news-ai-positive-sentiment", "news.llm.language_sentiment", "equals", "positive")]),
        _watchlist_rule("watchlist-news-bearish", "Bearish AI-reviewed news", "Requires a persisted issuer-specific AI review with forecast relevance and negative language implication.", [_watchlist_condition("news-ai-forecast-eligible-negative", "news.llm.forecast_eligible", "is_true", True), _watchlist_condition("news-ai-negative-sentiment", "news.llm.language_sentiment", "equals", "negative")]),
        _watchlist_rule("signal-news-synthesis-deepfm-bullish", "Bullish Synthesis + DeepFM", "Requires positive issuer direction from News Synthesis and an eligible decision from the promoted DeepFM release for the same canonical news event.", [_watchlist_condition("news-synthesis-positive-direction", "news.composite_sentiment", "equals", "positive"), _watchlist_condition("news-synthesis-deepfm-eligible", "news.deepfm.forecast_eligible", "is_true", True)]),
        _watchlist_rule("watchlist-sec-bullish", "Bullish SEC sentiment", "Requires a validated SEC label and a positive filing score of at least 0.35.", [_watchlist_condition("sec-labeled-positive", "signal.sec_labeled", "is_true", True), _watchlist_condition("sec-positive-score", "signal.sec_filing.score", "greater_or_equal", 0.35)], enabled=False, implementation_status="integration_pending"),
        _watchlist_rule("watchlist-sec-bearish", "Bearish SEC sentiment", "Requires a validated SEC label and a negative filing score of -0.35 or lower.", [_watchlist_condition("sec-labeled-negative", "signal.sec_labeled", "is_true", True), _watchlist_condition("sec-negative-score", "signal.sec_filing.score", "less_or_equal", -0.35)], enabled=False, implementation_status="integration_pending"),
        _watchlist_rule("watchlist-fundamental-bullish", "Fundamental Bullish", "Requires reliable SEC evidence and a trajectory score of at least 65.", [_watchlist_condition("fundamental-bull-quality", "fundamental.quality_score", "greater_or_equal", 60), _watchlist_condition("fundamental-bull-score", "fundamental.trajectory_score", "greater_or_equal", 65)]),
        _watchlist_rule("watchlist-fundamental-bearish", "Fundamental Bearish", "Requires reliable SEC evidence and a trajectory score of 35 or lower.", [_watchlist_condition("fundamental-bear-quality", "fundamental.quality_score", "greater_or_equal", 60), _watchlist_condition("fundamental-bear-score", "fundamental.trajectory_score", "less_or_equal", 35)]),
        _watchlist_rule("watchlist-ipo-window", "Past or Upcoming IPO", "Retains IPOs from 30 days before through 90 days after their event date.", [_watchlist_condition("ipo-window-start", "event.ipo.days_to_event", "greater_or_equal", -90), _watchlist_condition("ipo-window-end", "event.ipo.days_to_event", "less_or_equal", 30)]),
        _watchlist_rule("watchlist-split-window", "Stock split window", "Retains symbols from 10 days before through 5 days after a published split execution date.", [_watchlist_condition("split-window-start", "event.split.days_to_event", "greater_or_equal", -5), _watchlist_condition("split-window-end", "event.split.days_to_event", "less_or_equal", 10)]),
    ]


def _producer_output_filter_operators(output_type: str) -> list[str]:
    normalized = output_type.lower()
    if normalized == "boolean":
        return ["is_true", "equals"]
    if normalized in {"number", "integer", "float", "score", "ratio", "percent", "price", "bps_per_second"}:
        return ["greater_than", "greater_or_equal", "less_than", "less_or_equal", "equals", "not_equals"]
    return ["equals", "not_equals"]


def _market_discovery_field_catalog(
    calculation_rows: list[dict[str, Any]],
    classifications: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve one registry-owned field catalog for columns and filters."""

    registered = {field.field_id: field for field in FIELD_DEFINITIONS}
    capabilities = {
        str(row.get("capability_id") or ""): row for row in calculation_rows
    }
    presented_field_ids = {
        row.field_id for row in DISCOVERY_FIELD_PRESENTATIONS if row.field_id
    }
    classification_values = {
        "classification.market_cap": [
            {"value": str(row.get("value") or row.get("name") or ""), "label": str(row.get("name") or ""), "description": str(row.get("description") or "")}
            for row in classifications if str(row.get("classification_id") or "").startswith("market_cap.")
        ],
        "classification.float": [
            {"value": str(row.get("value") or row.get("name") or ""), "label": str(row.get("name") or ""), "description": str(row.get("description") or "")}
            for row in classifications if str(row.get("classification_id") or "").startswith("float.")
        ],
    }
    rows: list[dict[str, Any]] = []
    for presentation in DISCOVERY_FIELD_PRESENTATIONS:
        field = registered.get(presentation.field_id)
        capability = capabilities.get(presentation.source_id, {})
        implementation_status = str(
            field.status
            if field is not None
            else capability.get("implementation_status")
            or capability.get("availability")
            or "integration_pending"
        )
        rows.append({
            "source_id": presentation.source_id,
            "field_id": presentation.field_id,
            "column_id": presentation.column_id,
            "name": presentation.label,
            "description": presentation.description,
            "semantic_type": presentation.semantic_type,
            "source": str(
                field.owner
                if field is not None
                else capability.get("owner") or capability.get("provider") or "QMD"
            ),
            "source_path": str(
                field.source_path
                if field is not None
                else capability.get("source_path") or "qmd://runtime-capability"
            ),
            "query_plan_id": str(
                field.query_plan_id
                if field is not None
                else capability.get("query_plan_id") or "qmd.runtime-capability-catalog"
            ),
            "available_at": str(
                field.available_at
                if field is not None
                else capability.get("available_at") or "QMD publication clock"
            ),
            "provenance": str(field.provenance if field is not None else "qmd"),
            "source_columns": list(field.source_columns if field is not None else ()),
            "source_summary": str(field.source_summary if field is not None else capability.get("provider") or "QMD runtime output."),
            "calculation_summary": str(field.calculation_summary if field is not None else capability.get("calculation") or presentation.description),
            "formula": str((DERIVED_FIELD_METHODS.get(field.field_id) or TEMPORAL_DERIVED_METHODS.get(field.field_id, ("", ()))[0]) if field is not None else ""),
            "input_field_ids": list(field.input_field_ids if field is not None else capability.get("inputs") or []),
            "known_values": (classification_values.get(presentation.source_id) or [
                {"value": value, "label": label, "description": description}
                for value, label, description in field.known_values
            ] if field is not None else []),
            "value_type": str(
                field.value_type
                if field is not None
                else capability.get("output_type") or "number"
            ),
            "presentation_value_type": str(field.presentation_value_type if field is not None else _presentation_value_type(presentation.field_id, str(capability.get("output_type") or "number"), str(capability.get("output_type") or "scalar"))),
            "unit": str(field.unit if field is not None else capability.get("output_type") or "scalar"),
            "default_visible": presentation.default_visible,
            "filterable": presentation.filterable,
            "sortable": presentation.sortable,
            "filter_operators": list(presentation.filter_operators),
            "timeframes": list(presentation.timeframes),
            "implementation_status": implementation_status,
            "registry_authority": "application_registry",
            "market_discovery_supported": True,
            "interval_semantics": str(field.interval_semantics if field is not None else ""),
            "aggregation_functions": list(field.aggregation_functions if field is not None else ()),
            "default_aggregation": str(field.default_aggregation if field is not None else ""),
            "intrinsic_aggregation": str(field.intrinsic_aggregation if field is not None else ""),
            "aggregation_runtime_fields": dict(field.aggregation_runtime_fields if field is not None else ()),
        })
    for field in sorted(FIELD_DEFINITIONS, key=lambda row: row.field_id):
        if field.field_id in presented_field_ids:
            continue
        bar_gpt_field = field.field_id.startswith("model.bargpt.")
        rows.append({
            "source_id": field.field_id,
            "field_id": field.field_id,
            "column_id": field.field_id if bar_gpt_field else "",
            "name": field.label,
            "description": field.calculation_summary,
            "semantic_type": "model" if bar_gpt_field else "reference",
            "source": field.owner,
            "source_path": field.source_path,
            "query_plan_id": field.query_plan_id,
            "available_at": field.available_at,
            "provenance": field.provenance,
            "source_columns": list(field.source_columns),
            "source_summary": field.source_summary,
            "calculation_summary": field.calculation_summary,
            "formula": str(DERIVED_FIELD_METHODS.get(field.field_id) or TEMPORAL_DERIVED_METHODS.get(field.field_id, ("", ()))[0]),
            "input_field_ids": list(field.input_field_ids),
            "known_values": [
                {"value": value, "label": label, "description": description}
                for value, label, description in field.known_values
            ],
            "value_type": field.value_type,
            "presentation_value_type": field.presentation_value_type,
            "unit": field.unit,
            "default_visible": False,
            "filterable": bar_gpt_field,
            "sortable": bar_gpt_field,
            "filter_operators": _producer_output_filter_operators(field.value_type) if bar_gpt_field else [],
            "timeframes": list(field.timeframes),
            "implementation_status": field.status,
            "registry_authority": "application_registry",
            "market_discovery_supported": bar_gpt_field,
            "interval_semantics": field.interval_semantics,
            "aggregation_functions": list(field.aggregation_functions),
            "default_aggregation": field.default_aggregation,
            "intrinsic_aggregation": field.intrinsic_aggregation,
            "aggregation_runtime_fields": dict(field.aggregation_runtime_fields),
        })
    # Some typed QMD outputs (for example confirmed structure levels) are
    # registered directly by the producer capability rather than the static
    # source-field registry.  Keep those outputs available to Rule Sets, while
    # excluding operational/system capabilities that are not Data Fields.
    known_source_ids = {str(row["source_id"]) for row in rows}
    for capability_id, capability in sorted(capabilities.items()):
        capability_type = str(capability.get("capability_type") or "").lower()
        output_type = str(capability.get("output_type") or "").lower()
        if (
            not capability_id
            or capability_id in known_source_ids
            or capability_type == "system"
            or output_type == "system"
        ):
            continue
        fields = [str(value) for value in capability.get("fields") or [] if str(value)]
        # A family capability describes a computation bundle, not each output's
        # type/name contract. Only promote a typed capability whose identity is
        # itself one of its declared producer outputs.
        if capability_id.startswith("qmd.") or capability_id not in fields:
            continue
        source_ids = [capability_id]
        for source_id in source_ids:
            if source_id in known_source_ids:
                continue
            known_source_ids.add(source_id)
            rows.append({
                "source_id": source_id,
                "field_id": "",
                "column_id": "",
                "name": str(capability.get("name") or source_id),
                "description": str(capability.get("calculation") or capability.get("description") or "Registered QMD producer output."),
                "semantic_type": capability_type or "qmd_output",
                "source": str(capability.get("owner") or capability.get("provider") or "qmd"),
                "source_path": str(capability.get("source_path") or "qmd://registered-output"),
                "query_plan_id": str(capability.get("query_plan_id") or "qmd.scanner.snapshot.v1"),
                "available_at": str(capability.get("available_at") or "QMD publication clock"),
                "provenance": "derived",
                "source_columns": list(capability.get("inputs") or []),
                "source_summary": "Published by the registered QMD producer capability.",
                "calculation_summary": str(capability.get("calculation") or capability.get("description") or "Registered QMD calculation."),
                "formula": "",
                "input_field_ids": list(capability.get("inputs") or []),
                "known_values": [],
                "value_type": output_type or "number",
                "presentation_value_type": _presentation_value_type(source_id, output_type or "number", output_type or "scalar"),
                "unit": output_type or "scalar",
                "default_visible": False,
                "filterable": True,
                "sortable": True,
                "filter_operators": _producer_output_filter_operators(output_type or "number"),
                "timeframes": list(capability.get("timeframes") or []),
                "implementation_status": str(capability.get("implementation_status") or capability.get("availability") or "unknown"),
                "registry_authority": str(capability.get("catalog_authority") or "qmd_capability_registry"),
                "market_discovery_supported": True,
            })
    return rows


def _watchlist_column_catalog(
    field_catalog: list[dict[str, Any]],
    rule_sets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build presentation-only columns from canonical data and rule definitions."""

    columns = [
        {
            **deepcopy(row),
            "source_kind": "data_definition",
            "source_id": str(row.get("source_id") or row.get("field_id") or ""),
        }
        for row in field_catalog
        if str(row.get("column_id") or "")
    ]
    for rule_set in rule_sets:
        rule_set_id = str(rule_set.get("rule_set_id") or "")
        if not rule_set_id:
            continue
        columns.append({
            "column_id": f"rule_set:{rule_set_id}",
            "field_id": "",
            "source_kind": "rule_set",
            "source_id": rule_set_id,
            "name": str(rule_set.get("name") or rule_set_id),
            "description": str(rule_set.get("description") or "Boolean rule-set result."),
            "value_type": "boolean",
            "presentation_value_type": "boolean",
            "unit": "boolean",
            "default_visible": False,
            "filterable": True,
            "filter_operators": ["equals", "is_true"],
            "sortable": True,
            "source": "rule_set_registry",
            "source_path": f"rule_set.{rule_set_id}",
            "query_plan_id": "qmd.scanner.rule_projection.v1",
            "provenance": "derived",
            "available_at": "candidate evaluation clock",
            "implementation_status": "implemented",
            "registry_authority": "application_information_registry",
            "semantic_type": "rule_set",
            "timeframes": ["evaluation"],
        })
    return columns


def _bind_discovery_scanner_columns(
    calculation_rows: list[dict[str, Any]],
    field_catalog: list[dict[str, Any]],
) -> None:
    """Bind QMD capability outputs to the registered scanner presentation.

    Capability rows declare semantic outputs. The field registry owns how those
    outputs are presented, so frontend consumers never infer columns from names
    or maintain a second scanner catalog.
    """

    columns_by_source = {
        str(row.get("source_id") or ""): row
        for row in field_catalog
        if str(row.get("column_id") or "")
    }
    for capability in calculation_rows:
        if str(capability.get("execution_scope") or "") == "core_scan":
            capability["consumers"] = list(dict.fromkeys([
                *(str(value) for value in capability.get("consumers") or [] if str(value)),
                "core_scan",
                "watchlist",
            ]))
        sources = [
            str(value)
            for value in capability.get("fields") or []
            if str(value)
        ]
        capability_id = str(capability.get("capability_id") or "")
        capability_key = str(capability.get("capability_key") or "")
        # A QMD family keeps its producer-owned outputs (for example interval
        # OHLCV) while separately declaring which stable scanner projections
        # it supports.  Do not replace the family outputs with these columns:
        # that was the source of fake last-price-per-interval Data Fields.
        sources.extend(QMD_CORE_SCANNER_FIELDS.get(capability_key, []))
        primary_source = QMD_CORE_PRIMARY_SCANNER_FIELD.get(capability_id)
        if primary_source:
            sources = [primary_source]
        if capability_id:
            sources.append(capability_id)
        seen: set[str] = set()
        scanner_columns: list[dict[str, str]] = []
        for source_id in sources:
            presentation = columns_by_source.get(source_id)
            column_id = str((presentation or {}).get("column_id") or "")
            if not column_id or column_id in seen:
                continue
            seen.add(column_id)
            scanner_columns.append({
                "column_id": column_id,
                "name": str(presentation.get("name") or column_id),
                "source_id": source_id,
            })
        capability["scanner_columns"] = scanner_columns


def _default_watchlist_templates(symbols: list[str], calculation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    common_columns = ["symbol", "company_name", "last_price", "change_pct", "volume", "session_dollar_volume", "trade_rate_10s", "liquidity_score", "liquidity_rank", "relative_volume", "market_cap", "market_cap_category", "float_shares", "float_category", "short_interest_pct"]
    def template(identifier: str, name: str, description: str, rules: list[str], ranking: str, *, direction: str = "descending", refresh: int = 1000, enabled: bool = True, columns: list[str] | None = None, availability: str = "available", availability_detail: str = "") -> dict[str, Any]:
        return {"watchlist_id": identifier, "name": name, "description": description, "enabled": enabled, "origin": "system", "template": True, "availability": availability, "availability_detail": availability_detail, "source_scan_id": "qmd-core-scan", "inclusion_rule_sets": rules, "inclusion_operator": "all", "exclusion_rule_sets": [], "ranking_field": ranking, "ranking_direction": direction, "maximum_size": 10, "refresh_interval_ms": refresh, "membership_expiry": "end_of_trading_day", "membership_ttl_ms": 300000, "manual_inclusions": [], "manual_exclusions": [], "columns": columns or common_columns, "membership_history": []}
    gainers = []
    for slug, label, category_rule in [("penny", "Penny Stock", "watchlist-penny-stocks"), ("small-cap", "Small Cap", "watchlist-small-caps"), ("mid-cap", "Mid Cap", "watchlist-mid-caps"), ("large-cap", "Large Cap", "watchlist-large-caps")]:
        gainers.append(template(f"top-{slug}-gainers", f"Top {label} Gainers", f"Top positive session performers in the {label.lower()} category, ranked by percentage change.", [category_rule, "watchlist-positive-gainer"], "market.change_pct"))
        gainers.append(template(f"top-{slug}-volume-gainers", f"Top {label} Volume Gainers", f"Most unusually active {label.lower()} instruments, ranked by aligned relative volume.", [category_rule, "watchlist-relative-volume-gainer"], "market.relative_volume"))
    return [
        {"watchlist_id": "core-candidates", "name": "Core candidates", "description": "Candidate instruments produced from the Core Scan for strategy evaluation.", "enabled": True, "origin": "system", "template": False, "availability": "available", "availability_detail": "", "source_scan_id": "qmd-core-scan", "inclusion_rule_sets": [], "inclusion_operator": "all", "exclusion_rule_sets": [], "ranking_field": "market.liquidity_rank", "ranking_direction": "ascending", "maximum_size": 250, "refresh_interval_ms": 1000, "membership_expiry": "end_of_trading_day", "membership_ttl_ms": 300000, "manual_inclusions": symbols, "manual_exclusions": [], "columns": common_columns, "membership_history": []},
        template("squeeze-tradable-candidates", "Squeeze tradable candidates", "Current $2-$50 Early Squeeze candidates whose absolute session activity, sustained trade rates, and executable spread pass campaign admission. Membership may change while the Early Squeeze episode remains under Strategy observation.", ["strategy-squeeze-volume-spread-quality"], "market.liquidity_score", refresh=1000, columns=common_columns),
        *gainers,
        template("price-or-volume-squeeze", "Session Price or Volume Expansion", "Symbols with at least 5% session price expansion or 3x aligned 20-session relative volume.", ["watchlist-price-or-volume-squeeze"], "market.relative_volume"),
        template("vwap-breakout", "VWAP Breakout", "Symbols trading at least 5 basis points above causal session VWAP.", ["watchlist-vwap-breakout"], "market.change_pct"),
        template("news-bullish-sentiment", "AI Bullish News", "Issuers with a persisted forecast-relevant positive AI review.", ["watchlist-news-bullish"], "news.llm.positive_implication_probability", refresh=1000, columns=[*common_columns, "news_llm_forecast_probability", "news_llm_positive_probability", "news_llm_sentiment", "news_published_at"]),
        template("news-bearish-sentiment", "AI Bearish News", "Issuers with a persisted forecast-relevant negative AI review.", ["watchlist-news-bearish"], "news.llm.negative_implication_probability", direction="descending", refresh=1000, columns=[*common_columns, "news_llm_forecast_probability", "news_llm_negative_probability", "news_llm_sentiment", "news_published_at"]),
        template("sec-bullish-sentiment", "SEC Bullish Sentiment", "New SEC filing events with a validated positive Text Intelligence label.", ["watchlist-sec-bullish"], "signal.sec_filing.score", refresh=5000, enabled=False, columns=[*common_columns, "sec_sentiment"], availability="integration_pending", availability_detail="Requires validated Text Intelligence SEC-label events."),
        template("sec-bearish-sentiment", "SEC Bearish Sentiment", "New SEC filing events with a validated negative Text Intelligence label.", ["watchlist-sec-bearish"], "signal.sec_filing.score", direction="ascending", refresh=5000, enabled=False, columns=[*common_columns, "sec_sentiment"], availability="integration_pending", availability_detail="Requires validated Text Intelligence SEC-label events."),
        template("fundamental-bullish", "Fundamental Bullish", "Issuers with reliable SEC evidence and a financial trajectory score of at least 65.", ["watchlist-fundamental-bullish"], "fundamental.trajectory_score", refresh=60_000, columns=[*common_columns, "fundamental_trajectory", "fundamental_quality"]),
        template("fundamental-bearish", "Fundamental Bearish", "Issuers with reliable SEC evidence and a financial trajectory score of 35 or lower.", ["watchlist-fundamental-bearish"], "fundamental.trajectory_score", direction="ascending", refresh=60_000, columns=[*common_columns, "fundamental_trajectory", "fundamental_quality"]),
        template("past-upcoming-ipos", "Past and Upcoming IPOs", "IPOs from 30 days before through 90 days after the event date.", ["watchlist-ipo-window"], "event.ipo.days_to_event", refresh=60_000, columns=[*common_columns, "ipo_event"]),
        template("stock-splits", "Stock Splits", "Published stock splits from 10 days before through 5 days after execution.", ["watchlist-split-window"], "event.split.days_to_event", refresh=60_000, columns=[*common_columns, "split_event"]),
    ]


def _default_signal_streams(
    column_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Register practical compositions without making QMD signals order authorities."""

    columns_by_source = {
        str(row.get("source_id") or ""): str(row.get("column_id") or "")
        for row in column_catalog
        if str(row.get("source_id") or "") and str(row.get("column_id") or "")
    }
    context_sources = [
        "classification.float",
        "classification.short_pressure",
        "reference.short_interest",
        "reference.short_interest_pct",
        "reference.days_to_cover",
        "reference.short_volume",
        "reference.short_volume_pct",
        "market.liquidity_rank",
        "market.liquidity_score",
    ]

    def columns_for_sources(sources: list[str]) -> list[str]:
        return list(dict.fromkeys(
            columns_by_source[source_id]
            for source_id in [*sources, *context_sources]
            if source_id in columns_by_source
        ))

    evidence_sources = [
        "identity.symbol",
        "identity.company_name",
        "market.last_price",
        "market.change_pct",
        "market.relative_volume",
        "quote.bid_price",
        "quote.ask_price",
        "signal.squeeze_move_pct",
        "signal.squeeze_anchor_price",
        "signal.squeeze_high_water_pct",
        "signal.squeeze_episode_expires_at",
        "reference.float_shares",
        "reference.market_cap",
        "trade_count_change",
        "volume_change",
        "buy_sell_volume_delta",
        "depth_imbalance_proxy",
        "volume_rate_ratio",
        "market.spread_bps",
        "market.volume",
        "market.session_phase",
    ]
    columns = columns_for_sources(evidence_sources)
    intervals = {
        columns_by_source[source_id]: normalize_interval_spec(interval)
        for source_id, interval in {
            # Quote evidence is event-derived. Bind it at the fastest durable
            # QMD interval so the frozen occurrence records the actionable
            # spread rather than an unrelated slower bar.
            "quote.bid_price": "100ms",
            "quote.ask_price": "100ms",
            "trade_count_change": "100ms",
            "volume_change": "100ms",
            "buy_sell_volume_delta": "100ms",
            "depth_imbalance_proxy": "100ms",
            "volume_rate_ratio": "1s",
        }.items()
        if source_id in columns_by_source
    }
    news_sources = [
        "identity.symbol",
        "market.last_price",
        "quote.bid_price",
        "quote.ask_price",
        "market.spread_bps",
        "market.session_phase",
        "news.deepfm.eligible_probability",
        "news.deepfm.forecast_eligible",
        "news.llm.forecast_relevance_probability",
        "news.llm.forecast_eligible",
        "news.llm.positive_implication_probability",
        "news.llm.negative_implication_probability",
        "news.llm.language_sentiment",
        "news.canonical_news_id",
        "news.published_at",
    ]
    news_columns = columns_for_sources(news_sources)
    synthesis_deepfm_sources = [
        "identity.symbol",
        "market.last_price",
        "quote.bid_price",
        "quote.ask_price",
        "market.spread_bps",
        "market.session_phase",
        "news.composite_sentiment",
        "news.deepfm.eligible_probability",
        "news.deepfm.forecast_eligible",
        "news.deepfm.status",
        "news.canonical_news_id",
        "news.published_at",
    ]
    synthesis_deepfm_columns = columns_for_sources(synthesis_deepfm_sources)
    halt_sources = [
        "identity.symbol",
        "market.last_price",
        "quote.bid_price",
        "quote.ask_price",
        "market.spread_bps",
        "price_change_5_bar_pct",
        "market.halt_direction",
        "market.halt_category",
        "market.event_at",
        "clock.session_phase",
    ]
    halt_columns = columns_for_sources(halt_sources)
    return [
        {
            "signal_stream_id": "price-squeeze-early",
            "revision": 1,
            "name": "Early Squeeze Move",
            "description": "Emits on the first qualifying 100 ms bullish price, trade-count, and volume impulse. The event opens a five-minute move episode immediately; five minutes is only the expiry window.",
            "enabled": True,
            "origin": "system",
            "protected": True,
            "source_type": "core_scan",
            "source_id": "qmd-core-scan",
            "source_scan_id": "qmd-core-scan",
            "occurrence_source": "qmd_squeeze_episode",
            "episode_role": "start",
            "episode_ttl_ms": 300000,
            "inclusion_rule_sets": ["watchlist-squeeze-early-impulse-100ms"],
            "inclusion_operator": "all",
            "columns": columns,
            "column_intervals": intervals,
            "refresh_interval_ms": 100,
            "trigger_policy": "false_to_true",
            "rearm_policy": "after_false",
            "cooldown_ms": 0,
            "maximum_events": 5000,
            "watchlist_routes": [],
        },
        {
            "signal_stream_id": "price-squeeze-5m",
            "revision": 2,
            "name": "Exact 5% Squeeze",
            "description": "Emits at the first event-time observation at or above 5% from the active move anchor. It never waits for a five-minute bar; the five-minute value only expires the episode.",
            "enabled": True,
            "origin": "system",
            "protected": True,
            "source_type": "core_scan",
            "source_id": "qmd-core-scan",
            "source_scan_id": "qmd-core-scan",
            "occurrence_source": "qmd_squeeze_episode",
            "episode_role": "milestone",
            "episode_ttl_ms": 300000,
            "inclusion_rule_sets": ["signal-price-squeeze-5m"],
            "inclusion_operator": "all",
            "columns": columns,
            "column_intervals": intervals,
            "refresh_interval_ms": 100,
            "trigger_policy": "false_to_true",
            "rearm_policy": "after_false",
            "cooldown_ms": 0,
            "maximum_events": 5000,
            "watchlist_routes": [],
        },
        {
            "signal_stream_id": "market-halts",
            "revision": 2,
            "name": "Trading Halts",
            "description": "Append-only occurrences emitted when an exchange halt opens and whenever its quote state changes. The stream rearms after QMD observes the resume condition.",
            "enabled": True,
            "origin": "system",
            "protected": True,
            "source_type": "core_scan",
            "source_id": "qmd-core-scan",
            "source_scan_id": "qmd-core-scan",
            "occurrence_source": "qmd_live_market_state",
            "inclusion_rule_sets": ["signal-market-halt"],
            "inclusion_operator": "all",
            "columns": halt_columns,
            "column_labels": {
                columns_by_source["price_change_5_bar_pct"]: "Last 5 min"
            } if "price_change_5_bar_pct" in columns_by_source else {},
            "column_intervals": {
                columns_by_source[source_id]: normalize_interval_spec(
                    "1m" if source_id == "price_change_5_bar_pct" else "100ms"
                )
                for source_id in ("quote.bid_price", "quote.ask_price", "price_change_5_bar_pct")
                if source_id in columns_by_source
            },
            "refresh_interval_ms": 250,
            "trigger_policy": "false_to_true",
            "rearm_policy": "after_false",
            "cooldown_ms": 0,
            "maximum_events": 5000,
            "watchlist_routes": [],
        },
        {
            "signal_stream_id": "bullish-news-v1",
            "revision": 1,
            "name": "Bullish AI-reviewed News",
            "description": "Append-only issuer occurrences emitted when a persisted manual or automatic AI review becomes forecast-relevant and positive. Availability time is frozen without backdating to publication.",
            "enabled": True,
            "origin": "system",
            "protected": True,
            "source_type": "news_events",
            "source_id": "q_live.news_intelligence_events_v1",
            "source_scan_id": "qmd-core-scan",
            "inclusion_rule_sets": ["watchlist-news-bullish"],
            "inclusion_operator": "all",
            "columns": news_columns,
            "column_intervals": {
                columns_by_source[source_id]: normalize_interval_spec("100ms")
                for source_id in ("quote.bid_price", "quote.ask_price")
                if source_id in columns_by_source
            },
            "refresh_interval_ms": 1000,
            "trigger_policy": "false_to_true",
            "rearm_policy": "after_false",
            "cooldown_ms": 0,
            "maximum_events": 5000,
            "watchlist_routes": [],
        },
        {
            "signal_stream_id": "bullish-synthesis-deepfm-news-v1",
            "revision": 1,
            "name": "Bullish Synthesis + DeepFM News",
            "description": "Append-only issuer occurrences emitted only when News Synthesis direction is positive and the promoted DeepFM release marks the same canonical article forecast-eligible. DeepFM remains the eligibility authority.",
            "enabled": True,
            "origin": "system",
            "protected": True,
            "source_type": "news_events",
            "source_id": "q_live.news_intelligence_events_v1",
            "source_scan_id": "qmd-core-scan",
            "inclusion_rule_sets": ["signal-news-synthesis-deepfm-bullish"],
            "inclusion_operator": "all",
            "columns": synthesis_deepfm_columns,
            "column_intervals": {
                columns_by_source[source_id]: normalize_interval_spec("100ms")
                for source_id in ("quote.bid_price", "quote.ask_price")
                if source_id in columns_by_source
            },
            "refresh_interval_ms": 1000,
            "trigger_policy": "false_to_true",
            "rearm_policy": "after_false",
            "cooldown_ms": 0,
            "maximum_events": 5000,
            "watchlist_routes": [],
        },
    ]


def _default_market_discovery(
    runtime_assignments: list[dict[str, Any]],
    rule_sets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """QMD-owned discovery configuration exposed without moving calculations into Strategy."""

    qmd_rows = _qmd_family_capabilities()
    if not any(row.get("catalog_authority") == "qmd_runtime_catalog" for row in qmd_rows):
        qmd_rows = [*_universal_ingest_capabilities(), *qmd_rows]
    calculation_rows: list[dict[str, Any]] = [
        *qmd_rows,
        *_pending_text_intelligence_label_capabilities(),
        *_discovery_reference_capabilities(),
    ]
    seen: set[str] = {str(row["capability_id"]) for row in calculation_rows}
    for source in installed_strategy_input_catalog():
        capability_id = str(source.get("source_id") or "")
        if not capability_id or capability_id in seen:
            continue
        seen.add(capability_id)
        provider = str(source.get("provider") or "").lower()
        category = str(source.get("category") or "").lower()
        tier = (
            "core"
            if capability_id
            in {
                "market.last_price",
                "market.previous_close",
                "market.change_pct",
                "market.volume",
            }
            else "watchlist"
        )
        calculation_rows.append({
            "capability_id": capability_id,
            "capability_key": next(
                (
                    key
                    for key, outputs in QMD_CORE_SCANNER_FIELDS.items()
                    if capability_id in outputs or str(source.get("field") or "") in outputs
                ),
                capability_id,
            ),
            "name": str(source.get("label") or capability_id),
            "description": str(source.get("summary") or "Published QMD observation."),
            "category": str(source.get("category") or source.get("provider") or "Market observations"),
            "provider": str(source.get("provider") or "QMD"),
            "output_type": str(source.get("value_type") or "number"),
            "capability_type": _strategy_input_capability_type(capability_id),
            "priority": "p0" if tier == "core" else "p2",
            "availability": "implemented",
            "inputs": [str(source.get("provider") or "QMD")],
            "fields": [str(source.get("field") or source.get("source_id") or capability_id)],
            "calculation": str(source.get("summary") or "Published from causally available QMD observations."),
            "timeframes": list(source.get("timeframes") or []),
            "selected_timeframes": list(source.get("timeframes") or []),
            "enabled": True,
            "configurable": tier == "watchlist",
            "system_required": tier == "core",
            "tier": tier,
        })
    operational = [
        ("instrument-identity", "Instrument eligibility and identity", "Reference-backed identity, listing, venue, and tradability eligibility.", "Reference and eligibility", "core"),
        ("market-quality", "Market-data freshness and quality", "Freshness, completeness, crossed-market, halt, and stale-data checks.", "Data quality", "core"),
        ("liquidity-rank", "Liquidity and candidate ranking", "Price, volume, spread, activity, liquidity, and base candidate rank.", "Ranking", "core"),
        ("news-events", "News observations", "Point-in-time company-news events and scored downstream signals.", "Event intelligence", "watchlist"),
        ("sec-events", "SEC observations", "Point-in-time filing events and derived issuer signals.", "Event intelligence", "watchlist"),
        ("membership-history", "Watchlist membership history", "Append-only add, remove, expiry, and override events with causal reasons.", "Audit and history", "watchlist"),
    ]
    for capability_id, name, description, category, tier in operational:
        if capability_id in seen:
            continue
        required = capability_id in {"instrument-identity", "market-quality", "liquidity-rank", "membership-history"}
        calculation_rows.append({
            "capability_id": capability_id,
            "name": name,
            "description": description,
            "category": category,
            "provider": "QMD",
            "output_type": "system",
            "capability_type": "event" if capability_id in {"news-events", "sec-events"} else "system",
            "priority": "p0" if required else "p2",
            "availability": "implemented",
            "inputs": ["QMD services"],
            "fields": {
                "instrument-identity": ["identity.symbol", "identity.company_name", "identity.exchange", "identity.is_tradable"],
                "market-quality": ["market.quality_state", "market.event_age_ms", "market.event_at", "market.quality_flags", "market.degradation_reason"],
                "liquidity-rank": ["market.liquidity_rank", "market.liquidity_score", "market.spread_bps"],
            }.get(capability_id, [capability_id]),
            "calculation": description,
            "timeframes": [],
            "selected_timeframes": [],
            "enabled": True,
            "configurable": not required,
            "system_required": required,
            "tier": tier,
        })
    calculation_rows = [
        _normalize_discovery_capability_contract(row) for row in calculation_rows
    ]
    symbols = sorted({
        str(row.get("ticker") or "").upper()
        for row in runtime_assignments
        if str(row.get("ticker") or "").strip()
    })
    merged_rule_sets: list[dict[str, Any]] = []
    rule_set_ids: set[str] = set()
    for rule_set in [*(rule_sets or []), *_default_watchlist_rule_sets()]:
        rule_set_id = str(rule_set.get("rule_set_id") or "")
        if not rule_set_id or rule_set_id in rule_set_ids:
            continue
        rule_set_ids.add(rule_set_id)
        merged_rule_sets.append(deepcopy(rule_set))
    classifications = _market_discovery_classifications()
    field_catalog = _market_discovery_field_catalog(calculation_rows, classifications)
    _bind_discovery_scanner_columns(calculation_rows, field_catalog)
    data_fields = build_data_field_catalog(calculation_rows, field_catalog)
    migrate_rule_set_field_refs(merged_rule_sets, data_fields)
    column_catalog = build_column_catalog(data_fields, merged_rule_sets)
    output_index = data_field_output_index(data_fields)
    default_columns = [
        str(row.get("column_id") or "")
        for row in column_catalog
        if bool(row.get("default_visible"))
    ]
    result = {
        "security_universe": {
            "universe_id": "qmd-security-universe",
            "name": "QMD Security Universe",
            "description": "The broad authoritative instrument set eligible for the Core Scan.",
            "enabled": True,
            "configurable": False,
        },
        "core_scan": {
            "scan_id": "qmd-core-scan",
            "name": "Core Scan",
            "description": "Compose the full-universe scanner from registered Data Fields and Rule Sets.",
            "refresh_interval_ms": 1000,
            "published": True,
            "inclusion_rule_sets": [],
            "inclusion_operator": "all",
            "ranking_field": "market.liquidity_rank",
            "ranking_field_ref": str(
                output_index.get("market.liquidity_rank", {}).get("field_ref") or ""
            ),
            "ranking_direction": "ascending",
            "maximum_size": 250,
            "columns": default_columns,
        },
        "calculation_catalog": calculation_rows,
        "atomic_fields": atomic_field_catalog(
            value
            for row in calculation_rows
            for value in row.get("inputs") or []
            if str(value)
        ),
        "data_fields": data_fields,
        "data_field_plan": {},
        "classifications": classifications,
        "field_catalog": field_catalog,
        "column_catalog": column_catalog,
        "rule_sets": merged_rule_sets,
        "watchlists": _default_watchlist_templates(symbols, calculation_rows),
        "signal_streams": _default_signal_streams(column_catalog),
        "model_serving": {
            "bar_gpt": {
                "enabled": True,
                "watchlist_ids": ["core-candidates"],
                "trigger_mode": "auto",
                "maximum_tickers": 500,
                "model_ids": ["bar_gpt_v2", "bar_gpt_v3"],
            }
        },
    }
    for watchlist in result["watchlists"]:
        watchlist["ranking_field_ref"] = str(
            output_index.get(str(watchlist.get("ranking_field") or ""), {}).get("field_ref") or ""
        )
    _normalize_market_discovery_interval_specs(result)
    result["data_field_plan"] = compile_data_field_plan(result)
    return result


_RETIRED_VWAP_SOURCE_IDS = {
    "signal.vwap_transition.score",
    "indicator.vwap.slope",
}
_LEGACY_VWAP_SOURCE_ID = "indicator.vwap.value"
_EXECUTION_VWAP_SOURCE_ID = "indicator.vwap.execution_value"


def _retire_legacy_vwap_rule_sets(discovery: dict[str, Any]) -> None:
    """Migrate executable VWAP references and remove retired derivative rules."""

    execution_vwap_ref = next(
        (
            str(output.get("field_ref") or "")
            for data_field in discovery.get("data_fields") or []
            for output in data_field.get("outputs") or []
            if str(output.get("source_id") or "") == _EXECUTION_VWAP_SOURCE_ID
            and str(output.get("field_ref") or "")
        ),
        "",
    )
    for rule_set in discovery.get("rule_sets") or []:
        for condition in rule_set.get("conditions") or []:
            for side in ("left", "right"):
                source_key = f"{side}_source_id"
                if str(condition.get(source_key) or "") != _LEGACY_VWAP_SOURCE_ID:
                    continue
                condition[source_key] = _EXECUTION_VWAP_SOURCE_ID
                if execution_vwap_ref:
                    condition[f"{side}_field_ref"] = execution_vwap_ref

    retired_rule_set_ids = {
        str(rule_set.get("rule_set_id") or "")
        for rule_set in discovery.get("rule_sets") or []
        if any(
            {
                str(condition.get("left_source_id") or ""),
                str(condition.get("right_source_id") or ""),
            }
            & _RETIRED_VWAP_SOURCE_IDS
            for condition in rule_set.get("conditions") or []
        )
    }
    if retired_rule_set_ids:
        discovery["rule_sets"] = [
            rule_set
            for rule_set in discovery.get("rule_sets") or []
            if str(rule_set.get("rule_set_id") or "") not in retired_rule_set_ids
        ]
    retired_column_ids = {
        str(column.get("column_id") or "")
        for column in discovery.get("column_catalog") or []
        if str(column.get("source_kind") or "") == "rule_set"
        and str(column.get("source_id") or "") in retired_rule_set_ids
    }
    discovery["column_catalog"] = [
        column
        for column in discovery.get("column_catalog") or []
        if str(column.get("column_id") or "") not in retired_column_ids
    ]
    compositions = [dict(discovery.get("core_scan") or {})]
    compositions.extend(discovery.get("watchlists") or [])
    compositions.extend(discovery.get("signal_streams") or [])
    for composition in compositions:
        for key in ("inclusion_rule_sets", "exclusion_rule_sets"):
            composition[key] = [
                str(rule_set_id)
                for rule_set_id in composition.get(key) or []
                if str(rule_set_id) not in retired_rule_set_ids
            ]
        if "columns" in composition:
            composition["columns"] = [
                str(column_id)
                for column_id in composition.get("columns") or []
                if str(column_id) not in retired_column_ids
            ]
    discovery["data_field_plan"] = compile_data_field_plan(discovery)


def _default_data_plan_ids() -> dict[str, str]:
    return {
        "replay": "market.historical_scanner_materialization.v1",
        "backtest": "market.historical_scanner_materialization.v1",
        "backtest_debug": "market.historical_scanner_materialization.v1",
        "paper": "qmd.scanner.snapshot.v1",
        "live": "qmd.scanner.snapshot.v1",
    }


def _strategy_definition_summary(definition: dict[str, Any]) -> dict[str, Any]:
    config = dict(definition.get("config") or {})
    executor = dict(definition.get("executor") or {})
    return {
        "strategy_id": str(definition.get("strategy_id") or ""),
        "revision": int(definition.get("revision") or 0),
        "name": str(definition.get("name") or ""),
        "automatic": bool(definition.get("automatic", True)),
        "direction": str(config.get("direction") or ""),
        "supported_sides": list(config.get("supported_sides") or ["long"]),
        "executor_installed": bool(executor.get("installed")),
        "executor_key": str(executor.get("key") or ""),
        "executor_schema_version": executor.get("schema_version"),
        "parameter_defaults": deepcopy(dict(config.get("parameters") or {})),
        "input_source_ids": [
            str(row.get("source_id") or "")
            for row in config.get("input_catalog") or []
            if str(row.get("source_id") or "")
        ],
    }


def _session_profile_id_for_modes(modes: set[str]) -> str:
    return "live-session" if modes.intersection({"paper", "live"}) else "historical-session"


def _build_session_configuration(
    bindings: list[dict[str, Any]],
    mandates: list[dict[str, Any]],
    run_plans: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the execution authority graph independently from Canvas presentation."""

    profiles = [
        {
            "session_profile_id": "historical-session",
            "name": "Historical simulation",
            "description": "Deterministic Replay, Backtest, and Debug clock with simulated execution.",
            "enabled": True,
            "modes": ["replay", "backtest", "backtest_debug"],
            "market_data": {"authority": "qmd_history", "clock": "event_time"},
            "manual_authority": {"enabled": True, "maximum": "confirm"},
            "recovery_policy": "resume_from_checkpoint",
            "execution_route_ids": [],
            "default_execution_route_id": "",
        },
        {
            "session_profile_id": "live-session",
            "name": "Live market session",
            "description": "Real-time QMD clock with explicit Paper or Live broker execution routes.",
            "enabled": True,
            "modes": ["paper", "live"],
            "market_data": {"authority": "qmd_live", "clock": "exchange_time"},
            "manual_authority": {"enabled": True, "maximum": "confirm"},
            "recovery_policy": "resume_from_checkpoint",
            "execution_route_ids": [],
            "default_execution_route_id": "",
        },
    ]
    profile_by_id = {str(row["session_profile_id"]): row for row in profiles}
    routes: list[dict[str, Any]] = []
    manual_mandates: list[dict[str, Any]] = []
    for binding in bindings:
        modes = set(binding.get("modes") or [])
        session_profile_id = _session_profile_id_for_modes(modes)
        account_key = str(binding.get("account_key") or "")
        if not account_key:
            continue
        route_id = f"{session_profile_id}:{account_key}"
        manual_mandate_id = f"session:{session_profile_id}:{account_key}"
        routes.append({
            "execution_route_id": route_id,
            "name": f"{binding.get('name') or account_key} route",
            "session_profile_id": session_profile_id,
            "account_key": account_key,
            "portfolio_mandate_id": manual_mandate_id,
            "oms_profile_id": "adaptive-regular",
            "modes": sorted(modes & set(profile_by_id[session_profile_id]["modes"])),
            "enabled": bool(binding.get("enabled", True)),
            "manual_enabled": True,
            "system_generated": True,
        })
        profile = profile_by_id[session_profile_id]
        profile["execution_route_ids"].append(route_id)
        if not profile["default_execution_route_id"]:
            profile["default_execution_route_id"] = route_id
        manual_mandates.append({
            "mandate_id": manual_mandate_id,
            "principal_kind": "session",
            "principal_id": session_profile_id,
            "run_plan_id": "",
            "account_key": account_key,
            "enabled": bool(binding.get("enabled", True)),
            "maximum_cash_fraction": 1.0,
            "maximum_planned_risk_fraction": 0.01,
            "maximum_positions": 10,
            "assignment_mode": "single",
            "allocation_weight": 1.0,
            "maximum_action_authority": "confirm",
            "allow_replacement": False,
            "minimum_replacement_improvement_pct": 20.0,
        })

    routes_by_session: dict[str, list[dict[str, Any]]] = {}
    for route in routes:
        routes_by_session.setdefault(str(route["session_profile_id"]), []).append(route)
    mandate_ids_by_plan: dict[str, list[str]] = {}
    for mandate in mandates:
        mandate_ids_by_plan.setdefault(str(mandate.get("run_plan_id") or ""), []).append(
            str(mandate.get("mandate_id") or "")
        )
    deployments: list[dict[str, Any]] = []
    for plan in run_plans:
        plan_modes = set(plan.get("allowed_environments") or [])
        for profile in profiles:
            session_profile_id = str(profile["session_profile_id"])
            eligible_modes = sorted(plan_modes.intersection(set(profile.get("modes") or [])))
            if not eligible_modes:
                continue
            plan_id = str(plan.get("run_plan_id") or "")
            deployment_id = f"{plan_id}:{session_profile_id}"
            eligible_routes = [
                row for row in routes_by_session.get(session_profile_id, [])
                if str(row.get("account_key") or "") in {
                    str(mandate.get("account_key") or "")
                    for mandate in mandates
                    if str(mandate.get("run_plan_id") or "") == plan_id
                }
            ]
            deployments.append({
                "strategy_deployment_id": deployment_id,
                "name": str(plan.get("name") or plan_id),
                "description": str(plan.get("description") or ""),
                "run_plan_id": plan_id,
                "session_profile_id": session_profile_id,
                "execution_route_ids": [str(row["execution_route_id"]) for row in eligible_routes],
                "portfolio_mandate_ids": list(mandate_ids_by_plan.get(plan_id, [])),
                "enabled": bool(plan.get("enabled", True)),
                "enablement": deepcopy(plan.get("enablement") or {"state": "enabled", "scope": "persistent"}),
                "activation": deepcopy(plan.get("activation") or {}),
                "headless": True,
                "priority": 100,
                "modes": eligible_modes,
                "system_generated": True,
            })
    return {
        "profiles": profiles,
        "execution_routes": routes,
        "strategy_deployments": deployments,
        "manual_mandates": manual_mandates,
    }


def _default_draft() -> dict[str, Any]:
    definition = get_strategy_definition(STRATEGY_ID, STRATEGY_REVISION)
    parameters = deepcopy(definition.get("config", {}).get("parameters") or default_long_momentum_parameters())
    system_profiles = [
        _strategy_profile(
            "long-momentum-balanced",
            "Long Momentum · Balanced",
            "Balanced Long Momentum template with breakout confirmation, protection, and re-entry.",
            parameters,
            origin="system",
        ),
    ]
    system_profiles[0]["name"] = "Long Momentum · Squeeze"
    system_profiles[0]["description"] = (
        "Extended-hours long momentum strategy activated by the Early Squeeze Move "
        "episode start, admitted once by sustained executable liquidity, and entered "
        "through QMD's persistent Unified Structural Level Book while one-second MACD is positive and open."
    )
    squeeze_lifecycle = system_profiles[0]["lifecycle"]
    squeeze_lifecycle["trading_behavior"]["eligible_sessions"] = [
        "premarket"
    ]
    squeeze_lifecycle["trading_behavior"]["entry_cutoff_time"] = "09:29:59"
    squeeze_lifecycle["trading_behavior"]["flatten_time"] = "09:29:59"
    squeeze_lifecycle["initial_entry"]["opportunity"] = {
        "expression": {
            "kind": "operator",
            "operator": "and",
            "children": [{
                "kind": "rule_set",
                "rule_set_id": "strategy-squeeze-unified-resistance-break",
            }],
        }
    }
    squeeze_lifecycle["initial_entry"]["confirmation"] = {
        "expression": {
            "kind": "operator",
            "operator": "and",
            "children": [
                {
                    "kind": "rule_set",
                    "rule_set_id": "strategy-squeeze-volume-spread-quality",
                },
                {
                    "kind": "rule_set",
                    "rule_set_id": "strategy-squeeze-above-vwap-1s",
                },
                {
                    "kind": "rule_set",
                    "rule_set_id": "strategy-squeeze-macd-open-1s",
                },
            ],
        }
    }
    squeeze_lifecycle["initial_entry"]["add_steps"] = []
    squeeze_lifecycle["initial_entry"]["capital_request"] = {
        "mode": "all_available",
        "value": 1.0,
        "maximum_quantity": 5_000,
        "allow_replacement": False,
    }
    squeeze_lifecycle["reentry"]["capital_request"] = {
        "mode": "all_available",
        "value": 1.0,
        "maximum_quantity": 5_000,
        "allow_replacement": False,
    }
    squeeze_lifecycle["reentry"]["maximum_attempts"] = 0
    squeeze_lifecycle["reentry"]["unlimited_attempts"] = True
    squeeze_lifecycle["initial_entry"]["order_intent"][
        "protection_profile"
    ] = "structural-single-target"
    # A small-cap squeeze can leave the displayed offer before another quote
    # update arrives. Follow the executable touch by bounded ticks during the
    # causal entry deadline instead of leaving a whole-account order stranded
    # at its first ask after one partial fill.
    squeeze_lifecycle["initial_entry"]["order_intent"][
        "execution_policy"
    ] = "adaptive_very_urgent"
    squeeze_lifecycle["initial_entry"]["order_intent"]["deadline_ms"] = 5_000
    squeeze_lifecycle["reentry"]["order_intent"][
        "protection_profile"
    ] = "structural-single-target"
    squeeze_lifecycle["reentry"]["order_intent"][
        "execution_policy"
    ] = "adaptive_very_urgent"
    squeeze_lifecycle["reentry"]["order_intent"]["deadline_ms"] = 5_000
    squeeze_lifecycle["reentry"]["cooldown_ms"] = 0
    # Early Squeeze admits the ticker once for the whole campaign. Re-entry is
    # driven by the live structural/MACD state; it must not wait for every
    # confirmation source to publish a post-exit sample before trading again.
    squeeze_lifecycle["reentry"]["require_new_confirmation"] = False
    # The original Early Squeeze occurrence owns the campaign. A re-entry in
    # that same episode needs fresh post-exit market evidence, not a duplicate
    # scanner occurrence that may never be emitted during continuation.
    squeeze_lifecycle["reentry"]["require_new_signal_stream_id"] = ""
    squeeze_lifecycle["reentry"]["after_protective_exit"] = True
    squeeze_lifecycle["reentry"]["pullback_reclaim"] = {
        # The exact prior-frame below-to-above resistance crossing is itself
        # the required reclaim. A second pullback latch would delay or suppress
        # a valid crossing under the top-three contract.
        "enabled": False,
        "minimum_pullback_atr_multiple": 0.50,
        "minimum_pullback_bps": 25.0,
    }
    squeeze_lifecycle["reentry"]["rules"] = {
        "opportunity": deepcopy(squeeze_lifecycle["initial_entry"]["opportunity"]),
        "confirmation": deepcopy(squeeze_lifecycle["initial_entry"]["confirmation"]),
        "blockers": deepcopy(squeeze_lifecycle["initial_entry"]["blockers"]),
    }
    # The squeeze profile owns its causal exit ladder in the executor. Retain
    # the catalogued legacy routes as disabled definitions so configuration
    # validation and Strategy Studio can still explain what was superseded.
    for legacy_exit in squeeze_lifecycle["exit"]["rule_sets"]:
        legacy_exit["enabled"] = False
    system_profiles[0]["parameters"]["momentum_management"] = {
        "downside_loss_guard": {
            "enabled": True,
            "timeframe": "1s",
            "macd_closed": True,
            "below_vwap": True,
            "vwap_source_id": "indicator.vwap.execution_value",
        },
        "failure_to_extend": {
            "enabled": False,
            "minimum_gain_pct": 0.75,
            "minimum_extension_bps": 5.0,
            "stalled_for_ms": 3_000,
            "maximum_flow_structure_score": 0.15,
            "minimum_flow_price_divergence_score": 0.55,
            "position_fraction": 0.50,
            "maximum_uses": 1,
        },
        "qmd_exhaustion": {
            "enabled": False,
            "active_after_ms": 1_000,
            "maximum_flow_structure_score": -0.10,
            "minimum_confidence": 0.55,
            "minimum_flow_price_divergence_score": 0.60,
        },
        "structure_failure": {
            "enabled": False,
            "active_after_ms": 1_000,
            "buffer_bps": 5.0,
            "require_higher_low": True,
        },
        "macd_backstop": {
            "enabled": True,
            "active_after_ms": 0,
            "closed_for_ms": 0,
            "timeframe": "1s",
            "close_condition": "signal_above_line",
        },
    }
    system_profiles[0]["parameters"]["structural_entry"] = {
        "enabled": True,
        "selection_mode": "prior_completed_frame_top_n_below_session_high",
        "maximum_entry_levels": 3,
        # Structural admission is ticker-independent and owned exclusively by
        # the causal Unified Structural Level Book. Other level scores remain
        # observable, but they do not silently veto a level that satisfies the
        # configured hold/break contract.
        "minimum_salience": 0.0,
        "minimum_confidence": 0.0,
        "minimum_reaction_probability": 0.0,
        "minimum_hold_probability": 0.80,
        "maximum_break_count": 100,
        "maximum_break_probability": 1.0,
        "minimum_independent_pivot_count": 0,
        "minimum_level_age_ms": 0,
        "acceptance_buffer_bps": 0.0,
        "acceptance_hold_ms": 15_000,
        # Entry acceptance is an exact transition from the prior completed
        # one-second frame. It is never retained until later gates catch up.
        "acceptance_expires": True,
        # Bound only a single completed-frame leap over the selected
        # structural boundary. This is distinct from (and must not recreate)
        # a maximum distance-from-VWAP entry gate.
        "maximum_breakout_extension_bps": 500.0,
        "require_swing_high_frontier": False,
        "require_active_resistance_frontier": False,
    }
    system_profiles[0]["parameters"]["liquidity_admission"] = {
        "enabled": True,
        "latched": True,
        "minimum_price": 2.0,
        "maximum_price": 50.0,
        "minimum_session_dollar_volume": 1_000_000.0,
        "minimum_session_share_volume": 100_000.0,
        "minimum_trade_rate_10s": 1.0,
        "minimum_trade_rate_60s": 0.5,
        "minimum_current_trade_rate_10s": 5.0,
        # Require sustained participation as well as a short burst. A weak
        # tape can briefly clear the ten-second rate while remaining too thin
        # to enter and manage a whole-account small-cap position.
        "minimum_current_trade_rate_60s": 5.0,
        # Price-above-VWAP is already an exact protected confirmation rule.
        # Do not add an arbitrary distance floor that makes a fast causal
        # structural break wait until much of the squeeze has already traded.
        "minimum_vwap_extension_bps": 0.0,
        "minimum_initial_vwap_extension_bps": 0.0,
        "minimum_reentry_vwap_extension_bps": 0.0,
        # Campaign admission retains the protected rule-set authority.  The
        # order-time limit is intentionally distinct and non-latched for
        # volatile small-cap premarket names.
        "maximum_admission_spread_bps": 60.0,
        # Absolute volume and sustained tape activity are already latched.  A
        # three-cent spread on a roughly $3 small-cap is still executable for a
        # marketable entry; retaining the old 60-bps cap delayed otherwise valid
        # campaigns until after the breakout candle.
        "maximum_current_spread_bps": 100.0,
        # Preserve compatibility with immutable configurations whose one field
        # represented the order-time threshold.
        "maximum_spread_bps": 100.0,
    }
    system_profiles[0]["parameters"]["entry_momentum_confirmation"] = {
        # The causal one-second MACD contract is complete: line above signal
        # and the MACD line above zero. The signal line may remain negative.
        # Do not add a second histogram-slope gate;
        # that would reject valid entries even though the agreed MACD regime
        # is open.
        "enabled": False,
        "timeframe": "1s",
        "histogram_lookback_ms": 5_000,
        "minimum_histogram_increase": 0.0,
        "minimum_histogram_increase_bps": 0.25,
    }
    squeeze_lifecycle["reentry"]["target_replenishment"] = {
        # The single structural target is a whole-campaign exit, not a partial
        # profit pocket. Re-entry is therefore owned by the ordinary flat
        # campaign pullback/reclaim path. Arming replenishment from an
        # incremental child fill can race a still-working parent entry and
        # create overlapping buy and protective-sell groups.
        "enabled": False,
        "minimum_pullback_atr_multiple": 0.50,
        "minimum_pullback_bps": 25.0,
        "support_buffer_bps": 10.0,
    }
    system_profiles[0]["parameters"]["protection"]["profit_ladder"].update({
        "maximum_targets": 1,
        # The target is the third nearest qualifying level above the completed
        # one-second close. As completed bars accept successive levels while
        # MACD remains positive/open, the same rule advances the live target.
        "selection_mode": "ordinal_qualified_level",
        "target_level_ordinal": 3,
        "minimum_level_strength": 0.0,
        "minimum_level_confidence": 0.0,
        "minimum_reaction_probability": 0.0,
        "minimum_reversal_probability": 0.0,
        "minimum_hold_probability": 0.85,
        "maximum_break_count": 100,
        "maximum_break_probability": 1.0,
        "minimum_composite_score": 0.0,
        # Target geometry must not veto an otherwise valid causal entry. The
        # resting target can still be advanced as higher levels are accepted.
        "minimum_entry_target_gap_bps": 0.0,
        "premarket_maximum_gain_pct": 200.0,
    })
    system_profiles[0]["parameters"]["protection"]["luld_profit_target"].update({
        "buffer_bps": 25.0,
        "minimum_tick_offset_count": 2,
        "tick_size": 0.01,
        "include_current_spread": True,
    })
    system_profiles[0]["parameters"].setdefault("protection", {})["stop"] = {
        "method": "ordinal_qualified_support",
        "structure_buffer_bps": 0.0,
        "volatility_multiple": 1.25,
        "maximum_risk_pct": 15.0,
        "minimum_hold_probability": 0.85,
        "support_level_ordinal": 2,
        "prefer_closer_hybrid": True,
    }
    system_profiles[0]["parameters"]["protection"]["trailing"].update({
        "enabled": True,
        "activation_gain_pct": 0.0,
    })
    system_profiles[0]["parameters"]["profit_pocket"]["enabled"] = False
    system_profiles[0]["action_policy_ids"] = []
    system_profiles[0]["protected"] = True
    news_profile = deepcopy(system_profiles[0])
    news_profile["profile_id"] = "long-momentum-bullish-news"
    news_profile["name"] = "Long Momentum · Bullish News"
    news_profile["description"] = (
        "Extended-hours long momentum strategy activated immediately by a "
        "forecast-eligible positive News Synthesis V1 occurrence, subject to executable spread quality."
    )
    news_profile["lifecycle"]["initial_entry"]["opportunity"] = {
        "expression": {
            "kind": "operator",
            "operator": "and",
            "children": [{"kind": "rule_set", "rule_set_id": "watchlist-news-bullish"}],
        }
    }
    news_profile["lifecycle"]["initial_entry"]["confirmation"] = {
        "expression": {
            "kind": "operator",
            "operator": "and",
            "children": [{"kind": "rule_set", "rule_set_id": "strategy-live-spread-quality"}],
        }
    }
    news_profile["lifecycle"]["initial_entry"]["add_steps"] = []
    news_profile["protected"] = True
    system_profiles.append(news_profile)
    profile_templates = [
        _strategy_profile(
            "long-momentum-template",
            "Long Momentum · Balanced",
            "Balanced Long Momentum template with breakout confirmation, protection, and re-entry.",
            parameters,
            origin="system",
        ),
    ]
    source_assignments = list_strategy_assignments(active_only=True)
    account_ids = list(dict.fromkeys(str(row["account_id"]) for row in source_assignments)) or ["replay"]
    account_keys = {account_id: _account_key(account_id, index) for index, account_id in enumerate(account_ids)}
    runtime_assignments = [
        {
            "assignment_id": str(row["assignment_id"]),
            "account_key": account_keys[str(row["account_id"])],
            "ticker": str(row["ticker"]).upper(),
            "conid": int(row["conid"]),
            "status": str(row["status"]),
            "permissions": dict(row.get("permissions") or {}),
            "parameters": dict(row.get("parameters") or {}),
        }
        for row in source_assignments
    ]
    # Retire the legacy consolidated VWAP from every catalogued rule, including
    # superseded profile rules retained for configuration explainability.  The
    # catalog may preserve those rule identities, but it must expose only the
    # execution-eligible VWAP authority to current scanners and strategies.
    for rule_set in system_profiles[0].get("rule_set_catalog") or []:
        for condition in rule_set.get("conditions") or []:
            if condition.get("left_source_id") == "indicator.vwap.value":
                condition["left_source_id"] = "indicator.vwap.execution_value"
            if condition.get("right_source_id") == "indicator.vwap.value":
                condition["right_source_id"] = "indicator.vwap.execution_value"
    rule_set_catalog = list(system_profiles[0].get("rule_set_catalog") or [])
    rule_set_catalog = [
        rule_set
        for rule_set in rule_set_catalog
        if not any(
            {
                str(condition.get("left_source_id") or ""),
                str(condition.get("right_source_id") or ""),
            }
            & _RETIRED_VWAP_SOURCE_IDS
            for condition in rule_set.get("conditions") or []
        )
    ]
    discovery = _default_market_discovery(
        runtime_assignments,
        rule_set_catalog,
    )
    for profile in [*system_profiles, *profile_templates]:
        profile.pop("rule_set_catalog", None)
    policy = asdict(PortfolioPolicy())
    policy.update({
        # "Whole account" is the test allocation authority, but submitting
        # exactly net liquidation / reference price leaves no room for the
        # simulated per-share commission or a one-tick aggressive limit. Keep
        # a bounded execution reserve while allowing the strategy to use the
        # rest of the account.
        "maximum_buying_power_utilization": 0.995,
        "maximum_position_fraction": 1.0,
        "maximum_ticker_fraction": 1.0,
        # The squeeze campaign intentionally decomposes one position into five
        # independently protected OCA slices.  Portfolio must authorize the
        # same topology that Strategy and OMS publish; otherwise a valid entry
        # is rejected before any order reaches the broker.
        "maximum_protection_slices": 5,
        "maximum_planned_risk_fraction": 0.06,
        "maximum_open_risk_fraction": 0.06,
        "maximum_open_positions": 3,
        "allow_outside_rth": True,
        "allow_overnight": False,
    })
    real_policy = deepcopy(policy)
    real_policy.update({
        "policy_id": "long-momentum-real-80",
        "revision": 1,
        "eligible_equity_fraction": 0.80,
        "maximum_position_fraction": 0.80,
        "maximum_ticker_fraction": 0.80,
        "maximum_planned_risk_fraction": 0.0025,
        "maximum_open_risk_fraction": 0.0075,
    })
    bindings = [
        {
            "account_key": account_keys[account_id],
            "name": "Backtest account" if account_id in {"replay", "backtest"} else account_id,
            "source_account_id": account_id,
            "account_class": "simulated",
            "base_currency": "USD",
            "session_key": "replay",
            "portfolio_policy_id": policy["policy_id"],
            "enabled": True,
            "modes": ["replay", "backtest", "backtest_debug"],
        }
        for account_id in account_ids
    ]
    _ensure_environment_account_bindings(bindings, policy["policy_id"])
    for binding in bindings:
        if set(binding.get("modes") or []).intersection({"paper", "live"}):
            binding["portfolio_policy_id"] = real_policy["policy_id"]
    mandates = [
        {
            "mandate_id": f"balanced-{binding['account_key']}",
            "run_plan_id": "balanced-replay",
            "account_key": binding["account_key"],
            "enabled": True,
            "maximum_cash_fraction": 1.0,
            "maximum_planned_risk_fraction": 0.06,
            "maximum_positions": 3,
            "assignment_mode": "single",
            "allocation_weight": 1.0,
            "maximum_action_authority": "automatic",
            "allow_replacement": False,
            "minimum_replacement_improvement_pct": 20.0,
        }
        for binding in bindings
        if "replay" in binding["modes"]
    ]
    live_mandates = [
        {
            "mandate_id": f"long-momentum-squeeze-{binding['account_key']}",
            "run_plan_id": f"long-momentum-squeeze-{binding['account_key']}",
            "account_key": binding["account_key"],
            "enabled": True,
            "maximum_cash_fraction": 0.80,
            "maximum_planned_risk_fraction": 0.0025,
            "maximum_positions": 3,
            "assignment_mode": "single",
            "allocation_weight": 1.0,
            "maximum_action_authority": "automatic",
            "allow_replacement": False,
            "minimum_replacement_improvement_pct": 20.0,
        }
        for binding in bindings
        if set(binding.get("modes") or []).intersection({"paper", "live"})
    ]
    mandates.extend(live_mandates)
    news_live_mandates = [
        {
            **deepcopy(mandate),
            "mandate_id": str(mandate["mandate_id"]).replace("long-momentum-squeeze", "long-momentum-news"),
            "run_plan_id": str(mandate["run_plan_id"]).replace("long-momentum-squeeze", "long-momentum-news"),
        }
        for mandate in live_mandates
    ]
    mandates.extend(news_live_mandates)
    universes = [
        _default_universe(runtime_assignments),
        {
            "universe_id": "price-squeeze-signal-universe",
            "name": "Price Squeeze signals",
            "description": "Tickers are admitted causally when an Early Squeeze Move starts; Exact 5% remains observable as a continuation milestone.",
            "source": "signal_stream",
            "signal_stream_ids": ["price-squeeze-early"],
            "symbols": [],
            "enabled": True,
        },
        {
            "universe_id": "bullish-news-signal-universe",
            "name": "Bullish News signals",
            "description": "Tickers are admitted causally by forecast-eligible positive News Synthesis V1 occurrences.",
            "source": "signal_stream",
            "signal_stream_ids": ["bullish-news-v1"],
            "symbols": [],
            "enabled": True,
        },
    ]
    live_run_plans = [
        {
            "run_plan_id": f"long-momentum-squeeze-{binding['account_key']}",
            "name": f"Long Momentum · Price Squeeze · {str(binding['account_key']).title()}",
            "description": "Session-enabled extended-hours momentum execution activated only by the first Early Squeeze Move event-time episode start.",
            "profile_id": "long-momentum-balanced",
            "oms_profile_id": "adaptive-regular",
            "universe_id": "price-squeeze-signal-universe",
            "watchlist_ids": [],
            "signal_stream_ids": ["price-squeeze-early"],
            "activation": {"event_policy": "new_occurrences", "watchlist_policy": "not_required"},
            "enablement": {"state": "disabled", "scope": "persistent", "effective_session": ""},
            "canvas_profile_id": "current-canvas",
            "canvas_profile_id": "current-canvas",
            "data_plan_ids": _default_data_plan_ids(),
            "source_revision_policy": "require_complete",
            "book_id": "default",
            "action_authority": {
                **_default_action_authority(),
                "initial_entry": "automatic",
                "reentry": "automatic",
                "strategic_exit": "automatic",
            },
            "campaign_lifecycle": {
                **_default_campaign_policy(),
                "initial_entry_authority": "automatic",
                "reentry_authority": "automatic",
            },
            "safety_supervisor": _default_safety_supervisor(),
            "mandate_ids": [f"long-momentum-squeeze-{binding['account_key']}"],
            "enabled": True,
            "allowed_environments": list(binding.get("modes") or []),
            "runtime_assignments": [],
            "protected": True,
        }
        for binding in bindings
        if set(binding.get("modes") or []).intersection({"paper", "live"})
    ]
    live_run_plans.extend(
        {
            **deepcopy(plan),
            "run_plan_id": str(plan["run_plan_id"]).replace("long-momentum-squeeze", "long-momentum-news"),
            "name": str(plan["name"]).replace("Price Squeeze", "Bullish News"),
            "description": "Session-enabled extended-hours momentum execution activated by a fresh forecast-eligible positive News Synthesis V1 occurrence.",
            "profile_id": "long-momentum-bullish-news",
            "universe_id": "bullish-news-signal-universe",
            "signal_stream_ids": ["bullish-news-v1"],
            "mandate_ids": [str(value).replace("long-momentum-squeeze", "long-momentum-news") for value in plan["mandate_ids"]],
        }
        for plan in list(live_run_plans)
    )
    replay_run_plan = {
        "run_plan_id": "balanced-replay",
        "name": "Balanced Replay",
        "description": "Approved balanced strategy prepared for historical simulation.",
        "profile_id": "long-momentum-balanced",
        "oms_profile_id": "adaptive-regular",
        "universe_id": "configured-watch-universe",
        "watchlist_ids": ["squeeze-tradable-candidates"],
        "signal_stream_ids": ["price-squeeze-early"],
        "activation": {"event_policy": "new_occurrences", "watchlist_policy": "not_required"},
        "enablement": {"state": "enabled", "scope": "persistent", "effective_session": ""},
        "canvas_profile_id": "current-canvas",
        "data_plan_ids": _default_data_plan_ids(),
        "source_revision_policy": "require_complete",
        "book_id": "default",
        "action_authority": {
            **_default_action_authority(),
            "initial_entry": "automatic",
            "reentry": "automatic",
        },
        "campaign_lifecycle": {
            **_default_campaign_policy(),
            "initial_entry_authority": "automatic",
            "reentry_authority": "automatic",
            # Early Squeeze discovers the campaign once.  Subsequent valid
            # structural/MACD opportunities in the same session are not
            # artificially capped or delayed by the Run Plan.
            "maximum_reentries": 0,
            "reentry_cooldown_ms": 0,
            "session_end_behavior": "exit_and_stop",
        },
        "safety_supervisor": _default_safety_supervisor(),
        "mandate_ids": [row["mandate_id"] for row in mandates if row["run_plan_id"] == "balanced-replay"],
        "enabled": True,
        "allowed_environments": ["replay", "backtest", "backtest_debug"],
        "runtime_assignments": runtime_assignments,
    }
    all_run_plans = [replay_run_plan, *live_run_plans]
    sessions = _build_session_configuration(bindings, mandates, all_run_plans)
    deployment_by_plan = {
        str(row.get("run_plan_id") or ""): str(row.get("strategy_deployment_id") or "")
        for row in sessions.get("strategy_deployments") or []
    }
    for mandate in mandates:
        run_plan_id = str(mandate.get("run_plan_id") or "")
        if run_plan_id:
            mandate["principal_kind"] = "strategy_deployment"
            mandate["principal_id"] = deployment_by_plan.get(run_plan_id, run_plan_id)
    mandates.extend(sessions.pop("manual_mandates"))
    _normalize_market_discovery_interval_specs(discovery)
    discovery["data_field_plan"] = compile_data_field_plan(discovery)
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "strategy": {
            "default_profile_id": "long-momentum-balanced",
            "definitions": [_strategy_definition_summary(row) for row in list_strategy_definitions()],
            "input_catalog": installed_strategy_input_catalog(),
            "profile_templates": profile_templates,
            "profiles": system_profiles,
        },
        "trading_actions": {
            "definitions": trading_action_definitions(),
            "policies": default_action_policies(),
        },
        "market_discovery": discovery,
        "run_plans": {
            "universes": universes,
            "plans": all_run_plans,
        },
        "sessions": sessions,
        "portfolio": {"policies": [policy, real_policy], "groups": [], "mandates": mandates},
        "oms": {
            "profiles": [_default_oms_profile()],
            "execution_policies": _default_execution_policies(),
            "protection_profiles": _default_protection_profiles(),
        },
        "accounts": {"bindings": bindings},
    }


def _assert_published_profiles_unchanged(
    current: dict[str, Any], candidate: dict[str, Any]
) -> None:
    current_profiles = {
        str(row.get("profile_id")): row
        for row in dict(current.get("strategy") or {}).get("profiles") or []
        if str(row.get("publication_status") or "draft") == "published"
    }
    candidate_profiles = {
        str(row.get("profile_id")): row
        for row in dict(candidate.get("strategy") or {}).get("profiles") or []
    }
    for profile_id, published in current_profiles.items():
        proposed = candidate_profiles.get(profile_id)
        if proposed is None:
            raise ValueError(
                "Published strategies are immutable and cannot be deleted; clone one to create a new draft"
            )
        if _canonical_profile_content(proposed) != _canonical_profile_content(published):
            raise ValueError(
                f"Published Strategy {published.get('name')} is immutable; clone it to create a modified draft"
            )


def _canonical_profile_content(profile: dict[str, Any]) -> str:
    return json.dumps(profile, separators=(",", ":"), sort_keys=True)


def _validate_rule_constant(output: dict[str, Any], value: Any, label: str) -> None:
    domain = dict(output.get("value_domain") or {})
    kind = str(domain.get("kind") or "text")
    allowed = [row.get("value") for row in domain.get("allowed_values") or []]
    if bool(domain.get("closed")):
        if value not in allowed:
            raise ValueError(f"{label} requires one registered value: {', '.join(map(str, allowed))}")
        return
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{label} requires a Boolean value")
        return
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label} requires a numeric value")
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} requires a {kind} value")
    try:
        if kind == "date":
            date.fromisoformat(value)
        elif kind == "time":
            datetime_time.fromisoformat(value)
        elif kind == "timestamp":
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone missing")
    except ValueError as exc:
        raise ValueError(f"{label} requires a valid ISO {kind}") from exc


def _runtime_rule_set_ids(section: dict[str, Any]) -> set[str]:
    """Return only Rule Sets reachable by an enabled runtime composition."""

    core_scan = dict(section.get("core_scan") or {})
    compositions = [core_scan]
    compositions.extend(
        row
        for row in section.get("watchlists") or []
        if bool(row.get("enabled", True))
        and str(row.get("availability") or "available") == "available"
    )
    compositions.extend(
        row
        for row in section.get("signal_streams") or []
        if bool(row.get("enabled", True))
    )
    selected = {
        str(rule_set_id)
        for composition in compositions
        for key in ("inclusion_rule_sets", "exclusion_rule_sets")
        for rule_set_id in composition.get(key) or []
        if str(rule_set_id)
    }
    columns = {
        str(row.get("column_id") or ""): row
        for row in section.get("column_catalog") or []
    }
    selected.update(
        str(column.get("source_id") or "")
        for composition in compositions
        for column_id in composition.get("columns") or []
        if str((column := columns.get(str(column_id), {})).get("source_kind") or "")
        == "rule_set"
        and str(column.get("source_id") or "")
    )
    return selected


def _validate_market_discovery(
    section: dict[str, Any], *, runtime_only: bool = False
) -> None:
    universe = dict(section.get("security_universe") or {})
    if not str(universe.get("universe_id") or ""):
        raise ValueError("Market Discovery requires one QMD Security Universe")
    core_scan = dict(section.get("core_scan") or {})
    if not str(core_scan.get("scan_id") or ""):
        raise ValueError("Market Discovery requires one Core Scan")
    calculations = list(section.get("calculation_catalog") or [])
    calculation_ids = _unique_ids(calculations, "capability_id", "QMD capability")
    required_calculation_ids = {
        str(row.get("capability_id") or "")
        for row in _default_market_discovery([], []).get("calculation_catalog", [])
        if bool(row.get("system_required"))
    }
    missing_required = required_calculation_ids - calculation_ids
    if missing_required:
        raise ValueError(
            "Market Discovery is missing required QMD capabilities: "
            + ", ".join(sorted(missing_required))
        )
    rule_sets = list(section.get("rule_sets") or [])
    rule_set_ids = _unique_ids(
        rule_sets,
        "rule_set_id",
        "Watchlist rule set",
    )
    runtime_rule_set_ids = _runtime_rule_set_ids(section) if runtime_only else rule_set_ids
    validated_rule_sets = [
        rule_set
        for rule_set in rule_sets
        if str(rule_set.get("rule_set_id") or "") in runtime_rule_set_ids
    ]
    for rule_set in validated_rule_sets:
        _validate_rule_set_definition(rule_set, f"Watchlist rule set {rule_set.get('name')}")
    field_catalog = list(section.get("field_catalog") or [])
    field_source_ids = _unique_ids(
        field_catalog,
        "source_id",
        "Market Discovery field",
    )
    if not field_source_ids:
        raise ValueError("Market Discovery requires a registered field catalog")
    field_by_source = {
        str(row.get("source_id") or ""): row for row in field_catalog
    }
    atomic_fields = list(section.get("atomic_fields") or [])
    atomic_field_ids = _unique_ids(
        atomic_fields, "atomic_field_id", "Atomic Field"
    )
    if not atomic_field_ids:
        raise ValueError("Market Discovery requires an exhaustive Atomic Field catalog")
    data_fields = list(section.get("data_fields") or [])
    if not data_fields:
        raise ValueError("Market Discovery requires at least one registered Data Field")
    validate_data_field_catalog(data_fields)
    output_index = data_field_output_index(data_fields)
    def validate_aggregation(output: dict[str, Any], value: Any, label: str) -> None:
        contract = dict(output.get("aggregation") or {})
        mode = str(contract.get("mode") or "none")
        selected = str(value or "")
        if mode == "required" and selected not in set(contract.get("allowed") or []):
            raise ValueError(f"{label} requires a compatible aggregation function")
        if mode != "required" and selected:
            raise ValueError(f"{label} cannot override its intrinsic aggregation")
    data_field_by_output_ref = {
        str(output.get("field_ref") or ""): data_field
        for data_field in data_fields
        for output in data_field.get("outputs") or []
        if str(output.get("field_ref") or "")
    }
    output_refs = {
        str(output.get("field_ref") or "")
        for data_field in data_fields
        for output in data_field.get("outputs") or []
        if str(output.get("field_ref") or "")
    }
    for rule_set in validated_rule_sets:
        for condition in rule_set.get("conditions") or []:
            source_id = str(condition.get("left_source_id") or "")
            left_ref = str(condition.get("left_field_ref") or "")
            if not left_ref or left_ref not in output_refs:
                raise ValueError(
                    f"Rule Set {rule_set.get('name')} references unknown Data Field output {left_ref or '<empty>'}"
                )
            output = output_index.get(left_ref)
            if output is None or str(output.get("source_id") or "") != source_id:
                raise ValueError(
                    f"Watchlist rule set {rule_set.get('name')} references unknown field {source_id}"
                )
            output_data_field = data_field_by_output_ref.get(left_ref)
            if bool(rule_set.get("enabled")) and output_data_field is not None and not bool(output_data_field.get("enabled")):
                raise ValueError(
                    f"Enabled Rule Set {rule_set.get('name')} references unavailable Data Field {source_id}"
                )
            comparator = str(condition.get("comparator") or "")
            if not bool(output.get("filterable")) or comparator not in set(
                output.get("filter_operators") or []
            ):
                raise ValueError(
                    f"Watchlist rule set {rule_set.get('name')} cannot use {comparator} on {source_id}"
                )
            left_value_selection = str(condition.get("left_value_selection") or "latest")
            if left_value_selection != "latest":
                raise ValueError(
                    f"Rule Set {rule_set.get('name')} requires latest value selection for {source_id}"
                )
            left_interval = normalize_interval_spec(condition.get("left_interval"))
            left_available = {
                str(spec["unit"])
                for value in output.get("available_intervals") or []
                if (spec := normalize_interval_spec(value)) is not None
            }
            if left_available and (left_interval is None or left_interval["unit"] not in left_available):
                raise ValueError(
                    f"Rule Set {rule_set.get('name')} requires a supported interval for {source_id}"
                )
            if not left_available and left_interval is not None:
                raise ValueError(
                    f"Rule Set {rule_set.get('name')} assigns an interval to non-interval field {source_id}"
                )
            validate_aggregation(output, condition.get("left_aggregation"), f"Rule Set {rule_set.get('name')} field {source_id}")
            right_source_id = str(condition.get("right_source_id") or "")
            right_ref = str(condition.get("right_field_ref") or "")
            if comparator == "above_by_bps" and not right_source_id:
                raise ValueError(
                    f"Rule Set {rule_set.get('name')} requires a comparison Data Field for above_by_bps"
                )
            if right_source_id and (not right_ref or right_ref not in output_refs):
                raise ValueError(
                    f"Rule Set {rule_set.get('name')} references unknown comparison Data Field output {right_ref or '<empty>'}"
                )
            if right_source_id:
                right_value_selection = str(condition.get("right_value_selection") or "latest")
                if right_value_selection != "latest":
                    raise ValueError(
                        f"Rule Set {rule_set.get('name')} requires latest comparison value selection for {right_source_id}"
                    )
                right_output = output_index.get(right_ref, {})
                if str(right_output.get("source_id") or "") != right_source_id:
                    raise ValueError(
                        f"Watchlist rule set {rule_set.get('name')} references unknown comparison field {right_source_id}"
                    )
                right_data_field = data_field_by_output_ref.get(right_ref)
                if bool(rule_set.get("enabled")) and right_data_field is not None and not bool(right_data_field.get("enabled")):
                    raise ValueError(
                        f"Enabled Rule Set {rule_set.get('name')} references unavailable comparison Data Field {right_source_id}"
                    )
                left_type = str(output.get("value_type") or "").lower()
                right_type = str(right_output.get("value_type") or "").lower()
                left_unit = str(output.get("unit") or "").lower()
                right_unit = str(right_output.get("unit") or "").lower()
                unit_family = {
                    "price": "price",
                    "currency": "price",
                    "usd": "price",
                }
                comparable_units = unit_family.get(left_unit, left_unit) == unit_family.get(right_unit, right_unit)
                if (left_unit and right_unit and not comparable_units) or (
                    not left_unit and not right_unit and left_type != right_type
                ):
                    raise ValueError(
                        f"Rule Set {rule_set.get('name')} compares incompatible Data Fields {source_id} and {right_source_id}"
                    )
                right_interval = normalize_interval_spec(condition.get("right_interval"))
                right_available = {
                    str(spec["unit"])
                    for value in right_output.get("available_intervals") or []
                    if (spec := normalize_interval_spec(value)) is not None
                }
                if right_available and (right_interval is None or right_interval["unit"] not in right_available):
                    raise ValueError(
                        f"Rule Set {rule_set.get('name')} requires a supported comparison interval for {right_source_id}"
                    )
                validate_aggregation(right_output, condition.get("right_aggregation"), f"Rule Set {rule_set.get('name')} comparison field {right_source_id}")
                if not right_available and right_interval is not None:
                    raise ValueError(
                        f"Rule Set {rule_set.get('name')} assigns an interval to non-interval comparison field {right_source_id}"
                    )
            elif comparator != "is_true":
                _validate_rule_constant(
                    output,
                    condition.get("value"),
                    f"Rule Set {rule_set.get('name')} field {source_id}",
                )
    for calculation in calculations:
        execution_scope = str(calculation.get("execution_scope") or "")
        if execution_scope not in DISCOVERY_EXECUTION_SCOPES:
            raise ValueError(
                f"QMD capability {calculation.get('name')} has unknown execution scope {execution_scope}"
            )
        allowed_scopes = {
            str(value) for value in calculation.get("allowed_scopes") or [] if str(value)
        }
        if execution_scope not in allowed_scopes:
            raise ValueError(
                f"QMD capability {calculation.get('name')} does not allow its default execution scope"
            )
        if allowed_scopes - DISCOVERY_EXECUTION_SCOPES:
            raise ValueError(
                f"QMD capability {calculation.get('name')} has unknown allowed execution scopes"
            )
        configuration_policy = str(calculation.get("configuration_policy") or "")
        if configuration_policy not in DISCOVERY_CONFIGURATION_POLICIES:
            raise ValueError(
                f"QMD capability {calculation.get('name')} has unknown configuration policy"
            )
        if execution_scope == "universal_ingest" and (
            configuration_policy != "locked"
            or not bool(calculation.get("system_required"))
            or not bool(calculation.get("enabled"))
        ):
            raise ValueError(
                f"Universal Ingest capability {calculation.get('name')} must remain enabled and locked"
            )
        if bool(calculation.get("system_required")) and not bool(calculation.get("enabled")):
            raise ValueError(
                f"Required QMD capability {calculation.get('name')} cannot be disabled"
            )
        if (
            execution_scope == "core_scan"
            and bool(calculation.get("enabled") or calculation.get("system_required"))
            and not list(calculation.get("scanner_columns") or [])
        ):
            raise ValueError(
                f"Active Core Scan capability {calculation.get('name')} has no registered scanner column"
            )
        supported_timeframes = {
            str(value) for value in calculation.get("timeframes") or [] if str(value)
        }
        selected_timeframes = [
            str(value)
            for value in calculation.get("selected_timeframes") or []
            if str(value)
        ]
        unknown_timeframes = set(selected_timeframes) - supported_timeframes
        if unknown_timeframes:
            raise ValueError(
                f"QMD capability {calculation.get('name')} selects unsupported calculation cadences: "
                + ", ".join(sorted(unknown_timeframes))
            )
        if bool(calculation.get("enabled")) and supported_timeframes and not selected_timeframes:
            raise ValueError(
                f"Enabled QMD capability {calculation.get('name')} requires at least one calculation cadence"
            )
    watchlists = list(section.get("watchlists") or [])
    if not watchlists:
        raise ValueError("Market Discovery requires at least one Watchlist")
    _unique_ids(watchlists, "watchlist_id", "Watchlist")
    serving = dict(dict(section.get("model_serving") or {}).get("bar_gpt") or {})
    if serving:
        trigger_mode = str(serving.get("trigger_mode") or "auto")
        if trigger_mode not in {"auto", "manual"}:
            raise ValueError("BarGPT model serving trigger_mode must be auto or manual")
        maximum_tickers = int(serving.get("maximum_tickers") or 0)
        if maximum_tickers < 1 or maximum_tickers > 5000:
            raise ValueError("BarGPT model serving maximum_tickers must be between 1 and 5000")
        unknown = set(serving.get("watchlist_ids") or []) - {
            str(row.get("watchlist_id") or "") for row in watchlists
        }
        if unknown:
            raise ValueError("BarGPT model serving references unknown Watchlists: " + ", ".join(sorted(unknown)))
    column_catalog = list(section.get("column_catalog") or [])
    columns_by_id = {
        str(row.get("column_id") or ""): row for row in column_catalog
    }
    column_ids = _unique_ids(column_catalog, "column_id", "Watchlist column")
    if not column_ids:
        raise ValueError("Market Discovery requires a Watchlist column catalog")
    for column in column_catalog:
        source_id = str(column.get("source_id") or "")
        source_kind = str(column.get("source_kind") or "data_field")
        field = field_by_source.get(source_id)
        field_ref = str(column.get("field_ref") or "")
        if source_kind == "data_field" and field_ref not in output_refs:
            raise ValueError(
                f"Watchlist column {column.get('column_id')} is not generated from a Data Field output"
            )
        if source_kind == "rule_set" and source_id not in rule_set_ids:
            raise ValueError(
                f"Rule-set column {column.get('column_id')} references unknown rule set {source_id}"
            )
        if source_kind not in {"data_field", "rule_set"}:
            raise ValueError(f"Column {column.get('column_id')} has unknown source kind {source_kind}")
    core_rules = set(core_scan.get("inclusion_rule_sets") or [])
    if core_rules - rule_set_ids:
        raise ValueError("Core Scan references unknown rule sets")
    if str(core_scan.get("ranking_field") or "") not in field_source_ids:
        raise ValueError("Core Scan references an unknown ranking data definition")
    if str(core_scan.get("ranking_field_ref") or "") not in output_refs:
        raise ValueError("Core Scan references an unknown ranking Data Field output")
    if str(core_scan.get("ranking_direction") or "descending") not in {"ascending", "descending"}:
        raise ValueError("Core Scan has an unknown ranking direction")
    if int(core_scan.get("maximum_size") or 0) <= 0:
        raise ValueError("Core Scan maximum rows must be positive")
    if set(core_scan.get("columns") or []) - column_ids:
        raise ValueError("Core Scan references unknown display columns")
    _unique_ids(list(section.get("classifications") or []), "classification_id", "Market classification")
    for watchlist in watchlists:
        availability = str(watchlist.get("availability") or "available")
        if availability not in {"available", "integration_pending"}:
            raise ValueError(f"Watchlist {watchlist.get('name')} has an unknown availability state")
        if availability == "integration_pending" and bool(watchlist.get("enabled")):
            raise ValueError(f"Watchlist {watchlist.get('name')} cannot be enabled until its upstream integration is available")
        if str(watchlist.get("source_scan_id") or "") != str(core_scan.get("scan_id")):
            raise ValueError(f"Watchlist {watchlist.get('name')} references an unknown Core Scan")
        if int(watchlist.get("maximum_size") or 0) <= 0:
            raise ValueError(f"Watchlist {watchlist.get('name')} maximum size must be positive")
        if int(watchlist.get("refresh_interval_ms") or 0) <= 0:
            raise ValueError(f"Watchlist {watchlist.get('name')} refresh interval must be positive")
        expiry = str(watchlist.get("membership_expiry") or "end_of_trading_day")
        if expiry not in {"end_of_trading_day", "time_to_live", "never"}:
            raise ValueError(f"Watchlist {watchlist.get('name')} has an unknown membership expiry policy")
        if int(watchlist.get("membership_ttl_ms") or 0) < 0:
            raise ValueError(f"Watchlist {watchlist.get('name')} membership TTL cannot be negative")
        if expiry == "time_to_live" and int(watchlist.get("membership_ttl_ms") or 0) <= 0:
            raise ValueError(f"Watchlist {watchlist.get('name')} membership TTL must be positive")
        if str(watchlist.get("ranking_field") or "") not in field_source_ids:
            raise ValueError(f"Watchlist {watchlist.get('name')} references an unknown ranking data definition")
        if str(watchlist.get("ranking_direction") or "descending") not in {"ascending", "descending"}:
            raise ValueError(f"Watchlist {watchlist.get('name')} has an unknown ranking direction")
        if str(watchlist.get("inclusion_operator") or "all") not in {"all", "any"}:
            raise ValueError(f"Watchlist {watchlist.get('name')} has unsupported inclusion logic")
        unknown_columns = set(watchlist.get("columns") or []) - column_ids
        if unknown_columns:
            raise ValueError(f"Watchlist {watchlist.get('name')} references unknown display columns")
        unknown_rules = (
            set(watchlist.get("inclusion_rule_sets") or [])
            | set(watchlist.get("exclusion_rule_sets") or [])
        ) - rule_set_ids
        if unknown_rules:
            raise ValueError(f"Watchlist {watchlist.get('name')} references unknown rule sets")
    signal_streams = list(section.get("signal_streams") or [])
    _unique_ids(signal_streams, "signal_stream_id", "Signal Stream")
    watchlist_ids = {
        str(watchlist.get("watchlist_id") or "") for watchlist in watchlists
    }
    for stream in signal_streams:
        stream_name = str(stream.get("name") or stream.get("signal_stream_id") or "Signal Stream")
        source_type = str(stream.get("source_type") or "core_scan")
        source_id = str(
            stream.get("source_id")
            or stream.get("source_scan_id")
            or core_scan.get("scan_id")
            or ""
        )
        if source_type == "core_scan":
            if source_id != str(core_scan.get("scan_id") or ""):
                raise ValueError(f"Signal Stream {stream_name} references an unknown Core Scan")
        elif source_type == "watchlist":
            if source_id not in watchlist_ids:
                raise ValueError(f"Signal Stream {stream_name} references an unknown Watchlist")
        elif source_type == "news_events":
            if source_id not in {"q_live.news_intelligence_events_v1", "q_live.news_synthesis_v1"}:
                raise ValueError(f"Signal Stream {stream_name} references an unknown News Intelligence source")
        else:
            raise ValueError(f"Signal Stream {stream_name} has an unknown source type")
        if str(stream.get("inclusion_operator") or "all") not in {"all", "any"}:
            raise ValueError(f"Signal Stream {stream_name} has unsupported inclusion logic")
        if str(stream.get("trigger_policy") or "false_to_true") != "false_to_true":
            raise ValueError(f"Signal Stream {stream_name} has an unknown trigger policy")
        if str(stream.get("occurrence_source") or "rule_evaluator") not in {
            "rule_evaluator",
            "qmd_live_market_state",
            "qmd_squeeze_episode",
        }:
            raise ValueError(f"Signal Stream {stream_name} has an unknown occurrence source")
        if str(stream.get("rearm_policy") or "after_false") not in {"after_false", "after_cooldown"}:
            raise ValueError(f"Signal Stream {stream_name} has an unknown rearm policy")
        if int(stream.get("refresh_interval_ms") or 0) <= 0:
            raise ValueError(f"Signal Stream {stream_name} refresh interval must be positive")
        if int(stream.get("cooldown_ms") or 0) < 0:
            raise ValueError(f"Signal Stream {stream_name} cooldown cannot be negative")
        if int(stream.get("maximum_events") or 0) <= 0:
            raise ValueError(f"Signal Stream {stream_name} maximum events must be positive")
        unknown_rules = set(stream.get("inclusion_rule_sets") or []) - rule_set_ids
        if unknown_rules:
            raise ValueError(f"Signal Stream {stream_name} references unknown rule sets")
        unknown_columns = set(stream.get("columns") or []) - column_ids
        if unknown_columns:
            raise ValueError(f"Signal Stream {stream_name} references unknown display columns")
        for route in stream.get("watchlist_routes") or []:
            watchlist_id = str(route.get("watchlist_id") or "")
            if watchlist_id not in watchlist_ids:
                raise ValueError(f"Signal Stream {stream_name} routes to unknown Watchlist {watchlist_id}")
            if source_type == "watchlist" and watchlist_id == source_id:
                raise ValueError(f"Signal Stream {stream_name} cannot route back into its source Watchlist")
            expiry = str(route.get("membership_expiry") or "end_of_trading_day")
            if expiry not in {"end_of_trading_day", "time_to_live", "never"}:
                raise ValueError(f"Signal Stream {stream_name} has an unknown admission expiry policy")
            if expiry == "time_to_live" and int(route.get("membership_ttl_ms") or 0) <= 0:
                raise ValueError(f"Signal Stream {stream_name} admission TTL must be positive")
    for composition in [core_scan, *watchlists, *signal_streams]:
        composition_name = str(
            composition.get("name")
            or composition.get("scan_id")
            or composition.get("watchlist_id")
            or composition.get("signal_stream_id")
            or "Market Discovery composition"
        )
        selected_columns = {str(value) for value in composition.get("columns") or []}
        bindings = {
            str(key): normalize_interval_spec(value)
            for key, value in dict(composition.get("column_intervals") or {}).items()
            if str(key) and str(value)
        }
        aggregation_bindings = {
            str(key): str(value)
            for key, value in dict(composition.get("column_aggregations") or {}).items()
            if str(key) and str(value)
        }
        if set(bindings) - selected_columns:
            raise ValueError(f"{composition_name} has interval bindings for unselected columns")
        if set(aggregation_bindings) - selected_columns:
            raise ValueError(f"{composition_name} has aggregation bindings for unselected columns")
        for column_id in selected_columns:
            available = {
                str(spec["unit"])
                for value in columns_by_id.get(column_id, {}).get("available_intervals") or []
                if (spec := normalize_interval_spec(value)) is not None
            }
            selected_interval = bindings.get(column_id)
            if available and (selected_interval is None or selected_interval["unit"] not in available):
                raise ValueError(f"{composition_name} requires an interval for column {column_id}")
            if not available and selected_interval is not None:
                raise ValueError(f"{composition_name} assigns an interval to non-interval column {column_id}")
            validate_aggregation(output_index.get(str(columns_by_id.get(column_id, {}).get("field_ref") or ""), {}), aggregation_bindings.get(column_id), f"{composition_name} column {column_id}")
        if "ranking_field_ref" in composition:
            ranking_output = output_index.get(str(composition.get("ranking_field_ref") or ""), {})
            available = {
                str(spec["unit"])
                for value in ranking_output.get("available_intervals") or []
                if (spec := normalize_interval_spec(value)) is not None
            }
            ranking_interval = normalize_interval_spec(composition.get("ranking_interval"))
            if available and (ranking_interval is None or ranking_interval["unit"] not in available):
                raise ValueError(f"{composition_name} requires a ranking interval")
            if not available and ranking_interval is not None:
                raise ValueError(f"{composition_name} assigns an interval to a non-interval ranking field")
            validate_aggregation(ranking_output, composition.get("ranking_aggregation"), f"{composition_name} ranking field")
    compiled_plan = compile_data_field_plan(section)
    stored_plan = dict(section.get("data_field_plan") or {})
    if stored_plan and str(stored_plan.get("content_hash") or "") != str(compiled_plan.get("content_hash") or ""):
        raise ValueError("Market Discovery Data Field plan is stale; save the configuration again")


def _compile_run_plans(
    candidate: dict[str, Any], *, canvas_profile_id: str = ""
) -> None:
    """Freeze user-authored Run Plans without copying deployment authority into Strategy."""

    discovery = dict(candidate.get("market_discovery") or {})
    watchlists = {
        str(row.get("watchlist_id") or ""): row
        for row in discovery.get("watchlists") or []
    }
    signal_streams = {
        str(row.get("signal_stream_id") or ""): row
        for row in discovery.get("signal_streams") or []
    }
    profiles = {
        str(row.get("profile_id") or ""): row
        for row in dict(candidate.get("strategy") or {}).get("profiles") or []
    }
    mandates = list(dict(candidate.get("portfolio") or {}).get("mandates") or [])
    calculations = list(discovery.get("calculation_catalog") or [])
    data_fields = list(discovery.get("data_fields") or [])
    action_policies = {
        str(row.get("policy_id") or ""): row
        for row in dict(candidate.get("trading_actions") or {}).get("policies") or []
    }
    rule_sets = {
        str(row.get("rule_set_id") or ""): row
        for row in discovery.get("rule_sets") or []
        if str(row.get("rule_set_id") or "")
    }
    plans = list(dict(candidate.get("run_plans") or {}).get("plans") or [])
    compiled_universes: list[dict[str, Any]] = []

    for run_plan in plans:
        run_plan_id = str(run_plan.get("run_plan_id") or "")
        profile = profiles.get(str(run_plan.get("profile_id") or ""))
        if profile is None:
            raise ValueError(f"Run Plan {run_plan_id} references an unknown Strategy Profile")
        selected_watchlists = [
            watchlists[watchlist_id]
            for watchlist_id in run_plan.get("watchlist_ids") or []
            if str(watchlist_id) in watchlists
        ]
        selected_signal_streams = [
            signal_streams[stream_id]
            for stream_id in run_plan.get("signal_stream_ids") or []
            if str(stream_id) in signal_streams
        ]
        if not selected_signal_streams:
            raise ValueError(f"Run Plan {run_plan_id} requires at least one Signal Stream")
        universe_id = f"run-plan-{run_plan_id}-candidates"
        included = {
            str(value).strip().upper()
            for watchlist in selected_watchlists
            for value in watchlist.get("manual_inclusions") or []
            if str(value).strip()
        }
        excluded = {
            str(value).strip().upper()
            for watchlist in selected_watchlists
            for value in watchlist.get("manual_exclusions") or []
            if str(value).strip()
        }
        universe = {
            "universe_id": universe_id,
            "name": " + ".join(
                str(row.get("name") or row.get("watchlist_id"))
                for row in selected_watchlists
            ) if selected_watchlists else " + ".join(
                str(row.get("name") or row.get("signal_stream_id"))
                for row in selected_signal_streams
            ),
            "description": (
                "Eligibility union of the Run Plan's QMD Watchlists; new Signal Stream occurrences activate evaluation."
                if selected_watchlists
                else "Symbols activate from new occurrences in the Run Plan's Signal Streams."
            ),
            "source": "watchlist" if selected_watchlists else "signal_stream",
            "symbols": sorted(included - excluded),
            "scanner_view_id": str(selected_watchlists[0].get("watchlist_id") or "") if selected_watchlists else "",
            "scanner_view_ids": [str(row.get("watchlist_id") or "") for row in selected_watchlists],
            "watchlist_snapshots": deepcopy(selected_watchlists),
            "signal_stream_ids": [str(row.get("signal_stream_id") or "") for row in selected_signal_streams],
            "signal_stream_snapshots": deepcopy(selected_signal_streams),
            "enabled": all(bool(row.get("enabled", True)) for row in [*selected_watchlists, *selected_signal_streams]),
        }
        compiled_universes.append(universe)
        run_plan["universe_id"] = universe_id
        if canvas_profile_id:
            # Legacy metadata remains readable, but is not runtime authority.
            run_plan["canvas_profile_id"] = canvas_profile_id
        run_plan["mandate_ids"] = [
            str(row.get("mandate_id") or "")
            for row in mandates
            if str(row.get("run_plan_id") or "") == run_plan_id
            and bool(row.get("enabled", True))
        ]
        policy_rule_set_ids = sorted({
            rule_set_id
            for policy_id in profile.get("action_policy_ids") or []
            for rule_set_id in action_policy_rule_set_ids(
                dict(action_policies.get(str(policy_id)) or {})
            )
        })
        run_plan["action_policy_rule_set_ids"] = policy_rule_set_ids
        referenced_rule_set_ids = sorted({
            *_profile_rule_set_ids(dict(profile.get("lifecycle") or {})),
            *policy_rule_set_ids,
            *[
                str(rule_set_id)
                for stream in selected_signal_streams
                for rule_set_id in stream.get("inclusion_rule_sets") or []
                if str(rule_set_id)
            ],
        })
        run_plan["observation_dependencies"] = _compiled_observation_dependencies(
            profile,
            calculations,
            [rule_sets[rule_set_id] for rule_set_id in referenced_rule_set_ids if rule_set_id in rule_sets],
            data_fields,
        )
        run_plan["compiled"] = True

    candidate["run_plans"] = {"universes": compiled_universes, "plans": plans}


def _compiled_observation_dependencies(
    profile: dict[str, Any],
    capability_catalog_rows: list[dict[str, Any]] | None = None,
    rule_sets: list[dict[str, Any]] | None = None,
    data_fields: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    definition = get_strategy_definition(
        str(profile.get("definition_id") or ""),
        int(profile.get("definition_revision") or 0) or None,
    )
    taxonomy = StrategyTaxonomy.from_payload(
        definition.get("taxonomy") or dict(definition.get("config") or {}).get("taxonomy")
    )
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for input_kind, refs in (("indicator", taxonomy.indicators), ("signal", taxonomy.signals)):
        for ref in refs:
            capability_key = ref.capability_key or ref.key
            producer = ref.producer or "strategy_payload"
            key = (producer, capability_key)
            row = grouped.setdefault(key, {
                "producer": producer,
                "capability_key": capability_key,
                "input_kinds": set(),
                "input_keys": set(),
                "timeframes": set(),
                "required": False,
            })
            row["input_kinds"].add(input_kind)
            row["input_keys"].add(ref.key)
            if ref.timeframe:
                row["timeframes"].add(ref.timeframe.lower())
            row["required"] = bool(row["required"] or ref.required)
    capability_by_field: dict[str, dict[str, Any]] = {}
    for capability in capability_catalog_rows or []:
        for field_id in capability.get("fields") or []:
            capability_by_field[str(field_id)] = capability
        capability_id = str(
            capability.get("capability_key")
            or capability.get("capability_id")
            or ""
        )
        if capability_id:
            capability_by_field.setdefault(capability_id, capability)
    output_index = data_field_output_index(data_fields or [])
    data_field_by_ref = {
        str(output.get("field_ref") or ""): data_field
        for data_field in data_fields or []
        for output in data_field.get("outputs") or []
        if str(output.get("field_ref") or "")
    }
    for rule_set in rule_sets or []:
        for condition in rule_set.get("conditions") or []:
            for side in ("left", "right"):
                field_ref = str(condition.get(f"{side}_field_ref") or "")
                output = output_index.get(field_ref, {})
                source_id = str(
                    output.get("source_id")
                    or condition.get(f"{side}_source_id")
                    or ""
                )
                if not source_id:
                    continue
                capability = capability_by_field.get(source_id, {})
                capability_key = str(
                    capability.get("capability_key")
                    or capability.get("capability_id")
                    or source_id
                )
                producer = str(
                    capability.get("owner")
                    or capability.get("provider")
                    or "qmd"
                ).lower()
                key = (producer, capability_key)
                row = grouped.setdefault(key, {
                    "producer": producer,
                    "capability_key": capability_key,
                    "input_kinds": set(),
                    "input_keys": set(),
                    "timeframes": set(),
                    "required": False,
                })
                row["input_kinds"].add("rule_set")
                row["input_keys"].add(source_id)
                data_field = data_field_by_ref.get(field_ref, {})
                interval = interval_expression(condition.get(f"{side}_interval"))
                if interval:
                    row["timeframes"].add(interval.lower())
                else:
                    row["timeframes"].update(
                        str(value).lower()
                        for value in dict(data_field.get("execution") or {}).get("producer_intervals") or []
                        if str(value)
                    )
                row["required"] = True
    compiled: list[dict[str, Any]] = []
    for _, row in sorted(grouped.items()):
        compiled.append({
            **row,
            "input_kinds": sorted(row["input_kinds"]),
            "input_keys": sorted(row["input_keys"]),
            "timeframes": sorted(row["timeframes"]),
        })
    return _with_qmd_dependency_metadata(compiled, capability_catalog_rows)


def _with_qmd_dependency_metadata(
    dependencies: list[dict[str, Any]],
    capability_catalog_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    capability_metadata = {
        str(row.get("capability_key") or row.get("capability_id") or ""): row
        for row in capability_catalog_rows or []
        if str(row.get("capability_key") or row.get("capability_id") or "")
    }
    result: list[dict[str, Any]] = []
    for dependency in dependencies:
        output = deepcopy(dependency)
        if str(output.get("producer") or "") == "qmd":
            metadata = capability_metadata.get(str(output.get("capability_key") or ""))
            warm_up_bars = metadata.get("warm_up_bars") if metadata else None
            output["warm_up"] = {
                "bars": int(warm_up_bars) if warm_up_bars is not None else None,
                "status": (
                    "required"
                    if warm_up_bars is not None and int(warm_up_bars) > 0
                    else "not_required"
                    if metadata is not None
                    else "catalog_unavailable"
                ),
            }
            output["capability_revision"] = (
                int(metadata.get("implementation_version") or 1)
                if metadata is not None
                else None
            )
        result.append(output)
    return result


def _validate_draft(draft: dict[str, Any], *, require_runtime_ready: bool = True) -> None:
    missing = CONFIGURATION_SECTIONS - set(draft)
    if missing:
        raise ValueError(f"Trading configuration is missing sections: {', '.join(sorted(missing))}")
    _validate_market_discovery(dict(draft["market_discovery"]))
    strategy = dict(draft["strategy"])
    profiles = list(strategy.get("profiles") or [])
    if not profiles:
        raise ValueError("At least one Strategy Profile is required")
    profile_ids = _unique_ids(profiles, "profile_id", "Strategy Profile")
    default_profile_id = str(strategy.get("default_profile_id") or "")
    if default_profile_id not in profile_ids:
        raise ValueError("The protected default Strategy Profile is required")
    discovery_rule_sets = list(dict(draft["market_discovery"]).get("rule_sets") or [])
    discovery_rule_set_ids = {
        str(row.get("rule_set_id") or "") for row in discovery_rule_sets
    }
    trading_actions = dict(draft["trading_actions"])
    validate_trading_actions(trading_actions, discovery_rule_set_ids)
    action_policy_ids = {
        str(row.get("policy_id") or "")
        for row in trading_actions.get("policies") or []
    }
    for profile in profiles:
        definition = get_strategy_definition(
            str(profile.get("definition_id") or ""),
            int(profile.get("definition_revision") or 0),
        )
        if not definition.get("enabled", True):
            raise ValueError(f"Strategy definition for {profile.get('name')} is disabled")
        registration = strategy_executor(
            str(profile.get("definition_id") or ""),
            int(profile.get("definition_revision") or 0),
        )
        if str(profile.get("profile_id")) == default_profile_id and not bool(
            profile.get("protected")
        ):
            raise ValueError("The default Strategy Profile must remain protected")
        lifecycle = dict(profile.get("lifecycle") or {})
        lifecycle_rule_set_ids = set(_profile_rule_set_ids(lifecycle))
        unknown_rule_sets = lifecycle_rule_set_ids - discovery_rule_set_ids
        if unknown_rule_sets:
            raise ValueError(
                f"Strategy Profile {profile.get('name')} references unknown rule sets: "
                f"{', '.join(sorted(unknown_rule_sets))}"
            )
        references = [str(value) for value in profile.get("action_policy_ids") or []]
        if len(references) != len(set(references)):
            raise ValueError(
                f"Strategy Profile {profile.get('name')} contains duplicate Action Policy references"
            )
        unknown_policies = set(references) - action_policy_ids
        if unknown_policies:
            raise ValueError(
                f"Strategy Profile {profile.get('name')} references unknown Action Policies: "
                f"{', '.join(sorted(unknown_policies))}"
            )
        rule_set_catalog = _profile_rule_sets(
            profile, dict(draft["market_discovery"])
        )
        _validate_strategy_lifecycle(
            lifecycle,
            rule_set_catalog,
            registration.parameter_resolver,
            dict(profile.get("parameters") or {}),
            {
                str(row.get("action_id") or ""): str(row.get("category") or "")
                for row in trading_actions.get("definitions") or []
            },
        )
        definition_config = dict(definition.get("config") or {})
        direction = str(definition_config.get("direction") or "")
        configured_side = str(dict(lifecycle.get("trading_behavior") or {}).get("side") or "")
        supported_sides = set(definition_config.get("supported_sides") or ["long"])
        if configured_side not in supported_sides:
            raise ValueError(
                f"Strategy Profile {profile.get('name')} does not support the {configured_side} side"
            )
        _parameters_with_action_policies(
            profile,
            rule_set_catalog,
            _profile_action_policies(profile, trading_actions),
        )

    accounts = list(dict(draft["accounts"]).get("bindings") or [])
    if not accounts:
        raise ValueError("At least one account/session binding is required")
    account_keys = _unique_ids(accounts, "account_key", "Account")
    for row in accounts:
        modes = set(row.get("modes") or [])
        if not modes or not modes <= SUPPORTED_MODES:
            raise ValueError(f"Account {row.get('account_key')} has unsupported runtime modes")
        if modes.intersection({"paper", "live"}):
            if str(row.get("source_account_id") or "").strip():
                raise ValueError(
                    f"Account {row.get('account_key')} must resolve its broker account id "
                    "server-side instead of storing source_account_id"
                )
            reference = str(row.get("source_account_env") or "").strip()
            if not reference:
                raise ValueError(
                    f"Account {row.get('account_key')} requires a server-side broker "
                    "account environment key for Paper or Live"
                )
            if require_runtime_ready and not _resolved_source_account_id(row):
                raise ValueError(
                    f"Account {row.get('account_key')} requires an exact broker account id "
                    f"resolved from {reference} for Paper or Live"
                )
        if not str(row.get("session_key") or "").strip():
            raise ValueError(f"Account {row.get('account_key')} requires a session key")

    policies = [portfolio_policy_from_payload(dict(row)) for row in dict(draft["portfolio"]).get("policies") or []]
    if not policies:
        raise ValueError("At least one portfolio/risk policy is required")
    policy_ids = {policy.policy_id for policy in policies}
    if len(policy_ids) != len(policies):
        raise ValueError("Portfolio policy ids must be unique")
    for row in accounts:
        if str(row.get("portfolio_policy_id") or "") not in policy_ids:
            raise ValueError(f"Account {row.get('account_key')} references an unknown portfolio policy")

    oms_profiles = list(dict(draft["oms"]).get("profiles") or [])
    oms_ids = _unique_ids(oms_profiles, "profile_id", "OMS profile")
    execution_policies = list(dict(draft["oms"]).get("execution_policies") or [])
    protection_profiles = list(dict(draft["oms"]).get("protection_profiles") or [])
    if not execution_policies or not protection_profiles:
        raise ValueError("OMS requires execution policies and protection profiles")
    execution_ids = _unique_ids(execution_policies, "policy_id", "Execution policy")
    protection_ids = _unique_ids(protection_profiles, "profile_id", "Protection profile")
    execution_references: set[str] = set()
    protection_references: set[str] = set()
    for row in execution_policies:
        policy = _validate_execution_policy_config(row)
        execution_references.update({policy.policy_id, policy.identity})
    for row in protection_profiles:
        profile = _validate_protection_profile_config(row)
        protection_references.update({profile.profile_id, profile.identity})
    for row in oms_profiles:
        _validate_oms_settings(dict(row.get("settings") or {}))
        settings = dict(row.get("settings") or {})
        for key in ("entry_execution_policy_id", "exit_execution_policy_id"):
            if str(settings.get(key) or "") not in execution_references:
                raise ValueError(f"OMS profile {row.get('name')} references unknown {key}")
        if str(settings.get("protection_profile_id") or "") not in protection_references:
            raise ValueError(f"OMS profile {row.get('name')} references an unknown protection profile")
    watchlist_ids = {
        str(row.get("watchlist_id") or "")
        for row in dict(draft["market_discovery"]).get("watchlists") or []
    }
    signal_stream_ids = {
        str(row.get("signal_stream_id") or "")
        for row in dict(draft["market_discovery"]).get("signal_streams") or []
    }
    for profile in profiles:
        lifecycle = dict(profile.get("lifecycle") or {})
        for intent in _lifecycle_order_intents(lifecycle):
            if str(intent.get("execution_policy") or "") not in execution_references:
                raise ValueError(f"Strategy Profile {profile.get('name')} references an unknown execution policy")
            protection_reference = str(intent.get("protection_profile") or "")
            if protection_reference and protection_reference not in protection_references:
                raise ValueError(f"Strategy Profile {profile.get('name')} references an unknown protection profile")
    for policy in policies:
        unknown_execution = set(policy.allowed_execution_policies) - {"*"} - execution_references
        unknown_protection = set(policy.allowed_protection_profiles) - {"*"} - protection_references
        if unknown_execution:
            raise ValueError(f"Portfolio policy {policy.identity} allows unknown execution policies")
        if unknown_protection:
            raise ValueError(f"Portfolio policy {policy.identity} allows unknown protection profiles")

    run_plans = list(dict(draft["run_plans"]).get("plans") or [])
    if require_runtime_ready and not run_plans:
        raise ValueError("At least one Strategy Run Plan is required")
    run_plan_ids = _unique_ids(run_plans, "run_plan_id", "Strategy Run Plan")
    universes = list(dict(draft["run_plans"]).get("universes") or [])
    if require_runtime_ready and not universes:
        raise ValueError("At least one Watch Universe is required")
    universe_ids = _unique_ids(universes, "universe_id", "Watch Universe")
    for universe in universes:
        source = str(universe.get("source") or "")
        if source not in {
            "configured_symbols",
            "scanner_view",
            "watchlist",
            "signal_stream",
        }:
            raise ValueError(
                f"Watch Universe {universe.get('name')} has an unsupported source"
            )
        if require_runtime_ready and source == "scanner_view":
            raise ValueError(
                f"Watch Universe {universe.get('name')} cannot be published until its {source} runtime resolver is available"
            )
    mandates = list(dict(draft["portfolio"]).get("mandates") or [])
    mandate_ids = _unique_ids(mandates, "mandate_id", "Strategy-account mandate")
    for mandate in mandates:
        principal_kind = str(mandate.get("principal_kind") or "strategy_deployment")
        if principal_kind not in {"session", "strategy_deployment"}:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} has an unsupported principal kind")
        if principal_kind == "strategy_deployment" and str(mandate.get("run_plan_id") or "") not in run_plan_ids:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} references an unknown Run Plan")
        if str(mandate.get("account_key") or "") not in account_keys:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} references an unknown account")
        fraction = float(mandate.get("maximum_cash_fraction") or 0)
        if not 0 < fraction <= 1:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} maximum cash must be between 0 and 100 percent")
        if str(mandate.get("assignment_mode") or "") not in {"single", "replicated", "weighted", "partitioned"}:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} has unsupported account assignment mode")
        if float(mandate.get("allocation_weight") or 0) <= 0:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} allocation weight must be positive")
        if str(mandate.get("maximum_action_authority") or "") not in {"manual", "confirm", "automatic"}:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} has unsupported maximum action authority")
    for run_plan in run_plans:
        environments = set(run_plan.get("allowed_environments") or [])
        if not environments or not environments <= SUPPORTED_MODES:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} has unsupported environments")
        if str(run_plan.get("profile_id") or "") not in profile_ids:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} references an unknown Strategy Profile")
        if str(run_plan.get("oms_profile_id") or "") not in oms_ids:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} references an unknown OMS profile")
        selected_watchlist_ids = {
            str(value) for value in run_plan.get("watchlist_ids") or [] if str(value)
        }
        unknown_watchlists = selected_watchlist_ids - watchlist_ids
        if unknown_watchlists:
            raise ValueError(
                f"Run Plan {run_plan.get('run_plan_id')} references unknown Watchlists: {', '.join(sorted(unknown_watchlists))}"
            )
        selected_signal_stream_ids = {
            str(value) for value in run_plan.get("signal_stream_ids") or [] if str(value)
        }
        if not selected_signal_stream_ids:
            raise ValueError(
                f"Run Plan {run_plan.get('run_plan_id')} requires at least one Signal Stream"
            )
        unknown_signal_streams = selected_signal_stream_ids - signal_stream_ids
        if unknown_signal_streams:
            raise ValueError(
                f"Run Plan {run_plan.get('run_plan_id')} references unknown Signal Streams: "
                + ", ".join(sorted(unknown_signal_streams))
            )
        activation = dict(run_plan.get("activation") or {})
        if str(activation.get("event_policy") or "") not in {
            "new_occurrences", "latest_session_occurrence"
        }:
            raise ValueError(
                f"Run Plan {run_plan.get('run_plan_id')} has an unsupported Signal Stream event policy"
            )
        if str(activation.get("watchlist_policy") or "") not in {
            "any_selected", "all_selected", "not_required"
        }:
            raise ValueError(
                f"Run Plan {run_plan.get('run_plan_id')} has an unsupported Watchlist eligibility policy"
            )
        enablement = dict(run_plan.get("enablement") or {})
        if str(enablement.get("state") or "") not in {"enabled", "disabled"}:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} enablement state is unsupported")
        if str(enablement.get("scope") or "") not in {"current_session", "persistent"}:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} enablement scope is unsupported")
        data_plan_ids = dict(run_plan.get("data_plan_ids") or {})
        if any(mode not in data_plan_ids for mode in environments):
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} requires a data plan for every enabled environment")
        if set(str(value) for value in data_plan_ids.values()) - set(_default_data_plan_ids().values()):
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} references an unknown data plan")
        if str(run_plan.get("source_revision_policy") or "") not in {"require_complete", "allow_partial"}:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} has an unsupported source revision policy")
        if str(run_plan.get("universe_id") or "") not in universe_ids:
            raise ValueError(
                f"Run Plan {run_plan.get('run_plan_id')} references an unknown Watch Universe"
            )
        if not str(run_plan.get("book_id") or "").strip():
            raise ValueError(
                f"Run Plan {run_plan.get('run_plan_id')} requires a portfolio book"
            )
        action_authority = dict(run_plan.get("action_authority") or {})
        if set(str(value) for value in action_authority.values()) - ACTION_AUTHORITIES:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} has unsupported action authority")
        if str(action_authority.get("default") or "") not in {"manual", "confirm", "automatic"}:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} requires a default action authority")
        for action in ("initial_entry", "add", "reentry", "strategic_exit"):
            if action != "reentry" and str(action_authority.get(action) or "") == "disabled":
                raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} cannot disable {action}")
        if str(action_authority.get("protective_exit")) != "automatic" or str(action_authority.get("emergency_exit")) != "automatic":
            raise ValueError("Protective and emergency exits must remain automatic")
        safety = dict(dict(run_plan.get("safety_supervisor") or {}).get("enabled_by_environment") or {})
        if not bool(safety.get("live", True)) or not bool(safety.get("paper", True)):
            raise ValueError("Trading Safety Supervisor cannot be disabled for Live or Paper")
        policy = _effective_campaign_policy(run_plan)
        validate_campaign_policy(policy)
        if require_runtime_ready and str(policy.get("initial_entry_authority") or "") == "automatic":
            universe = next(
                row for row in universes
                if str(row.get("universe_id")) == str(run_plan.get("universe_id"))
            )
            universe_symbols = {
                str(value).strip().upper()
                for value in universe.get("symbols") or []
                if str(value).strip()
            }
            identity_bound_symbols = {
                str(row.get("ticker") or "").strip().upper()
                for row in run_plan.get("runtime_assignments") or []
                if str(row.get("ticker") or "").strip() and int(row.get("conid") or 0) > 0
            }
            missing_identity = sorted(universe_symbols - identity_bound_symbols)
            if str(universe.get("source") or "") == "watchlist":
                missing_identity = []
            if missing_identity:
                raise ValueError(
                    f"Automatic Run Plan {run_plan.get('name')} requires identity-bound assignments for: {', '.join(missing_identity)}"
                )
        referenced_mandates = [
            row for row in mandates
            if str(row.get("run_plan_id")) == str(run_plan.get("run_plan_id"))
        ]
        if require_runtime_ready and not referenced_mandates:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} requires at least one account mandate")
        enabled_mandates = [row for row in referenced_mandates if bool(row.get("enabled", True))]
        assignment_modes = {str(row.get("assignment_mode") or "") for row in enabled_mandates}
        if len(assignment_modes) > 1:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} account mandates must use one assignment mode")
        if assignment_modes == {"single"} and len(enabled_mandates) != 1:
            raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} single assignment mode requires exactly one account")
        authority_rank = {"disabled": 0, "manual": 1, "confirm": 2, "automatic": 3}
        default_authority = str(action_authority.get("default") or "confirm")
        effective_authorities = [
            default_authority if str(action_authority.get(action) or "inherit") == "inherit" else str(action_authority.get(action))
            for action in ("initial_entry", "add", "reentry")
        ]
        for mandate in enabled_mandates:
            maximum_authority = str(mandate.get("maximum_action_authority") or "manual")
            if any(authority_rank.get(value, 99) > authority_rank[maximum_authority] for value in effective_authorities):
                raise ValueError(f"Run Plan {run_plan.get('run_plan_id')} exceeds mandate {mandate.get('mandate_id')} action authority cap")
        for assignment in run_plan.get("runtime_assignments") or []:
            if str(assignment.get("account_key") or "") not in account_keys:
                raise ValueError(f"Runtime assignment {assignment.get('assignment_id')} references an unknown account")
            if not str(assignment.get("ticker") or "").strip() or int(assignment.get("conid") or 0) <= 0:
                raise ValueError(f"Runtime assignment {assignment.get('assignment_id')} requires ticker and conid")
    sessions = dict(draft.get("sessions") or {})
    session_profiles = list(sessions.get("profiles") or [])
    routes = list(sessions.get("execution_routes") or [])
    deployments = list(sessions.get("strategy_deployments") or [])
    if require_runtime_ready and not session_profiles:
        raise ValueError("At least one Session Profile is required")
    session_profile_ids = _unique_ids(session_profiles, "session_profile_id", "Session Profile")
    route_ids = _unique_ids(routes, "execution_route_id", "Execution Route")
    deployment_ids = _unique_ids(deployments, "strategy_deployment_id", "Strategy Deployment")
    route_by_id = {str(row.get("execution_route_id") or ""): row for row in routes}
    profile_by_id = {str(row.get("session_profile_id") or ""): row for row in session_profiles}
    account_by_key = {str(row.get("account_key") or ""): row for row in accounts}
    mandate_by_id = {str(row.get("mandate_id") or ""): row for row in mandates}
    for profile in session_profiles:
        modes = set(profile.get("modes") or [])
        if not modes or not modes <= SUPPORTED_MODES:
            raise ValueError(f"Session Profile {profile.get('name')} has unsupported modes")
        references = {str(value) for value in profile.get("execution_route_ids") or []}
        if references - route_ids:
            raise ValueError(f"Session Profile {profile.get('name')} references unknown execution routes")
        default_route_id = str(profile.get("default_execution_route_id") or "")
        if default_route_id and default_route_id not in references:
            raise ValueError(f"Session Profile {profile.get('name')} has an invalid default execution route")
    for route in routes:
        route_profile_id = str(route.get("session_profile_id") or "")
        if route_profile_id not in session_profile_ids:
            raise ValueError(f"Execution Route {route.get('name')} references an unknown Session Profile")
        route_id = str(route.get("execution_route_id") or "")
        if route_id not in {str(value) for value in profile_by_id[route_profile_id].get("execution_route_ids") or []}:
            raise ValueError(f"Execution Route {route.get('name')} is not owned by its Session Profile")
        account_key = str(route.get("account_key") or "")
        if account_key not in account_keys:
            raise ValueError(f"Execution Route {route.get('name')} references an unknown account")
        route_modes = set(route.get("modes") or [])
        if not route_modes:
            raise ValueError(f"Execution Route {route.get('name')} requires at least one mode")
        if not route_modes <= set(profile_by_id[route_profile_id].get("modes") or []):
            raise ValueError(f"Execution Route {route.get('name')} has modes outside its Session Profile")
        if str(route.get("oms_profile_id") or "") not in oms_ids:
            raise ValueError(f"Execution Route {route.get('name')} references an unknown OMS profile")
        mandate_id = str(route.get("portfolio_mandate_id") or "")
        if mandate_id not in mandate_ids:
            raise ValueError(f"Execution Route {route.get('name')} references an unknown Portfolio mandate")
        mandate = mandate_by_id[mandate_id]
        if str(mandate.get("account_key") or "") != account_key:
            raise ValueError(f"Execution Route {route.get('name')} account and Portfolio mandate disagree")
        if str(mandate.get("principal_kind") or "") != "session" or str(mandate.get("principal_id") or "") != route_profile_id:
            raise ValueError(f"Execution Route {route.get('name')} requires a mandate owned by its Session Profile")
    for deployment in deployments:
        deployment_id = str(deployment.get("strategy_deployment_id") or "")
        if str(deployment.get("run_plan_id") or "") not in run_plan_ids:
            raise ValueError(f"Strategy Deployment {deployment_id} references an unknown Run Plan")
        if str(deployment.get("session_profile_id") or "") not in session_profile_ids:
            raise ValueError(f"Strategy Deployment {deployment_id} references an unknown Session Profile")
        deployment_route_ids = {str(value) for value in deployment.get("execution_route_ids") or []}
        if deployment_route_ids - route_ids:
            raise ValueError(f"Strategy Deployment {deployment_id} references unknown execution routes")
        deployment_profile_id = str(deployment.get("session_profile_id") or "")
        if any(str(route_by_id[route_id].get("session_profile_id") or "") != deployment_profile_id for route_id in deployment_route_ids):
            raise ValueError(f"Strategy Deployment {deployment_id} mixes Execution Routes from another Session Profile")
        if not set(deployment.get("modes") or []) <= set(profile_by_id[deployment_profile_id].get("modes") or []):
            raise ValueError(f"Strategy Deployment {deployment_id} has modes outside its Session Profile")
        deployment_mandate_ids = {str(value) for value in deployment.get("portfolio_mandate_ids") or []}
        if deployment_mandate_ids - mandate_ids:
            raise ValueError(f"Strategy Deployment {deployment_id} requires at least one account mandate")
        deployment_mandates = [mandate_by_id[mandate_id] for mandate_id in deployment_mandate_ids]
        if bool(deployment.get("enabled", True)) and not deployment_mandates:
            raise ValueError(f"Strategy Deployment {deployment_id} requires at least one account mandate")
        route_account_keys = {str(route_by_id[route_id].get("account_key") or "") for route_id in deployment_route_ids}
        if {str(row.get("account_key") or "") for row in deployment_mandates} != route_account_keys:
            raise ValueError(f"Strategy Deployment {deployment_id} routes and Portfolio mandates disagree")
        if any(
            str(row.get("principal_kind") or "") != "strategy_deployment"
            or str(row.get("principal_id") or "") != deployment_id
            for row in deployment_mandates
        ):
            raise ValueError(f"Strategy Deployment {deployment_id} requires mandates owned by that deployment")
    known_principals = session_profile_ids | deployment_ids
    for mandate in mandates:
        principal_id = str(mandate.get("principal_id") or "")
        if principal_id and principal_id not in known_principals:
            raise ValueError(f"Mandate {mandate.get('mandate_id')} references an unknown execution principal")
    if require_runtime_ready:
        legacy_mandate_pairs = {
            (str(row.get("account_key") or ""), str(row.get("run_plan_id") or ""))
            for row in mandates
            if bool(row.get("enabled", True)) and str(row.get("run_plan_id") or "")
        }
        for account in accounts:
            if not bool(account.get("enabled", True)):
                continue
            for mode in account.get("modes") or []:
                eligible = any(
                    bool(route.get("enabled", True))
                    and str(route.get("account_key") or "") == str(account.get("account_key") or "")
                    and mode in set(
                        next(
                            row.get("modes") or []
                            for row in session_profiles
                            if str(row.get("session_profile_id") or "") == str(route.get("session_profile_id") or "")
                        )
                    )
                    for route in routes
                ) or any(
                    bool(run_plan.get("enabled", True))
                    and mode in set(run_plan.get("allowed_environments") or [])
                    and (str(account.get("account_key") or ""), str(run_plan.get("run_plan_id") or "")) in legacy_mandate_pairs
                    for run_plan in run_plans
                )
                if not eligible:
                    raise ValueError(
                        f"Account {account.get('account_key')} requires an enabled {mode} Execution Route"
                    )
    for raw in dict(draft["portfolio"]).get("groups") or []:
        group = PortfolioGroupPolicy(
            group_id=str(raw.get("group_id") or ""),
            account_keys=tuple(str(value) for value in raw.get("account_keys") or ()),
            maximum_gross_exposure=float(raw.get("maximum_gross_exposure") or 0),
            maximum_ticker_exposure=float(raw.get("maximum_ticker_exposure") or 0),
        )
        if set(group.account_keys) - account_keys:
            raise ValueError(f"Portfolio group {group.group_id} references unknown accounts")


def merged_assignment_parameters(configuration: dict[str, Any], assignment: dict[str, Any]) -> dict[str, Any]:
    base = deepcopy(dict(configuration["strategy"].get("parameters") or {}))
    _deep_merge(base, dict(assignment.get("parameters") or {}))
    campaign_policy = dict(configuration.get("campaign_policy") or {})
    reentry = base.setdefault("reentry", {})
    if campaign_policy:
        reentry["maximum_attempts"] = min(
            int(reentry.get("maximum_attempts") or 0),
            int(campaign_policy.get("maximum_reentries") or 0),
        )
        reentry["cooldown_ms"] = max(
            int(reentry.get("cooldown_ms") or 0),
            int(campaign_policy.get("reentry_cooldown_ms") or 0),
        )
        reentry["enabled"] = (
            bool(reentry.get("enabled", True))
            and str(campaign_policy.get("reentry_authority")) != "disabled"
        )
    oms_contract = dict(configuration["oms"])
    oms = dict(oms_contract.get("settings") or oms_contract)
    execution_policies = list(oms_contract.get("execution_policies") or [])
    protection_profiles = list(oms_contract.get("protection_profiles") or [])
    base["execution_policy_catalog"] = _policy_catalog_payload(
        execution_policies, "policy_id"
    )
    base["protection_profile_catalog"] = _policy_catalog_payload(
        protection_profiles, "profile_id"
    )
    execution = base.setdefault("execution", {})
    execution.update({
        "entry_urgency": oms["entry_urgency"],
        "exit_urgency": oms["exit_urgency"],
        "limit_offset_bps": float(oms["limit_offset_bps"]),
        "tick_size": float(oms["tick_size"]),
    })
    protection = dict(oms.get("protection") or {})
    stop = base.setdefault("protection", {}).setdefault("stop", {})
    stop.update({
        "method": protection["stop_method"],
        "structure_buffer_bps": float(protection.get("structure_buffer_bps") or 0),
        "volatility_multiple": float(protection.get("volatility_multiple") or 0),
        "maximum_risk_pct": float(protection.get("maximum_risk_pct") or 0),
    })
    trailing = base["protection"].setdefault("trailing", {})
    # OMS may forbid trailing protection, but it must not re-enable a trailing
    # strategy exit that the selected Strategy profile explicitly disabled.
    trailing["enabled"] = bool(trailing.get("enabled", True)) and bool(
        protection.get("trailing_enabled", True)
    )
    identity = dict(configuration.get("strategy") or {})
    return strategy_executor(
        str(identity.get("strategy_id") or assignment.get("strategy_id") or ""),
        int(identity.get("revision") or assignment.get("strategy_revision") or 0),
    ).parameter_resolver(base)


def _policy_catalog_payload(
    rows: list[dict[str, Any]],
    identity_key: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = deepcopy(row)
        policy_id = str(row.get(identity_key) or "")
        revision = int(row.get("revision") or 1)
        references = {policy_id, f"{policy_id}@{revision}"}
        for reference in references - {""}:
            result[reference] = payload
    return result


def _migrate_market_discovery_instances(
    discovery: dict[str, Any],
    *,
    legacy_column_catalog: list[dict[str, Any]],
    legacy_data_fields: list[dict[str, Any]],
) -> None:
    """Move v28 interval variants into Rule Set and composition instances."""

    columns = list(discovery.get("column_catalog") or [])
    columns_by_id = {str(row.get("column_id") or ""): row for row in columns}
    columns_by_source = {
        str(row.get("source_id") or ""): row
        for row in columns
        if str(row.get("source_kind") or "data_field") == "data_field"
    }
    legacy_outputs = {
        str(output.get("field_ref") or ""): {
            **dict(output),
            "interval": str(dict(data_field.get("context") or {}).get("interval") or output.get("context_interval") or ""),
        }
        for data_field in legacy_data_fields
        for output in data_field.get("outputs") or []
    }
    legacy_columns = {
        str(row.get("column_id") or ""): row for row in legacy_column_catalog
    }

    def preferred(values: list[str]) -> str:
        for value in ("1m", "5m", "1s", "10s", "30s", "1h", "100ms"):
            if value in values:
                return value
        return values[0] if values else ""

    compositions = [
        discovery.setdefault("core_scan", {}),
        *list(discovery.get("watchlists") or []),
        *list(discovery.get("signal_streams") or []),
    ]
    for composition in compositions:
        migrated_columns: list[str] = []
        bindings = {
            str(key): str(value)
            for key, value in dict(composition.get("column_intervals") or {}).items()
            if str(key) and str(value)
        }
        for legacy_column_id in composition.get("columns") or []:
            old_id = str(legacy_column_id)
            old_column = legacy_columns.get(old_id, {})
            legacy_output = legacy_outputs.get(str(old_column.get("field_ref") or ""), {})
            source_id = str(old_column.get("source_id") or legacy_output.get("source_id") or "")
            new_column = columns_by_id.get(old_id) or columns_by_source.get(source_id)
            if new_column is None:
                continue
            new_id = str(new_column.get("column_id") or "")
            if new_id and new_id not in migrated_columns:
                migrated_columns.append(new_id)
            available = [str(value) for value in new_column.get("available_intervals") or []]
            interval = str(old_column.get("interval") or legacy_output.get("interval") or "")
            if available:
                bindings[new_id] = interval if interval in available else preferred(available)
        composition["columns"] = migrated_columns
        composition["column_intervals"] = {
            key: value for key, value in bindings.items() if key in migrated_columns
        }
        ranking_ref = str(composition.get("ranking_field_ref") or "")
        legacy_ranking = legacy_outputs.get(ranking_ref, {})
        if legacy_ranking.get("interval"):
            composition["ranking_interval"] = str(legacy_ranking["interval"])
        else:
            composition.pop("ranking_interval", None)


def _normalize_market_discovery_interval_specs(discovery: dict[str, Any]) -> None:
    """Normalize use-site timing and typed event-window aggregation bindings."""

    output_index = data_field_output_index(list(discovery.get("data_fields") or []))
    columns = {str(row.get("column_id") or ""): row for row in discovery.get("column_catalog") or []}

    def normalized_aggregation(output: dict[str, Any], value: Any) -> str:
        contract = dict(output.get("aggregation") or {})
        if str(contract.get("mode") or "none") != "required":
            return ""
        selected = str(value or "")
        allowed = [str(item) for item in contract.get("allowed") or []]
        return selected if selected in allowed else str(contract.get("default") or (allowed[0] if allowed else ""))

    for rule_set in discovery.get("rule_sets") or []:
        for condition in rule_set.get("conditions") or []:
            condition.pop("left_value_selection", None)
            condition.pop("right_value_selection", None)
            for key in ("left_interval", "right_interval"):
                normalized = normalize_interval_spec(condition.get(key))
                if normalized is None:
                    condition.pop(key, None)
                else:
                    condition[key] = normalized
            for side in ("left", "right"):
                output = output_index.get(str(condition.get(f"{side}_field_ref") or ""), {})
                aggregation = normalized_aggregation(output, condition.get(f"{side}_aggregation"))
                if aggregation:
                    condition[f"{side}_aggregation"] = aggregation
                else:
                    condition.pop(f"{side}_aggregation", None)
    for composition in [
        discovery.get("core_scan") or {},
        *list(discovery.get("watchlists") or []),
        *list(discovery.get("signal_streams") or []),
    ]:
        composition["column_intervals"] = {
            str(key): normalized
            for key, value in dict(composition.get("column_intervals") or {}).items()
            if str(key) and (normalized := normalize_interval_spec(value)) is not None
        }
        composition["column_aggregations"] = {
            column_id: aggregation
            for column_id in (str(value) for value in composition.get("columns") or [])
            if (aggregation := normalized_aggregation(
                output_index.get(str(columns.get(column_id, {}).get("field_ref") or ""), {}),
                dict(composition.get("column_aggregations") or {}).get(column_id),
            ))
        }
        ranking = normalize_interval_spec(composition.get("ranking_interval"))
        if ranking is None:
            composition.pop("ranking_interval", None)
        else:
            composition["ranking_interval"] = ranking
        ranking_output = output_index.get(str(composition.get("ranking_field_ref") or ""), {})
        ranking_aggregation = normalized_aggregation(ranking_output, composition.get("ranking_aggregation"))
        if ranking_aggregation:
            composition["ranking_aggregation"] = ranking_aggregation
        else:
            composition.pop("ranking_aggregation", None)


def _migrate_draft(raw: dict[str, Any]) -> dict[str, Any]:
    source_schema_version = int(raw.get("schema_version") or 0)
    legacy_discovery = deepcopy(raw.get("market_discovery") or {})
    legacy_column_catalog = list(legacy_discovery.get("column_catalog") or [])
    if (
        isinstance(raw.get("run_plans"), dict)
        or isinstance(raw.get("assignments"), dict)
    ) and isinstance(raw.get("strategy"), dict):
        result = deepcopy(raw)
        defaults = _default_draft()
        result["schema_version"] = CONFIGURATION_SCHEMA_VERSION
        result["trading_actions"] = deepcopy(
            result.get("trading_actions") or defaults["trading_actions"]
        )
        result["trading_actions"]["definitions"] = deepcopy(
            defaults["trading_actions"]["definitions"]
        )
        default_policies = {
            str(row.get("policy_id") or ""): deepcopy(row)
            for row in defaults["trading_actions"]["policies"]
        }
        for row in result["trading_actions"].get("policies") or []:
            policy_id = str(row.get("policy_id") or "")
            if policy_id:
                default_policies[policy_id] = deepcopy(row)
        result["trading_actions"]["policies"] = list(default_policies.values())
        result["market_discovery"] = deepcopy(
            result.get("market_discovery") or defaults["market_discovery"]
        )
        result["market_discovery"]["model_serving"] = deepcopy(
            result["market_discovery"].get("model_serving")
            or defaults["market_discovery"].get("model_serving")
            or {}
        )
        result["market_discovery"]["classifications"] = deepcopy(
            defaults["market_discovery"].get("classifications") or []
        )
        result["market_discovery"]["field_catalog"] = deepcopy(
            defaults["market_discovery"].get("field_catalog") or []
        )
        result["market_discovery"]["column_catalog"] = deepcopy(
            defaults["market_discovery"].get("column_catalog") or []
        )
        current_rule_sets = {
            str(row.get("rule_set_id") or ""): deepcopy(row)
            for row in result["market_discovery"].get("rule_sets") or []
        }
        merged_default_rule_sets: list[dict[str, Any]] = []
        for default_rule_set in defaults["market_discovery"].get("rule_sets") or []:
            rule_set_id = str(default_rule_set.get("rule_set_id") or "")
            merged = {**default_rule_set, **current_rule_sets.pop(rule_set_id, {})}
            if bool(default_rule_set.get("protected")):
                # Built-in Rule Sets are read-only registry projections. Keep
                # their executable semantics and availability aligned with the
                # current code instead of retaining stale session copies.
                for key in (
                    "name", "description", "enabled", "implementation_status",
                    "publication_status", "operator", "required_score", "conditions",
                ):
                    merged[key] = deepcopy(default_rule_set.get(key))
            merged_default_rule_sets.append(merged)
        result["market_discovery"]["rule_sets"] = merged_default_rule_sets + list(current_rule_sets.values())
        default_rule_set_ids = {
            str(row.get("rule_set_id") or "")
            for row in defaults["market_discovery"].get("rule_sets") or []
        }
        for rule_set in result["market_discovery"]["rule_sets"]:
            _normalize_data_rule_set_metadata(
                rule_set,
                atomic=str(rule_set.get("rule_set_id") or "") in default_rule_set_ids,
            )
            _normalize_rule_set_conditions(rule_set)
        if source_schema_version < 40:
            synthesis_decision_replacements = {
                "news.forecast_trigger_eligible": "news.deepfm.forecast_eligible",
                "news.composite_sentiment": "news.llm.language_sentiment",
                "news.positive_strength": "news.llm.positive_implication_probability",
                "news.negative_strength": "news.llm.negative_implication_probability",
            }
            for rule_set in result["market_discovery"]["rule_sets"]:
                if bool(rule_set.get("protected")):
                    continue
                migrated = False
                for condition in rule_set.get("conditions") or []:
                    for key in ("left_field_ref", "left_source_id", "right_field_ref", "right_source_id"):
                        previous = str(condition.get(key) or "")
                        replacement = synthesis_decision_replacements.get(previous)
                        if replacement:
                            condition[key] = replacement
                            migrated = True
                    previous_left = str(condition.get("left_source_id") or condition.get("left_field_ref") or "")
                    if previous_left in {
                        "news.llm.positive_implication_probability",
                        "news.llm.negative_implication_probability",
                    } and isinstance(condition.get("value"), (int, float)):
                        condition["value"] = min(1.0, max(0.0, float(condition["value"]) / 3.0))
                if migrated:
                    rule_set["enabled"] = False
                    rule_set["implementation_status"] = "requires_operator_review"
                    rule_set["publication_status"] = "draft"
        result["market_discovery"]["column_catalog"] = _watchlist_column_catalog(
            list(result["market_discovery"].get("field_catalog") or []),
            list(result["market_discovery"].get("rule_sets") or []),
        )
        default_calculations = list(defaults["market_discovery"].get("calculation_catalog") or [])
        current_calculations = {
            str(row.get("capability_id") or ""): deepcopy(row)
            for row in [
                *list(result["market_discovery"].get("calculation_catalog") or []),
                *list(dict(result["market_discovery"].get("core_scan") or {}).pop("calculations", []) or []),
            ]
        }
        merged_calculations: list[dict[str, Any]] = []
        for default_calculation in default_calculations:
            capability_id = str(default_calculation.get("capability_id") or "")
            calculation = {**default_calculation, **current_calculations.pop(capability_id, {})}
            calculation.update({
                "name": str(default_calculation.get("name") or capability_id),
                "description": str(default_calculation.get("description") or ""),
                "category": str(default_calculation.get("category") or ""),
                "provider": str(default_calculation.get("provider") or "QMD"),
                "output_type": str(default_calculation.get("output_type") or "number"),
                "capability_type": str(default_calculation.get("capability_type") or "indicator"),
                "priority": str(default_calculation.get("priority") or "p2"),
                "availability": str(default_calculation.get("availability") or "implemented"),
                "inputs": list(default_calculation.get("inputs") or []),
                "fields": list(default_calculation.get("fields") or []),
                "calculation": str(default_calculation.get("calculation") or default_calculation.get("description") or ""),
                "timeframes": list(default_calculation.get("timeframes") or []),
                "configurable": bool(default_calculation.get("configurable")),
                "system_required": bool(default_calculation.get("system_required")),
                "tier": str(default_calculation.get("tier") or "core"),
                "execution_scope": str(default_calculation.get("execution_scope") or "core_scan"),
                "allowed_scopes": list(default_calculation.get("allowed_scopes") or []),
                "configuration_policy": str(default_calculation.get("configuration_policy") or "generated"),
                "implementation_status": str(default_calculation.get("implementation_status") or default_calculation.get("availability") or "implemented"),
                "operational_status": str(default_calculation.get("operational_status") or "unknown"),
                "coverage_status": str(default_calculation.get("coverage_status") or "unknown"),
                "cost_class": str(default_calculation.get("cost_class") or "unknown"),
                "stateful": bool(default_calculation.get("stateful")),
                "scanner_columns": deepcopy(default_calculation.get("scanner_columns") or []),
                "consumers": deepcopy(default_calculation.get("consumers") or []),
            })
            supported_timeframes = list(calculation["timeframes"])
            selected_timeframes = [
                str(value)
                for value in calculation.get("selected_timeframes") or supported_timeframes
                if str(value) in supported_timeframes
            ]
            calculation["selected_timeframes"] = selected_timeframes or supported_timeframes
            if calculation["system_required"]:
                calculation["enabled"] = True
            merged_calculations.append(calculation)
        for calculation in current_calculations.values():
            supported_timeframes = [
                str(value) for value in calculation.get("timeframes") or [] if str(value)
            ]
            selected_timeframes = [
                str(value)
                for value in calculation.get("selected_timeframes") or supported_timeframes
                if str(value) in supported_timeframes
            ]
            calculation["selected_timeframes"] = selected_timeframes or supported_timeframes
            merged_calculations.append(calculation)
        result["market_discovery"]["calculation_catalog"] = merged_calculations
        legacy_data_fields = list(result["market_discovery"].get("data_fields") or [])
        generated_data_fields = build_data_field_catalog(
            merged_calculations,
            list(result["market_discovery"].get("field_catalog") or []),
        )
        # Data Fields are a read-only projection of the registered producer
        # authorities.  Intervals and presentation choices live at Rule Set or
        # Market Discovery use sites, so stale saved definitions must never
        # override regenerated source, calculation, output, or availability
        # contracts.
        merged_data_fields = generated_data_fields
        validate_data_field_catalog(merged_data_fields)
        result["market_discovery"]["data_fields"] = merged_data_fields
        result["market_discovery"]["atomic_fields"] = atomic_field_catalog(
            value
            for row in merged_calculations
            for value in row.get("inputs") or []
            if str(value)
        )
        migrate_rule_set_field_refs(
            result["market_discovery"]["rule_sets"],
            merged_data_fields,
            legacy_data_fields=legacy_data_fields,
        )
        result["market_discovery"]["column_catalog"] = build_column_catalog(
            merged_data_fields, result["market_discovery"]["rule_sets"]
        )
        if source_schema_version < CONFIGURATION_SCHEMA_VERSION:
            _migrate_market_discovery_instances(
                result["market_discovery"],
                legacy_column_catalog=legacy_column_catalog,
                legacy_data_fields=legacy_data_fields,
            )
        _normalize_market_discovery_interval_specs(result["market_discovery"])
        core_scan = result["market_discovery"]["core_scan"]
        default_core_scan = defaults["market_discovery"]["core_scan"]
        # Core Scan is a protected system surface. Keep its descriptive
        # metadata aligned with the current registry contract while retaining
        # user-configurable selection, ranking, and presentation state.
        core_scan["name"] = str(default_core_scan.get("name") or "Core Scan")
        core_scan["description"] = str(default_core_scan.get("description") or "")
        core_scan.setdefault("inclusion_rule_sets", list(default_core_scan.get("inclusion_rule_sets") or []))
        core_scan.setdefault("inclusion_operator", "all")
        core_scan.setdefault("ranking_field", str(default_core_scan.get("ranking_field") or "market.liquidity_rank"))
        core_scan.setdefault("ranking_direction", "descending")
        core_scan.setdefault("maximum_size", int(default_core_scan.get("maximum_size") or 250))
        core_scan.setdefault("columns", list(default_core_scan.get("columns") or []))
        default_watchlists = list(defaults["market_discovery"].get("watchlists") or [])
        current_watchlists = {
            str(row.get("watchlist_id") or ""): deepcopy(row)
            for row in result["market_discovery"].get("watchlists") or []
        }
        merged_watchlists: list[dict[str, Any]] = []
        for default_watchlist in default_watchlists:
            watchlist_id = str(default_watchlist.get("watchlist_id") or "")
            current_watchlist = current_watchlists.pop(watchlist_id, {})
            merged = {**default_watchlist, **current_watchlist}
            if bool(default_watchlist.get("template")) and str(default_watchlist.get("origin")) == "system":
                # System template names and descriptions document the current
                # rule semantics; a persisted draft must not pin stale copy.
                merged["name"] = str(default_watchlist.get("name") or watchlist_id)
                merged["description"] = str(default_watchlist.get("description") or "")
                previous_availability = str(current_watchlist.get("availability") or "")
                merged["availability"] = default_watchlist.get("availability", "available")
                merged["availability_detail"] = default_watchlist.get("availability_detail", "")
                if previous_availability == "integration_pending" and merged["availability"] == "available":
                    merged["enabled"] = bool(default_watchlist.get("enabled", True))
            merged_watchlists.append(merged)
        merged_watchlists.extend(current_watchlists.values())
        result["market_discovery"]["watchlists"] = merged_watchlists
        current_signal_stream_rows = [
            deepcopy(row)
            for row in result["market_discovery"].get("signal_streams") or []
            if str(row.get("signal_stream_id") or "")
        ]
        defaults_by_stream_id = {
            str(row.get("signal_stream_id") or ""): row
            for row in defaults["market_discovery"].get("signal_streams") or []
        }
        merged_signal_streams: list[dict[str, Any]] = []
        consumed_stream_ids: set[str] = set()
        # Preserve the user's visible ordering. Protected definitions keep the
        # current executable contract, but are not moved ahead of user streams.
        for current_stream in current_signal_stream_rows:
            stream_id = str(current_stream.get("signal_stream_id") or "")
            default_stream = defaults_by_stream_id.get(stream_id)
            if default_stream is None:
                merged_signal_streams.append(current_stream)
                continue
            merged = {**default_stream, **current_stream}
            if bool(default_stream.get("protected")):
                for key in (
                    "revision", "name", "description", "origin", "protected",
                    "source_type", "source_id", "source_scan_id",
                    "occurrence_source", "episode_role", "episode_ttl_ms",
                    "inclusion_rule_sets", "inclusion_operator",
                    "columns", "column_labels", "column_intervals", "column_aggregations",
                    "refresh_interval_ms", "trigger_policy", "rearm_policy",
                    "cooldown_ms", "maximum_events", "watchlist_routes",
                ):
                    merged[key] = deepcopy(default_stream.get(key))
            merged_signal_streams.append(merged)
            consumed_stream_ids.add(stream_id)
        for default_stream in defaults["market_discovery"].get("signal_streams") or []:
            stream_id = str(default_stream.get("signal_stream_id") or "")
            if stream_id not in consumed_stream_ids:
                merged_signal_streams.append(deepcopy(default_stream))
        result["market_discovery"]["signal_streams"] = merged_signal_streams
        column_ids = {
            str(row.get("column_id") or "")
            for row in result["market_discovery"].get("column_catalog") or []
        }
        core_scan["columns"] = [
            column_id
            for raw_column_id in core_scan.get("columns") or []
            if (column_id := (
                "execution_vwap" if str(raw_column_id) == "vwap" else str(raw_column_id)
            )) in column_ids
        ]
        if source_schema_version < 39:
            core_columns = list(core_scan.get("columns") or [])
            for column_id in ("liquidity_score", "liquidity_rank"):
                if column_id in column_ids and column_id not in core_columns:
                    core_columns.append(column_id)
            core_scan["columns"] = core_columns
        for watchlist in result["market_discovery"].get("watchlists") or []:
            watchlist.setdefault("membership_expiry", "end_of_trading_day")
            watchlist.setdefault("inclusion_operator", "all")
            watchlist.setdefault("ranking_direction", "descending")
            watchlist.setdefault("origin", "user")
            watchlist.setdefault("template", False)
            watchlist.setdefault("availability", "available")
            watchlist.setdefault("availability_detail", "")
            watchlist.pop("calculations", None)
            watchlist["columns"] = [
                str(column_id)
                for column_id in watchlist.get("columns") or []
                if str(column_id) in column_ids
            ] or [
                str(row.get("column_id") or "")
                for row in result["market_discovery"].get("column_catalog") or []
                if bool(row.get("default_visible"))
            ]
            ranking_aliases = {"liquidity-rank": "market.liquidity_rank"}
            watchlist["ranking_field"] = ranking_aliases.get(
                str(watchlist.get("ranking_field") or ""),
                str(watchlist.get("ranking_field") or ""),
            )
            field_source_ids = {
                str(row.get("source_id") or "")
                for row in result["market_discovery"].get("field_catalog") or []
            }
            if str(watchlist.get("ranking_field") or "") not in field_source_ids:
                watchlist["ranking_field"] = "market.liquidity_rank"
            if source_schema_version < 39:
                for column_id in ("liquidity_score", "liquidity_rank"):
                    if column_id in column_ids and column_id not in watchlist["columns"]:
                        watchlist["columns"].append(column_id)
                if str(watchlist.get("ranking_field") or "") == "market.liquidity_rank":
                    watchlist["ranking_direction"] = "ascending"
        if (
            source_schema_version < 39
            and str(core_scan.get("ranking_field") or "") == "market.liquidity_rank"
        ):
            core_scan["ranking_direction"] = "ascending"
        for stream in result["market_discovery"].get("signal_streams") or []:
            stream.setdefault("revision", 1)
            stream.setdefault("enabled", True)
            stream.setdefault("origin", "user")
            stream.setdefault("source_scan_id", str(core_scan.get("scan_id") or "qmd-core-scan"))
            legacy_source_id = str(stream.get("source_scan_id") or core_scan.get("scan_id") or "qmd-core-scan")
            stream.setdefault("source_type", "core_scan")
            stream.setdefault("source_id", legacy_source_id)
            if str(stream.get("source_type") or "core_scan") == "core_scan":
                stream["source_id"] = str(core_scan.get("scan_id") or "qmd-core-scan")
                stream["source_scan_id"] = stream["source_id"]
            stream.setdefault("inclusion_rule_sets", [])
            stream.setdefault("inclusion_operator", "all")
            stream.setdefault("columns", list(default_core_scan.get("columns") or []))
            stream.setdefault("refresh_interval_ms", int(core_scan.get("refresh_interval_ms") or 1000))
            stream.setdefault("trigger_policy", "false_to_true")
            stream.setdefault("rearm_policy", "after_false")
            stream.setdefault("cooldown_ms", 0)
            stream.setdefault("maximum_events", 5000)
            stream.setdefault("watchlist_routes", [])
            stream["columns"] = [
                str(column_id)
                for column_id in stream.get("columns") or []
                if str(column_id) in column_ids
            ]
        output_index = data_field_output_index(merged_data_fields)
        ranking_source = str(core_scan.get("ranking_field") or "")
        core_scan["ranking_field_ref"] = str(
            output_index.get(str(core_scan.get("ranking_field_ref") or ""), {}).get("field_ref")
            or output_index.get(ranking_source, {}).get("field_ref")
            or core_scan.get("ranking_field_ref")
            or ""
        )
        for watchlist in result["market_discovery"].get("watchlists") or []:
            ranking_source = str(watchlist.get("ranking_field") or "")
            watchlist["ranking_field_ref"] = str(
                output_index.get(str(watchlist.get("ranking_field_ref") or ""), {}).get("field_ref")
                or output_index.get(ranking_source, {}).get("field_ref")
                or ""
            )
        result["market_discovery"]["data_field_plan"] = compile_data_field_plan(
            result["market_discovery"]
        )
        legacy_run_plans = dict(
            result.get("run_plans") or result.pop("assignments", {}) or {}
        )
        result["run_plans"] = {
            "universes": deepcopy(legacy_run_plans.get("universes") or []),
            "plans": deepcopy(
                legacy_run_plans.get("plans")
                or legacy_run_plans.get("deployments")
                or []
            ),
        }
        result.pop("assignments", None)
        if source_schema_version < CONFIGURATION_SCHEMA_VERSION:
            universe_ids = {
                str(row.get("universe_id") or "")
                for row in result["run_plans"]["universes"]
            }
            for universe in defaults["run_plans"]["universes"]:
                if str(universe.get("universe_id") or "") not in universe_ids:
                    result["run_plans"]["universes"].append(deepcopy(universe))
            plan_ids = {
                str(row.get("run_plan_id") or "")
                for row in result["run_plans"]["plans"]
            }
            added_plan_ids: set[str] = set()
            for plan in defaults["run_plans"]["plans"]:
                plan_id = str(plan.get("run_plan_id") or "")
                if bool(plan.get("protected")) and plan_id not in plan_ids:
                    result["run_plans"]["plans"].append(deepcopy(plan))
                    added_plan_ids.add(plan_id)
            mandate_ids = {
                str(row.get("mandate_id") or "")
                for row in dict(result.get("portfolio") or {}).get("mandates") or []
            }
            for mandate in defaults["portfolio"]["mandates"]:
                if (
                    str(mandate.get("run_plan_id") or "") in added_plan_ids
                    and str(mandate.get("mandate_id") or "") not in mandate_ids
                ):
                    result.setdefault("portfolio", {}).setdefault("mandates", []).append(
                        deepcopy(mandate)
                    )
        for run_plan in result["run_plans"]["plans"]:
            legacy_policy = dict(run_plan.pop("campaign_policy", {}) or {})
            run_plan["run_plan_id"] = str(
                run_plan.get("run_plan_id")
                or run_plan.pop("deployment_id", "")
            )
            run_plan["allowed_environments"] = list(
                run_plan.get("allowed_environments")
                or run_plan.pop("modes", [])
                or []
            )
            run_plan.pop("selection_priority", None)
            authority = dict(run_plan.get("action_authority") or {})
            authority.setdefault("default", "confirm")
            authority.setdefault(
                "initial_entry",
                str(legacy_policy.get("initial_entry_authority") or "inherit"),
            )
            authority.setdefault("add", str(legacy_policy.get("add_authority") or "inherit"))
            authority.setdefault(
                "reentry", str(legacy_policy.get("reentry_authority") or "inherit")
            )
            authority.setdefault(
                "strategic_exit", str(legacy_policy.get("exit_authority") or "automatic")
            )
            authority["protective_exit"] = "automatic"
            authority["emergency_exit"] = "automatic"
            run_plan["action_authority"] = authority
            run_plan["campaign_lifecycle"] = {
                **_default_campaign_policy(),
                **dict(run_plan.get("campaign_lifecycle") or legacy_policy),
            }
            safety = dict(run_plan.get("safety_supervisor") or {})
            run_plan["safety_supervisor"] = {
                "enabled_by_environment": {
                    **_default_safety_supervisor()["enabled_by_environment"],
                    **dict(safety.get("enabled_by_environment") or {}),
                    "paper": True,
                    "live": True,
                }
            }
        if source_schema_version < 41:
            for universe in result["run_plans"]["universes"]:
                if str(universe.get("universe_id") or "") == "price-squeeze-signal-universe":
                    universe["signal_stream_ids"] = ["price-squeeze-early"]
                    universe["description"] = (
                        "Tickers are admitted causally only when an Early Squeeze Move starts."
                    )
            for run_plan in result["run_plans"]["plans"]:
                if (
                    str(run_plan.get("profile_id") or "") != "long-momentum-balanced"
                    or str(run_plan.get("run_plan_id") or "").startswith("long-momentum-news")
                ):
                    continue
                run_plan["signal_stream_ids"] = ["price-squeeze-early"]
                activation = dict(run_plan.get("activation") or {})
                activation["event_policy"] = "new_occurrences"
                activation["watchlist_policy"] = "not_required"
                run_plan["activation"] = activation
        for mandate in dict(result.get("portfolio") or {}).get("mandates") or []:
            mandate["run_plan_id"] = str(
                mandate.get("run_plan_id")
                or mandate.pop("deployment_id", "")
            )
            mandate.setdefault("assignment_mode", "single")
            mandate.setdefault("allocation_weight", 1.0)
            mandate.setdefault("maximum_action_authority", "automatic")
            mandate.pop("autonomy", None)
            mandate.pop("priority", None)
        result["strategy"].setdefault("profiles", [])
        result["strategy"].pop("capability_catalog", None)
        result["strategy"]["default_profile_id"] = str(
            result["strategy"].get("default_profile_id")
            or "long-momentum-balanced"
        )
        result["strategy"]["profile_templates"] = deepcopy(
            defaults["strategy"]["profile_templates"]
        )
        result["strategy"]["definitions"] = deepcopy(
            defaults["strategy"]["definitions"]
        )
        result["strategy"]["input_catalog"] = installed_strategy_input_catalog()
        legacy_compositions: dict[str, dict[str, Any]] = {}
        for profile in result["strategy"]["profiles"]:
            profile.setdefault(
                "publication_status",
                "template" if str(profile.get("origin") or "") == "system" else "draft",
            )
            profile.setdefault("derived_from_profile_id", "")
            profile_id = str(profile.get("profile_id") or "")
            if isinstance(profile.get("composition"), dict):
                legacy_compositions[profile_id] = deepcopy(profile["composition"])
            profile.pop("composition", None)
            if str(profile.get("publication_status")) == "published":
                profile["editable"] = False
            elif str(profile.get("origin") or "") == "system":
                profile["editable"] = False
            profile.setdefault("definition_id", STRATEGY_ID)
            profile.setdefault("definition_revision", STRATEGY_REVISION)
            registration = strategy_executor_optional(
                str(profile.get("definition_id") or ""),
                int(profile.get("definition_revision") or 0),
            )
            parameters = (
                registration.parameter_resolver(dict(profile.get("parameters") or {}))
                if registration is not None
                else dict(profile.get("parameters") or {})
            )
            if not isinstance(parameters.get("entry_rules"), dict):
                parameters["entry_rules"] = default_entry_decision_rules(parameters)
            _normalize_entry_rule_sources(parameters["entry_rules"])
            parameters.pop("entry", None)
            lifecycle = deepcopy(
                dict(profile.get("lifecycle") or _default_strategy_lifecycle(parameters))
            )
            legacy_reentry = next(
                (
                    row
                    for row in profile.get("capabilities") or []
                    if str(row.get("capability_id")) == "exit-watch-reenter"
                ),
                None,
            )
            if legacy_reentry is not None and not profile.get("lifecycle"):
                settings = dict(legacy_reentry.get("settings") or {})
                lifecycle.setdefault("reentry", {}).update(
                    {
                        key: value
                        for key, value in settings.items()
                        if key
                        in {
                            "cooldown_ms",
                            "maximum_attempts",
                            "require_new_confirmation",
                        }
                    }
                )
                lifecycle["reentry"]["enabled"] = bool(
                    legacy_reentry.get("enabled", True)
                )
            lifecycle = _migrate_lifecycle_v14(lifecycle, parameters)
            _normalize_lifecycle_action_ids(lifecycle)
            lifecycle.pop("re_evaluation", None)
            initial_entry = dict(lifecycle.get("initial_entry") or {})
            initial_capital = dict(initial_entry.get("capital_request") or {})
            initial_capital.pop("priority", None)
            initial_entry["capital_request"] = initial_capital
            for step in initial_entry.get("add_steps") or []:
                step_capital = dict(step.get("capital_request") or {})
                step_capital.pop("priority", None)
                step["capital_request"] = step_capital
            lifecycle["initial_entry"] = initial_entry
            reentry = dict(lifecycle.get("reentry") or {})
            reentry_capital = dict(reentry.get("capital_request") or {})
            reentry_capital.pop("priority", None)
            reentry["capital_request"] = reentry_capital
            lifecycle["reentry"] = reentry
            for intent in _lifecycle_order_intents(lifecycle):
                intent.setdefault("protection_profile", "hybrid-single")
            initial_entry = dict(lifecycle.get("initial_entry") or {})
            _normalize_entry_rule_sources({
                key: value
                for key, value in initial_entry.items()
                if key in {"opportunity", "confirmation", "blockers"}
            })
            reentry_rules = dict(
                dict(lifecycle.get("reentry") or {}).get("rules") or {}
            )
            _normalize_entry_rule_sources(reentry_rules)
            for step in initial_entry.get("add_steps") or []:
                _normalize_entry_rule_sources(
                    {"rules": dict(step.get("rules") or {})}
                )
            for route in dict(lifecycle.get("exit") or {}).get("rule_sets") or []:
                _normalize_entry_rule_sources(
                    {"rules": dict(route.get("rules") or {})}
                )
            profile["lifecycle"] = lifecycle
            _migrate_profile_rule_catalog(profile)
            legacy_rule_sets = list(profile.pop("rule_set_catalog", []) or [])
            global_rule_set_ids = {
                str(row.get("rule_set_id") or "")
                for row in result["market_discovery"].get("rule_sets") or []
            }
            for rule_set in legacy_rule_sets:
                rule_set_id = str(rule_set.get("rule_set_id") or "")
                if not rule_set_id or rule_set_id in global_rule_set_ids:
                    continue
                migrated_rule_set = deepcopy(rule_set)
                _normalize_data_rule_set_metadata(
                    migrated_rule_set,
                    atomic=bool(migrated_rule_set.get("atomic")),
                )
                _normalize_rule_set_conditions(migrated_rule_set)
                result["market_discovery"]["rule_sets"].append(migrated_rule_set)
                global_rule_set_ids.add(rule_set_id)
            profile.pop("rule_set_ids", None)
            profile["parameters"] = _parameters_without_lifecycle(parameters)
            if (
                source_schema_version < 42
                and str(profile.get("profile_id") or "")
                == "long-momentum-balanced"
            ):
                # The protected squeeze strategy requires price above the
                # executable VWAP, but it has no separate maximum distance
                # ceiling. Older drafts inherited an unrequested 500-bps
                # anti-chase gate; remove it only from this system profile.
                profile["parameters"].setdefault("liquidity_admission", {}).pop(
                    "maximum_vwap_extension_bps", None
                )
            profile["protected"] = (
                str(profile.get("profile_id"))
                == result["strategy"]["default_profile_id"]
            )
            legacy_capability_ids = {
                str(row.get("capability_id") or "")
                for row in profile.pop("capabilities", []) or []
                if bool(row.get("enabled", True))
            }
            profile["action_policy_ids"] = list(dict.fromkeys([
                *[str(value) for value in profile.get("action_policy_ids") or []],
                *[
                    policy_id
                    for policy_id in ("profit-pocket", "confirmed-pullback-add")
                    if policy_id in legacy_capability_ids
                ],
            ]))
        template_ids = {
            str(row.get("profile_id"))
            for row in result["strategy"]["profile_templates"]
        }
        referenced_profile_ids = {
            str(row.get("profile_id"))
            for row in dict(result.get("run_plans") or {}).get("plans") or []
        }
        migrated_profiles: list[dict[str, Any]] = []
        for profile in result["strategy"]["profiles"]:
            profile_id = str(profile.get("profile_id"))
            if profile_id in template_ids and profile_id not in referenced_profile_ids:
                continue
            if profile_id in template_ids:
                profile["origin"] = "user"
            migrated_profiles.append(profile)
        result["strategy"]["profiles"] = migrated_profiles
        existing_profiles = {
            str(row.get("profile_id"))
            for row in dict(result.get("strategy") or {}).get("profiles") or []
        }
        result["strategy"]["profiles"].extend(
            deepcopy(row)
            for row in defaults["strategy"]["profiles"]
            if bool(row.get("protected"))
            and str(row["profile_id"]) not in existing_profiles
        )
        if source_schema_version < CONFIGURATION_SCHEMA_VERSION:
            default_profile = next(
                row
                for row in defaults["strategy"]["profiles"]
                if str(row.get("profile_id") or "")
                == result["strategy"]["default_profile_id"]
            )
            for profile in result["strategy"]["profiles"]:
                if str(profile.get("profile_id") or "") != str(
                    default_profile.get("profile_id") or ""
                ):
                    continue
                profile["name"] = deepcopy(default_profile["name"])
                profile["description"] = deepcopy(default_profile["description"])
                profile["protected"] = True
                profile["definition_revision"] = STRATEGY_REVISION
                profile["action_policy_ids"] = ["profit-pocket"]
                profile.setdefault("parameters", {})["protection"] = deepcopy(
                    default_profile["parameters"]["protection"]
                )
                profile["parameters"]["momentum_management"] = deepcopy(
                    default_profile["parameters"]["momentum_management"]
                )
                lifecycle = dict(profile.get("lifecycle") or {})
                default_lifecycle = dict(default_profile["lifecycle"])
                lifecycle.setdefault("trading_behavior", {}).update(
                    deepcopy(default_lifecycle["trading_behavior"])
                )
                initial_entry = lifecycle.setdefault("initial_entry", {})
                default_initial = dict(default_lifecycle["initial_entry"])
                initial_entry["opportunity"] = deepcopy(default_initial["opportunity"])
                initial_entry["confirmation"] = deepcopy(default_initial["confirmation"])
                initial_entry["add_steps"] = []
                profile["lifecycle"] = lifecycle
        for template in result["strategy"]["profile_templates"]:
            template["publication_status"] = "template"
            template["editable"] = False
            template.setdefault("derived_from_profile_id", "")
            template.pop("composition", None)
            template.pop("rule_set_catalog", None)
            template.pop("rule_set_ids", None)
            template.pop("capabilities", None)
            template.setdefault(
                "action_policy_ids",
                ["profit-pocket", "confirmed-pullback-add"],
            )
        result["run_plans"].setdefault(
            "universes",
            deepcopy(defaults["run_plans"]["universes"]),
        )
        plans = result["run_plans"].setdefault("plans", [])
        plans_by_profile: dict[str, dict[str, Any]] = {}
        for row in plans:
            plans_by_profile.setdefault(str(row.get("profile_id") or ""), row)
        portfolio = result.setdefault("portfolio", deepcopy(defaults["portfolio"]))
        mandates = portfolio.setdefault("mandates", [])
        for profile in result["strategy"].get("profiles") or []:
            profile_id = str(profile.get("profile_id") or "")
            legacy = legacy_compositions.get(profile_id, {})
            run_plan = plans_by_profile.get(profile_id)
            if run_plan is None and legacy:
                run_plan_id = f"strategy-{profile_id}"
                run_plan = {
                    "run_plan_id": run_plan_id,
                    "name": str(profile.get("name") or profile_id),
                    "description": "Migrated Run Plan for a legacy Strategy composition.",
                    "profile_id": profile_id,
                    "oms_profile_id": str(legacy.get("oms_profile_id") or "adaptive-regular"),
                    "universe_id": "configured-watch-universe",
                    "book_id": "default",
                    "action_authority": deepcopy(legacy.get("action_authority") or _default_action_authority()),
                    "campaign_lifecycle": _default_campaign_policy(),
                    "safety_supervisor": _default_safety_supervisor(),
                    "mandate_ids": [],
                    "enabled": bool(profile.get("enabled", True)),
                    "allowed_environments": list(legacy.get("allowed_environments") or ["replay"]),
                    "runtime_assignments": [],
                }
                plans.append(run_plan)
                plans_by_profile[profile_id] = run_plan
                for account_key in legacy.get("account_keys") or []:
                    mandate_id = f"{run_plan_id}-{account_key}"
                    mandates.append({
                        "mandate_id": mandate_id,
                        "run_plan_id": run_plan_id,
                        "account_key": str(account_key),
                        "enabled": True,
                        "maximum_cash_fraction": 1.0,
                        "maximum_planned_risk_fraction": 0.01,
                        "maximum_positions": 10,
                        "assignment_mode": "single",
                        "allocation_weight": 1.0,
                        "maximum_action_authority": "confirm",
                        "allow_replacement": False,
                        "minimum_replacement_improvement_pct": 20.0,
                    })
            if run_plan is None:
                continue
            watchlist_id = str(legacy.get("watchlist_id") or "core-candidates")
            run_plan.setdefault("watchlist_ids", [watchlist_id])
            run_plan.setdefault("canvas_profile_id", "current-canvas")
            run_plan.setdefault("data_plan_ids", _default_data_plan_ids())
            run_plan.setdefault("source_revision_policy", "require_complete")
            if legacy:
                run_plan.setdefault("oms_profile_id", str(legacy.get("oms_profile_id") or "adaptive-regular"))
                run_plan.setdefault("allowed_environments", list(legacy.get("allowed_environments") or ["replay"]))
                run_plan.setdefault("action_authority", deepcopy(legacy.get("action_authority") or _default_action_authority()))
        universe_ids = {
            str(row.get("universe_id"))
            for row in result["run_plans"]["universes"]
        }
        fallback_universe = (
            next(iter(universe_ids))
            if universe_ids
            else "configured-watch-universe"
        )
        profiles_by_id = {
            str(profile.get("profile_id") or ""): profile
            for profile in result["strategy"].get("profiles") or []
        }
        for run_plan in result["run_plans"].get("plans") or []:
            run_plan.setdefault("universe_id", fallback_universe)
            run_plan.setdefault("book_id", "default")
            run_plan.setdefault("signal_stream_ids", ["price-squeeze-5m"])
            run_plan.setdefault(
                "activation",
                {"event_policy": "new_occurrences", "watchlist_policy": "any_selected"},
            )
            run_plan.setdefault(
                "enablement",
                {"state": "enabled", "scope": "persistent", "effective_session": ""},
            )
            calculations = list(
                dict(result.get("market_discovery") or {})
                .get("core_scan", {})
                .get("calculations")
                or []
            )
            if "observation_dependencies" not in run_plan:
                profile = profiles_by_id.get(str(run_plan.get("profile_id") or ""))
                run_plan["observation_dependencies"] = (
                    _compiled_observation_dependencies(profile, calculations)
                    if profile
                    else []
                )
            else:
                run_plan["observation_dependencies"] = _with_qmd_dependency_metadata(
                    list(run_plan.get("observation_dependencies") or []),
                    calculations,
                )
        existing_oms = {
            str(row.get("profile_id"))
            for row in dict(result.get("oms") or {}).get("profiles") or []
        }
        result["oms"]["profiles"].extend(
            deepcopy(row)
            for row in defaults["oms"]["profiles"]
            if str(row["profile_id"]) not in existing_oms
        )
        for key in ("execution_policies", "protection_profiles"):
            result["oms"].setdefault(key, deepcopy(defaults["oms"][key]))
        if source_schema_version < 43:
            default_protection_profiles = {
                str(row.get("profile_id") or ""): deepcopy(row)
                for row in defaults["oms"]["protection_profiles"]
            }
            result["oms"]["protection_profiles"] = [
                default_protection_profiles.get(
                    str(row.get("profile_id") or ""),
                    row,
                )
                for row in result["oms"]["protection_profiles"]
            ]
        for oms_profile in result["oms"]["profiles"]:
            settings = dict(oms_profile.get("settings") or {})
            settings.pop("time_in_force", None)
            settings.pop("outside_rth", None)
            settings["session_routing"] = "smart"
            settings.setdefault("entry_execution_policy_id", "adaptive_urgent")
            settings.setdefault("exit_execution_policy_id", "adaptive_very_urgent")
            settings.setdefault("protection_profile_id", "hybrid-single")
            oms_profile["settings"] = settings
        for binding in dict(result.get("accounts") or {}).get("bindings") or []:
            _normalize_account_binding(binding)
            if source_schema_version < 19:
                _migrate_server_side_broker_binding(binding)
        _ensure_environment_account_bindings(
            result["accounts"]["bindings"],
            str(result["portfolio"]["policies"][0]["policy_id"]),
        )
        derived_sessions = _build_session_configuration(
            list(result["accounts"].get("bindings") or []),
            list(result["portfolio"].get("mandates") or []),
            list(result["run_plans"].get("plans") or []),
        )
        manual_mandates = derived_sessions.pop("manual_mandates")
        existing_mandate_ids = {
            str(row.get("mandate_id") or "")
            for row in result["portfolio"].get("mandates") or []
        }
        result["portfolio"]["mandates"].extend(
            row for row in manual_mandates
            if str(row.get("mandate_id") or "") not in existing_mandate_ids
        )
        if source_schema_version < 38 or not isinstance(result.get("sessions"), dict):
            result["sessions"] = derived_sessions
        else:
            sessions = result["sessions"]
            derived_route_ids = {
                str(row.get("execution_route_id") or "")
                for row in derived_sessions["execution_routes"]
            }
            sessions["execution_routes"] = [
                row for row in sessions.setdefault("execution_routes", [])
                if not bool(row.get("system_generated", True))
                or str(row.get("execution_route_id") or "") in derived_route_ids
            ]
            derived_deployment_ids = {
                str(row.get("strategy_deployment_id") or "")
                for row in derived_sessions["strategy_deployments"]
            }
            sessions["strategy_deployments"] = [
                row for row in sessions.setdefault("strategy_deployments", [])
                if not bool(row.get("system_generated", True))
                or str(row.get("strategy_deployment_id") or "") in derived_deployment_ids
            ]
            for profile in sessions.setdefault("profiles", []):
                profile["execution_route_ids"] = [
                    str(value) for value in profile.get("execution_route_ids") or []
                    if str(value) in {
                        str(row.get("execution_route_id") or "")
                        for row in sessions["execution_routes"]
                    }
                ]
            for key, identity_key in (
                ("profiles", "session_profile_id"),
                ("execution_routes", "execution_route_id"),
                ("strategy_deployments", "strategy_deployment_id"),
            ):
                rows = sessions.setdefault(key, [])
                by_identity = {str(row.get(identity_key) or ""): row for row in rows}
                for derived in derived_sessions[key]:
                    identity = str(derived.get(identity_key) or "")
                    current = by_identity.get(identity)
                    if current is None:
                        rows.append(deepcopy(derived))
                        by_identity[identity] = rows[-1]
                        continue
                    if key == "profiles":
                        current["execution_route_ids"] = list(dict.fromkeys([
                            *list(current.get("execution_route_ids") or []),
                            *list(derived.get("execution_route_ids") or []),
                        ]))
                        if str(current.get("default_execution_route_id") or "") not in current["execution_route_ids"]:
                            current["default_execution_route_id"] = str(derived.get("default_execution_route_id") or "")
                    elif key == "execution_routes":
                        if bool(current.get("system_generated", True)):
                            current["modes"] = deepcopy(derived.get("modes") or [])
                            current["system_generated"] = True
                        else:
                            current.setdefault("modes", deepcopy(derived.get("modes") or []))
                    elif key == "strategy_deployments":
                        if bool(current.get("system_generated", True)):
                            current["execution_route_ids"] = deepcopy(derived.get("execution_route_ids") or [])
                            current["portfolio_mandate_ids"] = deepcopy(derived.get("portfolio_mandate_ids") or [])
                            current["modes"] = deepcopy(derived.get("modes") or [])
                            current["system_generated"] = True
                        else:
                            current["execution_route_ids"] = list(dict.fromkeys([
                                *list(current.get("execution_route_ids") or []),
                                *list(derived.get("execution_route_ids") or []),
                            ]))
                            current["portfolio_mandate_ids"] = list(dict.fromkeys([
                                *list(current.get("portfolio_mandate_ids") or []),
                                *list(derived.get("portfolio_mandate_ids") or []),
                            ]))
                            current["modes"] = deepcopy(current.get("modes") or derived.get("modes") or [])
        deployments = list(dict(result.get("sessions") or {}).get("strategy_deployments") or [])
        deployments_by_plan = {
            str(row.get("run_plan_id") or ""): str(row.get("strategy_deployment_id") or "")
            for row in deployments
        }
        for mandate in result["portfolio"].get("mandates") or []:
            run_plan_id = str(mandate.get("run_plan_id") or "")
            if run_plan_id:
                mandate["principal_kind"] = "strategy_deployment"
                mandate["principal_id"] = deployments_by_plan.get(run_plan_id, run_plan_id)
            else:
                mandate.setdefault("principal_kind", "session")
                mandate.setdefault("principal_id", "")
        _retire_legacy_vwap_rule_sets(result["market_discovery"])
        return result
    if not isinstance(raw.get("strategy"), dict) or "strategy_id" not in dict(raw.get("strategy") or {}):
        return deepcopy(raw)
    old = deepcopy(raw)
    base = _default_draft()
    legacy_strategy = dict(old["strategy"])
    profile = _strategy_profile(
        "migrated-primary",
        str(legacy_strategy.get("name") or "Migrated strategy"),
        "Migrated from the original application configuration.",
        dict(legacy_strategy.get("parameters") or default_long_momentum_parameters()),
        origin="user",
    )
    base["strategy"]["profiles"] = [
        profile,
        *[
            row for row in base["strategy"]["profiles"]
            if row["profile_id"] != profile["profile_id"]
        ],
    ]
    base["oms"]["profiles"] = [{
        "profile_id": "migrated-oms",
        "revision": 1,
        "name": "Migrated OMS",
        "description": "Migrated execution and protection behavior.",
        "origin": "user",
        "settings": deepcopy(old["oms"]),
    }]
    base["oms"]["profiles"][0]["settings"].pop("time_in_force", None)
    base["oms"]["profiles"][0]["settings"].pop("outside_rth", None)
    base["oms"]["profiles"][0]["settings"]["session_routing"] = "smart"
    base["oms"]["profiles"][0]["settings"].setdefault(
        "entry_execution_policy_id", "adaptive_urgent"
    )
    base["oms"]["profiles"][0]["settings"].setdefault(
        "exit_execution_policy_id", "adaptive_very_urgent"
    )
    base["oms"]["profiles"][0]["settings"].setdefault(
        "protection_profile_id", "hybrid-single"
    )
    base["accounts"] = deepcopy(old["accounts"])
    for binding in base["accounts"].get("bindings") or []:
        _normalize_account_binding(binding)
        _migrate_server_side_broker_binding(binding)
    base["portfolio"]["policies"] = deepcopy(dict(old["portfolio"]).get("policies") or [])
    base["portfolio"]["groups"] = deepcopy(dict(old["portfolio"]).get("groups") or [])
    account_keys = [str(row["account_key"]) for row in dict(old["accounts"]).get("bindings") or []]
    mandates = [{
        "mandate_id": f"migrated-{key}",
        "run_plan_id": "migrated-run-plan",
        "account_key": key,
        "enabled": True,
        "maximum_cash_fraction": 1.0,
        "maximum_planned_risk_fraction": 0.01,
        "maximum_positions": 10,
        "assignment_mode": "single" if len(account_keys) == 1 else "replicated",
        "allocation_weight": 1.0,
        "maximum_action_authority": "automatic",
        "allow_replacement": False,
        "minimum_replacement_improvement_pct": 20.0,
    } for key in account_keys]
    base["portfolio"]["mandates"] = mandates
    base["run_plans"] = {
        "universes": deepcopy(base["run_plans"]["universes"]),
        "plans": [{
        "run_plan_id": "migrated-run-plan",
        "name": "Migrated Run Plan",
        "description": "Run Plan migrated from the original assignment configuration.",
        "profile_id": profile["profile_id"],
        "oms_profile_id": "migrated-oms",
        "universe_id": "configured-watch-universe",
        "watchlist_ids": ["core-candidates"],
        "canvas_profile_id": "current-canvas",
        "data_plan_ids": _default_data_plan_ids(),
        "source_revision_policy": "require_complete",
        "book_id": "default",
        "action_authority": _default_action_authority(),
        "campaign_lifecycle": _default_campaign_policy(),
        "safety_supervisor": _default_safety_supervisor(),
        "mandate_ids": [row["mandate_id"] for row in mandates],
        "enabled": True,
        "allowed_environments": ["replay", "backtest", "backtest_debug"],
        "runtime_assignments": deepcopy(old.get("assignments") or []),
    }]}
    if "canvas" in old:
        base["canvas"] = deepcopy(old["canvas"])
    return base


def _migrate_server_side_broker_binding(binding: dict[str, Any]) -> None:
    modes = set(binding.get("modes") or [])
    if not modes.intersection({"paper", "live"}):
        return
    if not str(binding.get("source_account_env") or "").strip():
        if modes == {"paper"} or str(binding.get("account_class") or "") == "paper":
            binding["source_account_env"] = "IBKR_PAPER_ACCOUNT_ID"
        elif modes == {"live"}:
            binding["source_account_env"] = "IBKR_CASH_ACCOUNT_ID"
    binding["source_account_id"] = ""


def _strategy_profile(
    profile_id: str,
    name: str,
    description: str,
    parameters: dict[str, Any],
    *,
    origin: str,
    capability_modes: dict[str, str] | None = None,
    protected: bool = False,
) -> dict[str, Any]:
    modes = capability_modes or {}
    action_policy_ids = ["profit-pocket", "confirmed-pullback-add"]
    profile = {
        "profile_id": profile_id,
        "revision": 1,
        "name": name,
        "description": description,
        "definition_id": STRATEGY_ID,
        "definition_revision": STRATEGY_REVISION,
        "origin": origin,
        "editable": origin == "user",
        "protected": protected,
        "enabled": True,
        "publication_status": "template" if origin == "system" else "draft",
        "derived_from_profile_id": "",
        "lifecycle": _default_strategy_lifecycle(parameters),
        "parameters": _parameters_without_lifecycle(parameters),
        "action_policy_ids": action_policy_ids,
    }
    _migrate_profile_rule_catalog(profile)
    return profile


def _normalize_account_binding(binding: dict[str, Any]) -> None:
    account_key = str(binding.get("account_key") or "")
    fallback = "Backtest account" if account_key in {"replay", "backtest"} else account_key or "Trading account"
    if account_key == "replay" and str(binding.get("name") or "") == "Replay account":
        binding["name"] = fallback
        return
    binding["name"] = str(binding.get("name") or binding.get("display_name") or fallback)


def _ensure_environment_account_bindings(bindings: list[dict[str, Any]], policy_id: str) -> None:
    managed = [
        {
            "account_key": "paper",
            "name": "IBKR Paper account",
            "source_account_env": "IBKR_PAPER_ACCOUNT_ID",
            "source_account_id": "",
            "account_class": "paper",
            "base_currency": "USD",
            "session_key": "ibkr-paper",
            "portfolio_policy_id": policy_id,
            "enabled": False,
            "system_managed": True,
            "modes": ["paper"],
        },
        {
            "account_key": "cash",
            "name": "IBKR Cash account",
            "source_account_env": "IBKR_CASH_ACCOUNT_ID",
            "source_account_id": "",
            "account_class": "cash",
            "base_currency": "USD",
            "session_key": "ibkr-live",
            "portfolio_policy_id": policy_id,
            "enabled": False,
            "system_managed": True,
            "modes": ["live"],
        },
    ]
    managed_by_key = {binding["account_key"]: binding for binding in managed}
    existing = {str(binding.get("account_key") or "") for binding in bindings}
    for binding in bindings:
        template = managed_by_key.get(str(binding.get("account_key") or ""))
        if template is None or str(binding.get("source_account_env") or "") != template["source_account_env"]:
            continue
        if not bool(binding.get("system_managed")):
            binding["enabled"] = False
            binding["system_managed"] = True
    bindings.extend(deepcopy(binding) for binding in managed if binding["account_key"] not in existing)


def _lifecycle_order_intents(lifecycle: dict[str, Any]) -> list[dict[str, Any]]:
    initial = lifecycle.setdefault("initial_entry", {})
    reentry = lifecycle.setdefault("reentry", {})
    exit_section = lifecycle.setdefault("exit", {})
    return [
        initial.setdefault("order_intent", {}),
        *[
            step.setdefault("order_intent", {})
            for step in initial.get("add_steps") or []
        ],
        reentry.setdefault("order_intent", {}),
        *[
            route.setdefault("order_intent", {})
            for route in exit_section.get("rule_sets") or []
        ],
    ]


def _validate_strategy_lifecycle(
    lifecycle: dict[str, Any],
    raw_rule_set_catalog: list[dict[str, Any]],
    parameter_resolver: Callable[[dict[str, Any] | None], dict[str, Any]],
    engine_parameters: dict[str, Any],
    action_categories: dict[str, str],
) -> None:
    required = {"phase_modes", "trading_behavior", "initial_entry", "reentry", "exit"}
    missing = required - set(lifecycle)
    if missing:
        raise ValueError(
            f"Strategy lifecycle is missing: {', '.join(sorted(missing))}"
        )
    phase_modes = dict(lifecycle["phase_modes"])
    required_phases = {"initial_entry", "manage", "reentry", "exit"}
    if set(phase_modes) != required_phases or any(
        str(phase_modes.get(phase_name) or "") not in {"automatic", "manual"}
        for phase_name in required_phases
    ):
        raise ValueError(
            "Strategy phase modes must define initial_entry, manage, reentry, and exit as automatic or manual"
        )
    behavior = dict(lifecycle["trading_behavior"])
    if str(behavior.get("side") or "") not in {"long", "short"}:
        raise ValueError("Each Strategy Profile must use exactly one side: long or short")
    sessions = set(behavior.get("eligible_sessions") or [])
    if not sessions or not sessions <= {"premarket", "regular", "after_hours"}:
        raise ValueError("Strategy eligible sessions are unsupported")
    rule_set_ids = _unique_ids(
        raw_rule_set_catalog,
        "rule_set_id",
        "Strategy rule set",
    )
    rule_set_catalog = {
        str(rule_set["rule_set_id"]): dict(rule_set)
        for rule_set in raw_rule_set_catalog
    }
    for rule_set in raw_rule_set_catalog:
        _validate_rule_set_definition(dict(rule_set), f"Rule set {rule_set.get('name')}")
    initial_entry = dict(lifecycle["initial_entry"])
    _validate_lifecycle_action(initial_entry, "Initial entry", action_categories, {"enter"})
    runtime_rules = {
        "trigger": _materialize_rule_stage(dict(initial_entry.get("opportunity") or {}), rule_set_catalog),
        "confirmation": _materialize_rule_stage(dict(initial_entry.get("confirmation") or {}), rule_set_catalog),
        "veto": _materialize_rule_stage(dict(initial_entry.get("blockers") or {}), rule_set_catalog),
    }
    for stage_name in ("opportunity", "confirmation", "blockers"):
        _validate_rule_stage(
            dict(initial_entry.get(stage_name) or {}),
            f"Initial entry {stage_name}",
            rule_set_ids,
        )
    parameters = deepcopy(engine_parameters)
    parameters["entry_rules"] = runtime_rules
    parameter_resolver(parameters)
    _validate_capital_request(dict(initial_entry.get("capital_request") or {}), "Initial entry")
    _validate_order_intent(dict(initial_entry.get("order_intent") or {}), "Initial entry")
    add_steps = list(initial_entry.get("add_steps") or [])
    _unique_ids(add_steps, "step_id", "Initial-entry add step")
    for step in add_steps:
        _validate_lifecycle_action(step, f"Add step {step.get('name')}", action_categories, {"add"})
        _validate_rule_stage(dict(step.get("rules") or {}), f"Add step {step.get('name')}", rule_set_ids)
        _validate_capital_request(dict(step.get("capital_request") or {}), f"Add step {step.get('name')}")
        _validate_order_intent(dict(step.get("order_intent") or {}), f"Add step {step.get('name')}")
        if int(step.get("maximum_uses") or 0) < 1:
            raise ValueError(f"Add step {step.get('name')} maximum uses must be positive")
    reentry = dict(lifecycle["reentry"])
    _validate_lifecycle_action(reentry, "Reentry", action_categories, {"enter"})
    if int(reentry.get("cooldown_ms") or 0) < 0:
        raise ValueError("Strategy reentry cooldown cannot be negative")
    if int(reentry.get("maximum_attempts") or 0) < 0:
        raise ValueError("Strategy maximum reentries cannot be negative")
    reentry_rules = dict(reentry.get("rules") or {})
    runtime_reentry_rules = {
        "trigger": _materialize_rule_stage(dict(reentry_rules.get("opportunity") or {}), rule_set_catalog),
        "confirmation": _materialize_rule_stage(dict(reentry_rules.get("confirmation") or {}), rule_set_catalog),
        "veto": _materialize_rule_stage(dict(reentry_rules.get("blockers") or {}), rule_set_catalog),
    }
    for stage_name in ("opportunity", "confirmation", "blockers"):
        _validate_rule_stage(
            dict(reentry_rules.get(stage_name) or {}),
            f"Reentry {stage_name}",
            rule_set_ids,
        )
    reentry_parameters = deepcopy(engine_parameters)
    reentry_parameters["entry_rules"] = runtime_reentry_rules
    parameter_resolver(reentry_parameters)
    _validate_capital_request(dict(reentry.get("capital_request") or {}), "Reentry")
    _validate_order_intent(dict(reentry.get("order_intent") or {}), "Reentry")
    routes = list(dict(lifecycle["exit"]).get("rule_sets") or [])
    _unique_ids(routes, "rule_set_id", "Strategy exit rule set")
    if not routes:
        raise ValueError("Strategy exit requires at least one rule set")
    for route in routes:
        if str(route.get("action") or "") not in {"close", "reduce"}:
            raise ValueError(f"Exit rule set {route.get('name')} has an unsupported action")
        _validate_lifecycle_action(
            route,
            f"Exit rule set {route.get('name')}",
            action_categories,
            {"exit", "reduce"},
        )
        timing = dict(route.get("timing") or {})
        if int(timing.get("active_after_ms") or 0) < 0 or int(
            timing.get("expires_after_ms") or 0
        ) < 0:
            raise ValueError("Exit rule-set timing cannot be negative")
        _validate_rule_stage(dict(route.get("rules") or {}), f"Exit rule set {route.get('name')}", rule_set_ids)
        _validate_order_intent(dict(route.get("order_intent") or {}), f"Exit rule set {route.get('name')}")
        position_fraction = float(route.get("position_fraction") or 0)
        if not 0 < position_fraction <= 1:
            raise ValueError(f"Exit rule set {route.get('name')} position fraction must be between zero and one")


def _validate_lifecycle_action(
    route: dict[str, Any],
    label: str,
    action_categories: dict[str, str],
    categories: set[str],
) -> None:
    action_id = str(route.get("action_id") or "")
    if action_id not in action_categories:
        raise ValueError(f"{label} references an unknown Trading Action: {action_id or 'missing'}")
    category = action_categories[action_id]
    if category not in categories:
        raise ValueError(f"{label} references an incompatible Trading Action: {action_id}")


def _validate_rule_set_definition(rule_set: dict[str, Any], label: str) -> None:
    if not str(rule_set.get("name") or "").strip():
        raise ValueError(f"{label} requires a name")
    if not str(rule_set.get("description") or "").strip():
        raise ValueError(f"{label} requires a description")
    operator = str(rule_set.get("operator") or "")
    if operator not in {"all", "any", "score"}:
        raise ValueError(f"{label} has unsupported condition logic")
    if operator == "score" and not 0 < float(rule_set.get("required_score") or 0) <= 1:
        raise ValueError(f"{label} required score must be between zero and one")
    conditions = list(rule_set.get("conditions") or [])
    if not conditions:
        raise ValueError(f"{label} requires at least one condition")
    condition_ids = [str(condition.get("condition_id") or "") for condition in conditions]
    if any(not condition_id for condition_id in condition_ids) or len(set(condition_ids)) != len(condition_ids):
        raise ValueError(f"{label} condition ids must be present and unique")
    if bool(rule_set.get("enabled", True)) and not any(
        bool(condition.get("enabled", True)) for condition in conditions
    ):
        raise ValueError(f"{label} requires an enabled condition")
    for condition in conditions:
        condition_label = f"{label} condition {condition.get('condition_id')}"
        comparator = str(condition.get("comparator") or "")
        if comparator not in RULE_SET_COMPARATORS:
            raise ValueError(f"{condition_label} comparator is unsupported")
        if not str(condition.get("left_source_id") or ""):
            raise ValueError(f"{condition_label} requires a left source")
        right_source_id = str(condition.get("right_source_id") or "")
        value = condition.get("value")
        if comparator == "is_true":
            if right_source_id:
                raise ValueError(f"{condition_label} is_true cannot use a target source")
            continue
        if comparator == "above_by_bps":
            if not right_source_id:
                raise ValueError(f"{condition_label} requires a target source")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{condition_label} requires a numeric basis-point buffer")
            continue
        if not right_source_id and value is None:
            raise ValueError(f"{condition_label} requires a comparison value or target source")


def _validate_rule_expression(
    expression: dict[str, Any],
    label: str,
    rule_set_ids: set[str],
) -> None:
    kind = str(expression.get("kind") or "")
    if kind == "rule_set":
        rule_set_id = str(expression.get("rule_set_id") or "")
        if rule_set_id not in rule_set_ids:
            raise ValueError(f"{label} references unknown rule set {rule_set_id or '<empty>'}")
        return
    if kind != "operator" or str(expression.get("operator") or "") not in {"and", "or"}:
        raise ValueError(f"{label} has unsupported expression logic")
    children = list(expression.get("children") or [])
    if not children:
        raise ValueError(f"{label} requires at least one rule set")
    for child in children:
        _validate_rule_expression(dict(child), label, rule_set_ids)


def _validate_rule_stage(
    stage: dict[str, Any],
    label: str,
    rule_set_ids: set[str],
) -> None:
    _validate_rule_expression(dict(stage.get("expression") or {}), label, rule_set_ids)


def _validate_capital_request(request: dict[str, Any], label: str) -> None:
    mode = str(request.get("mode") or "")
    if mode not in {"fixed_quantity", "mandate_fraction", "risk_fraction", "all_available"}:
        raise ValueError(f"{label} capital request mode is unsupported")
    value = float(request.get("value") or 0)
    if mode in {"mandate_fraction", "risk_fraction"} and not 0 < value <= 1:
        raise ValueError(f"{label} capital request fraction must be between zero and one")
    if mode == "fixed_quantity" and value <= 0:
        raise ValueError(f"{label} fixed quantity must be positive")
    if mode == "all_available" and value not in {0.0, 1.0}:
        raise ValueError(f"{label} all-available requests do not accept a custom value")
    minimum_quantity = float(request.get("minimum_quantity") or 0.0)
    maximum_quantity = request.get("maximum_quantity")
    if minimum_quantity < 0:
        raise ValueError(f"{label} minimum quantity cannot be negative")
    if maximum_quantity is not None and float(maximum_quantity) <= 0:
        raise ValueError(f"{label} maximum quantity must be positive")
    if maximum_quantity is not None and minimum_quantity > float(maximum_quantity):
        raise ValueError(f"{label} minimum quantity cannot exceed maximum quantity")


def _validate_order_intent(intent: dict[str, Any], label: str) -> None:
    if not str(intent.get("execution_policy") or "").strip():
        raise ValueError(f"{label} execution policy is required")
    if "time_in_force" in intent or "outside_rth" in intent:
        raise ValueError(
            f"{label} session routing must be derived by OMS from Trading Behavior"
        )
    if str(intent.get("partial_fill_policy") or "") not in {
        "complete_remainder",
        "accept_partial",
        "cancel_remainder",
    }:
        raise ValueError(f"{label} partial-fill policy is unsupported")
    if int(intent.get("deadline_ms") or 0) < 0:
        raise ValueError(f"{label} execution deadline cannot be negative")


def _normalize_entry_rule_sources(entry_rules: dict[str, Any]) -> None:
    for stage in entry_rules.values():
        if not isinstance(stage, dict):
            continue
        for group in stage.get("groups") or []:
            for condition in group.get("conditions") or []:
                for side in ("left", "right"):
                    source_key = f"{side}_source_id"
                    if str(condition.get(source_key) or "") == _LEGACY_VWAP_SOURCE_ID:
                        condition[source_key] = _EXECUTION_VWAP_SOURCE_ID
        stage["groups"] = [
            group
            for group in stage.get("groups") or []
            if not any(
                {
                    str(condition.get("left_source_id") or ""),
                    str(condition.get("right_source_id") or ""),
                }
                & {
                    "signal.vwap_transition.score",
                    "indicator.vwap.slope",
                }
                for condition in group.get("conditions") or []
            )
        ]


def _default_oms_profile() -> dict[str, Any]:
    return {
        "profile_id": "adaptive-regular",
        "revision": 1,
        "name": "Adaptive regular",
        "description": "Balanced shared execution with hybrid protection and trailing enabled.",
        "origin": "system",
        "editable": True,
        "settings": {
            "entry_execution_policy_id": "adaptive_urgent",
            "exit_execution_policy_id": "adaptive_very_urgent",
            "protection_profile_id": "hybrid-single",
            "entry_urgency": "urgent",
            "exit_urgency": "very_urgent",
            "limit_offset_bps": 5.0,
            "tick_size": 0.01,
            "session_routing": "smart",
            "protection": {
                "stop_method": "hybrid",
                "structure_buffer_bps": 8.0,
                "volatility_multiple": 1.25,
                "maximum_risk_pct": 6.0,
                "trailing_enabled": True,
            },
        },
    }


def _default_execution_policies() -> list[dict[str, Any]]:
    timing = {
        "passive": (1_000, 2, 250),
        "midpoint": (750, 3, 150),
        "adaptive_patient": (1_000, 3, 250),
        "adaptive_regular": (750, 4, 100),
        "adaptive_urgent": (500, 6, 50),
        "adaptive_very_urgent": (300, 8, 25),
        "immediate_with_limit": (250, 3, 25),
        "ibkr_native_adaptive": (1_000, 0, 0),
        "cancel_if_not_filled": (500, 3, 100),
    }
    descriptions = {
        "passive": "Posts at the near-side quote without crossing the spread, prioritizing price quality over fill certainty.",
        "midpoint": "Posts at the current bid-ask midpoint, seeking spread improvement while accepting that the order may not fill.",
        "adaptive_patient": "Starts at the near-side quote and advances only to the midpoint after repeated attempts, favoring price quality over speed.",
        "adaptive_regular": "Moves progressively from the near-side quote toward executable liquidity, balancing price improvement with fill probability.",
        "adaptive_urgent": "Quotes at the executable touch immediately, prioritizing a timely fill while remaining inside the approved price envelope.",
        "adaptive_very_urgent": "Starts at the executable touch and may move through it by bounded ticks on reprices, maximizing fill urgency within hard limits.",
        "immediate_with_limit": "Seeks an immediate fill at executable liquidity but never crosses the configured buy ceiling or sell floor.",
        "ibkr_native_adaptive": "Uses urgent touch pricing without OMS repricing; the current runtime does not delegate this policy to a broker-native adaptive algorithm.",
        "cancel_if_not_filled": "Moves from passive toward executable pricing while time remains, then cancels the unfilled remainder at the deadline.",
    }
    return [
        {
            "policy_id": name,
            "revision": 1,
            "name": name,
            "description": descriptions[name],
            "origin": "system",
            "editable": True,
            "quote_source": "qmd",
            "partial_fill_policy": "complete_remainder",
            "maximum_price_discretion_ticks": (
                DEFAULT_VERY_URGENT_PRICE_DISCRETION_TICKS
                if name == "adaptive_very_urgent"
                else 0
            ),
            "envelope": {
                "maximum_buy_price": None,
                "minimum_sell_price": None,
                "deadline_ms": values[0],
                "maximum_reprices": values[1],
                "minimum_reprice_interval_ms": values[2],
            },
        }
        for name, values in timing.items()
    ]


def _default_protection_profiles() -> list[dict[str, Any]]:
    profiles = [{
        "profile_id": "hybrid-single",
        "revision": 1,
        "name": "Hybrid single stop",
        "description": "One full-position hybrid stop using the current causal strategy swing and volatility.",
        "origin": "system",
        "editable": True,
        "add_policy": "independent_slice",
        "profit_pocket_transition": "move_to_breakeven",
        "mandatory_catastrophic_backstop": True,
        "emergency_repair_deadline_ms": 500,
        "slices": [{
            "slice_id": "position",
            "quantity_fraction": 1.0,
            "profit_target_price": None,
            "use_strategy_profit_target": True,
            "stop": {
                "rule_type": "hybrid",
                "order_type": "STP",
                "price": None,
                "distance_percent": None,
                "distance_bps": None,
                "maximum_cash_risk": None,
                "volatility_multiple": 1.25,
                "buffer_bps": 8.0,
                "anchor_source": "strategy_swing",
                "anchor_ordinal": "most_recent",
                "structural_timeframe": "strategy",
                "stop_limit_offset_bps": None,
            },
            "trailing": {
                "rule_type": "volatility_trail",
                "amount": None,
                "percent": None,
                "volatility_multiple": 1.0,
                "activation_gain_percent": 0.5,
                "breakeven_buffer_bps": 0.0,
                "structural_timeframe": "",
            },
        }],
    }]
    profiles.append({
        "profile_id": "structural-five-tranche",
        "revision": 1,
        "name": "Structural five-tranche ladder",
        "description": (
            "Five independent OCA slices use the strategy's causal structural/Fibonacci "
            "targets and one strategy-owned adaptive stop. Trailing remains dormant until "
            "an eight-percent favorable move."
        ),
        "origin": "system",
        "editable": True,
        "add_policy": "independent_slice",
        "profit_pocket_transition": "keep_existing",
        "mandatory_catastrophic_backstop": True,
        "emergency_repair_deadline_ms": 500,
        "slices": [
            {
                "slice_id": f"target-{index + 1}",
                "quantity_fraction": 0.2,
                "profit_target_price": None,
                "strategy_profit_target_index": index,
                "stop": {
                    "rule_type": "fixed_price",
                    "order_type": "STP",
                    "price": None,
                    "stop_limit_offset_bps": None,
                },
                "trailing": {
                    "rule_type": "volatility_trail",
                    "volatility_multiple": 2.0,
                    "activation_gain_percent": 8.0,
                },
            }
            for index in range(5)
        ],
    })
    profiles.append({
        "profile_id": "structural-single-target",
        "revision": 1,
        "name": "Structural single target",
        "description": (
            "One full-position OCA group uses the strategy's single causal structural "
            "target, level-derived fixed stop, and always-active broker trailing stop."
        ),
        "origin": "system",
        "editable": True,
        "add_policy": "independent_slice",
        "profit_pocket_transition": "keep_existing",
        "mandatory_catastrophic_backstop": True,
        "emergency_repair_deadline_ms": 500,
        "slices": [{
            "slice_id": "position",
            "quantity_fraction": 1.0,
            "profit_target_price": None,
            "strategy_profit_target_index": 0,
            "stop": {
                "rule_type": "fixed_price",
                "order_type": "STP",
                "price": None,
                "stop_limit_offset_bps": None,
            },
            "trailing": {
                "rule_type": "broker_amount",
                "amount": None,
                "activation_gain_percent": 0.0,
            },
        }],
    })
    return profiles


def _parameters_with_action_policies(
    profile: dict[str, Any],
    rule_sets: list[dict[str, Any]],
    action_policies: list[dict[str, Any]],
) -> dict[str, Any]:
    parameters = deepcopy(dict(profile.get("parameters") or {}))
    lifecycle = dict(profile.get("lifecycle") or {})
    rule_set_catalog = {
        str(rule_set.get("rule_set_id") or ""): dict(rule_set)
        for rule_set in rule_sets
    }
    initial_entry = dict(lifecycle.get("initial_entry") or {})
    parameters["entry_rules"] = {
        "trigger": _materialize_rule_stage(dict(initial_entry.get("opportunity") or {}), rule_set_catalog),
        "confirmation": _materialize_rule_stage(dict(initial_entry.get("confirmation") or {}), rule_set_catalog),
        "veto": _materialize_rule_stage(dict(initial_entry.get("blockers") or {}), rule_set_catalog),
    }
    behavior = deepcopy(dict(lifecycle.get("trading_behavior") or {}))
    phase_modes = deepcopy(dict(lifecycle.get("phase_modes") or {}))
    reentry = deepcopy(dict(lifecycle.get("reentry") or {}))
    reentry_rules = dict(reentry.pop("rules", {}) or {})
    reentry["enabled"] = str(phase_modes.get("reentry") or "automatic") == "automatic"
    parameters["reentry"] = reentry
    exit_rule_sets = list(
        dict(lifecycle.get("exit") or {}).get("rule_sets") or []
    )
    parameters["strategy_behavior"] = behavior
    parameters["phase_policy"] = {
        "initial_entry": {
            "mode": str(phase_modes.get("initial_entry") or "automatic"),
            "capital_request": deepcopy(dict(initial_entry.get("capital_request") or {})),
            "order_intent": deepcopy(dict(initial_entry.get("order_intent") or {})),
            "add_steps": deepcopy(list(initial_entry.get("add_steps") or [])),
        },
        "manage": {
            "mode": str(phase_modes.get("manage") or "automatic"),
        },
        "reentry": {
            "mode": str(phase_modes.get("reentry") or "automatic"),
            "rules": {
                "trigger": _materialize_rule_stage(dict(reentry_rules.get("opportunity") or {}), rule_set_catalog),
                "confirmation": _materialize_rule_stage(dict(reentry_rules.get("confirmation") or {}), rule_set_catalog),
                "veto": _materialize_rule_stage(dict(reentry_rules.get("blockers") or {}), rule_set_catalog),
            },
            "capital_request": deepcopy(dict(reentry.get("capital_request") or {})),
            "order_intent": deepcopy(dict(reentry.get("order_intent") or {})),
        },
        "exit": {"mode": str(phase_modes.get("exit") or "automatic"), "rule_sets": [
            {
                **deepcopy(route),
                "rules": _materialize_rule_stage(dict(route.get("rules") or {}), rule_set_catalog),
            }
            for route in exit_rule_sets
        ]},
    }
    parameters["phase_policy"]["initial_entry"]["add_steps"] = [
        {
            **deepcopy(step),
            "rules": _materialize_rule_stage(dict(step.get("rules") or {}), rule_set_catalog),
        }
        for step in initial_entry.get("add_steps") or []
    ]
    policies = {
        str(row.get("policy_id") or ""): row
        for row in action_policies
        if bool(row.get("enabled", True))
    }
    pocket = policies.get("profit-pocket")
    if pocket:
        _deep_merge(
            parameters.setdefault("profit_pocket", {}),
            {
                **dict(pocket.get("settings") or {}),
                "quantity_fraction": float(
                    dict(pocket.get("quantity") or {}).get("value") or 0.5
                ),
                "minimum_remaining_quantity": float(
                    dict(pocket.get("quantity") or {}).get(
                        "minimum_remaining_quantity"
                    )
                    or 0
                ),
            },
        )
        parameters["profit_pocket"]["enabled"] = True
    else:
        parameters.setdefault("profit_pocket", {})["enabled"] = False
    add = policies.get("confirmed-pullback-add")
    if add:
        add_steps = list(
            parameters.setdefault("phase_policy", {})
            .setdefault("initial_entry", {})
            .get("add_steps") or []
        )
        if add_steps:
            add_steps[0]["maximum_uses"] = int(
                add.get("maximum_uses") or add_steps[0].get("maximum_uses") or 1
            )
            parameters["phase_policy"]["initial_entry"]["add_steps"] = add_steps
    resolved = strategy_executor(
        str(profile.get("definition_id") or ""),
        int(profile.get("definition_revision") or 0),
    ).parameter_resolver(parameters)
    if str(profile.get("profile_id") or "") == "long-momentum-balanced":
        # The squeeze strategy makes decisions on completed one-second frames.
        # Project its flow/liquidity vetoes at that same causal cadence so the
        # historical runtime does not prepare an unused 100ms product for every
        # source-signal symbol.
        for veto_stage in (
            resolved.get("entry_rules", {}).get("veto", {}),
            resolved.get("phase_policy", {})
            .get("reentry", {})
            .get("rules", {})
            .get("veto", {}),
        ):
            for group in [
                *list(veto_stage.get("groups") or []),
                *list(veto_stage.get("rule_sets") or []),
            ]:
                for condition in group.get("conditions") or []:
                    for side in ("left", "right"):
                        if str(condition.get(f"{side}_timeframe") or "") == "100ms":
                            condition[f"{side}_timeframe"] = "1s"
                        interval_key = f"{side}_interval"
                        if (
                            isinstance(condition.get(interval_key), dict)
                            and int(condition[interval_key].get("value") or 0)
                            == 100
                            and str(condition[interval_key].get("unit") or "")
                            == "milliseconds"
                        ):
                            condition[interval_key] = normalize_interval_spec("1s")
    return resolved


def _validate_oms_settings(oms: dict[str, Any]) -> None:
    if str(oms.get("entry_urgency") or "") not in SUPPORTED_URGENCIES:
        raise ValueError("OMS entry urgency is unsupported")
    if str(oms.get("exit_urgency") or "") not in SUPPORTED_URGENCIES:
        raise ValueError("OMS exit urgency is unsupported")
    if float(oms.get("limit_offset_bps") or 0) < 0 or float(oms.get("tick_size") or 0) <= 0:
        raise ValueError("OMS offset cannot be negative and tick size must be positive")
    if str(oms.get("session_routing") or "") != "smart":
        raise ValueError("OMS session routing must use smart broker-aware mode")
    if str(dict(oms.get("protection") or {}).get("stop_method") or "") not in {"structure", "volatility", "hybrid"}:
        raise ValueError("Protection stop method is unsupported")


def _validate_execution_policy_config(payload: dict[str, Any]):
    try:
        return execution_policy_from_payload(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Execution policy {payload.get('policy_id') or '<unknown>'} is invalid: {exc}"
        ) from exc


def _validate_protection_profile_config(payload: dict[str, Any]):
    resolved = deepcopy(payload)
    for raw_slice in resolved.get("slices") or []:
        raw_slice.pop("use_strategy_profit_target", None)
        raw_slice.pop("strategy_profit_target_index", None)
        stop = dict(raw_slice.get("stop") or {})
        anchor_source = str(stop.pop("anchor_source", "") or "")
        stop.pop("anchor_ordinal", None)
        stop.pop("structural_timeframe", None)
        rule_type = str(stop.get("rule_type") or "")
        if anchor_source and anchor_source not in {"strategy_swing", "explicit"}:
            raise ValueError("Protection anchor source must be strategy_swing or explicit")
        if rule_type in {"swing_anchored", "hybrid"} and anchor_source == "strategy_swing":
            stop["anchor"] = {
                "observation_id": "configuration-validation",
                "price": 90.0,
                "confirmed_at": "2000-01-01T00:00:00+00:00",
                "timeframe": "strategy",
                "ordinal": "most_recent",
            }
        if rule_type in {"fixed_price", "catastrophic"} and not stop.get("price"):
            stop["price"] = 90.0
        raw_slice["stop"] = stop
        trailing = dict(raw_slice.get("trailing") or {})
        if (
            str(trailing.get("rule_type") or "") == "broker_amount"
            and not trailing.get("amount")
        ):
            # A null catalog amount is a deliberate strategy-owned binding.
            # Runtime materialization replaces it with the positive amount on
            # the StrategyIntent; use a sentinel only for schema validation.
            trailing["amount"] = 1.0
        raw_slice["trailing"] = trailing
    try:
        return protection_profile_from_payload(resolved)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Protection profile {payload.get('profile_id') or '<unknown>'} is invalid: {exc}"
        ) from exc


def _unique_ids(rows: list[dict[str, Any]], key: str, label: str) -> set[str]:
    values = [str(row.get(key) or "").strip() for row in rows]
    if any(not value for value in values) or len(set(values)) != len(values):
        raise ValueError(f"{label} ids must be present and unique")
    return set(values)


def _without_timestamp(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in payload.items() if key != "updated_at"}


def _deep_merge(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def _account_key(account_id: str, index: int) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in account_id)
    return normalized.strip("-") or f"account-{index + 1}"
