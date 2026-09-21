"""Session-specific certification using the pinned canonical detector recipe.

Saved candidates and their original artifacts remain immutable. New sessions get
separate requests, ticker checkpoints and hash-pinned outputs. A process lock
bounds preparation to one producer; cancelled work retains resumable checkpoints.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, time
import hashlib
import json
import os
import re
import subprocess
from .filtered_v7_preparation import preparation_process
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.backend.historical_signal_occurrence_service import (
    _clock, _load_repository_env, certified_signal_manifest,
    historical_source_native_signal_occurrences,
)
from src.data_provider.calendar import market_sessions

NEW_YORK = ZoneInfo("America/New_York")


def _root() -> Path:
    from src.runtime_paths import runtime_root
    root = Path(os.environ.get("TRADINGML_RUNTIME_ROOT") or runtime_root()).resolve()
    if not root.is_dir():
        raise ValueError(f"Operational runtime root is unavailable: {root}")
    return root


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(path: Path, value: Any) -> None:
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _binary() -> Path:
    default = _root() / "qmd_history_gateway/cargo-target/release/historical_squeeze_replay.exe"
    path = Path(os.environ.get("QMD_HISTORICAL_SIGNAL_PRODUCER") or default).resolve()
    if not path.is_file():
        raise ValueError(f"Canonical signal producer is unavailable: {path}")
    from src.backend.historical_runtime_versions import qmd_source_fingerprint
    result = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=10,
                            **({"creationflags": 0x08000000} if os.name == "nt" else {}))
    try:
        version = json.loads(result.stdout)
    except ValueError:
        version = {}
    if result.returncode or version.get("source_sha256") != qmd_source_fingerprint():
        raise ValueError("Canonical signal producer is outdated; rebuild historical_squeeze_replay from current source")
    return path


def _recipe(stream: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Path]:
    path, manifest = certified_signal_manifest(stream)
    request_path = path.parent.parent / "request.json"
    if _hash(request_path) != manifest.get("request_sha256"):
        raise ValueError("Pinned signal preparation request hash changed; restore its certified request")
    request = json.loads(request_path.read_bytes())
    definition = {k: v for k, v in stream.items() if k != "historical_occurrence_artifact"}
    if request["configuration"]["streams"] != [definition]:
        raise ValueError("Session preparation requires the identical single-stream detector recipe")
    if (manifest.get("timing_policy") != "canonical_sip"
            or manifest.get("activation") != "first_qualifying_signal_through_session_end"
            or request.get("maximum_price_exclusive") != manifest.get("maximum_price_exclusive")):
        raise ValueError("Unsupported certified signal preparation policy")
    population = request.get("population_authority") or {}
    if (population.get("table") != "q_live.feature_tradable_universe_v1"
            or population.get("classification_table") != "q_live.id_symbol_v1"
            or population.get("accepted_types") != ["CS", "ADRC"]):
        raise ValueError("Unsupported signal population authority; explicit migration required")
    return request, manifest, path


def _population(day, *, common_only=True) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Same published, point-in-time classification policy as the original build.
    from src.backend.experimental_structure_book import rows
    _load_repository_env()
    candidates = rows(
        "SELECT u.ticker,u.symbol_id,u.listing_id,u.security_id,u.ibkr_conid,u.product_type,"
        "u.asset_class,u.currency_code,u.exchange_code,u.source_run_id,"
        "s.instrument_type,s.ticker_type_id,toString(s.first_seen_at_utc) classification_first_seen "
        "FROM q_live.feature_tradable_universe_v1 u FINAL "
        "LEFT JOIN q_live.id_symbol_v1 s FINAL ON u.symbol_id=s.symbol_id "
        f"WHERE u.universe_date='{day.isoformat()}' AND u.is_tradable=1 ORDER BY u.ticker"
    )
    included, excluded = [], []
    for row in candidates:
        reason = ""
        if not common_only and not re.fullmatch(r'[A-Z0-9.-]{1,32}', row['ticker']):
            reason = 'unsupported_canonical_ticker_format'
        elif common_only and row["ticker_type_id"] not in {"ticker_type:stocks:cs", "ticker_type:stocks:adrc"}:
            reason = "not_confirmed_common_share"
        elif common_only and (not row.get("classification_first_seen") or row["classification_first_seen"][:10] > day.isoformat()):
            reason = "classification_not_available"
        if reason:
            excluded.append({**row, "reason": reason})
        else:
            included.append(row)
    if not included or len(included) != len({r["ticker"] for r in included}):
        raise ValueError(f"No unambiguous published stock population for {day}")
    if len(included) > 25_000 or any(
        r["product_type"] != "STK" or r["asset_class"] != "stocks" or r["currency_code"] != "USD"
        for r in included
    ):
        raise ValueError(f"Invalid published US-stock population for {day}")
    return included, excluded


def signal_session_plans(stream: dict[str, Any], *, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Read-only coverage/planning. Never run a producer during preflight."""
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("Signal session bounds must be ordered and timezone-aware")
    _, manifest = certified_signal_manifest(stream)
    if _clock(manifest["available_start"], "start") <= start and _clock(manifest["available_end"], "end") >= end:
        return [dict(stream=stream, start=start, end=end, ready=True)]
    recipe, manifest, seed_path = _recipe(stream)
    days = market_sessions(start.astimezone(NEW_YORK).date(), end.astimezone(NEW_YORK).date())
    plans = []
    runtime = _root()
    for day in days:
        session_start = datetime.combine(day, time(4), NEW_YORK)
        session_end = datetime.combine(day, time(20), NEW_YORK)
        left, right = max(start, session_start), min(end, session_end)
        if left >= right:
            continue
        if _clock(manifest["available_start"], "start") <= left and _clock(manifest["available_end"], "end") >= right:
            plans.append(dict(stream=stream, start=left, end=right, ready=True))
            continue
        directory = (runtime / "trading/signal-preparation" / _hash(seed_path) / day.isoformat()).resolve()
        if not directory.is_relative_to(runtime):
            raise ValueError("Signal preparation escaped the runtime root")
        pin = directory / "artifact.json"
        if pin.exists():
            resolved = {**stream, "historical_occurrence_artifact": json.loads(pin.read_bytes())}
            _, saved = certified_signal_manifest(resolved)
            if saved.get("request_sha256") != _hash(directory / "request.json"):
                raise ValueError("Prepared signal request hash changed")
            if _clock(saved["available_start"], "start") > left or _clock(saved["available_end"], "end") < right:
                raise ValueError("Prepared signal coverage does not match its session")
            plans.append(dict(stream=resolved, start=left, end=right, ready=True))
            continue
        binary = _binary()
        population, exclusions = _population(day)
        plans.append(dict(stream=stream, start=left, end=right, ready=False, directory=directory,
                          recipe=recipe, day=day, population=population, exclusions=exclusions, binary=binary))
    if not plans:
        raise ValueError("No market sessions in the requested signal window")
    return plans


def signal_coverage_check(streams: list[dict[str, Any]], *, start: datetime, end: datetime) -> dict[str, Any]:
    ready, pending, errors = 0, 0, []
    for stream in streams:
        if not stream.get("historical_occurrence_artifact"):
            continue
        try:
            for plan in signal_session_plans(stream, start=start, end=end):
                ready += int(plan["ready"])
                pending += int(not plan["ready"])
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
            errors.append(f"{stream.get('signal_stream_id')}: {exc}")
    return dict(id="historical_signal_coverage", label="Historical signal coverage", required=True,
                status="blocked" if errors else "ready",
                summary="; ".join(errors) if errors else (
                    f"{ready} certified session(s) ready; {pending} session(s) will be prepared from canonical data before playback."
                    if pending else f"Certified signal coverage verified for {ready} session(s)."),
                evidence=dict(certified_sessions=ready, preparation_sessions=pending, errors=errors))


def _try_lock(handle) -> bool:
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


def _freeze_request(plan: dict[str, Any]) -> Path:
    directory = plan["directory"]
    directory.mkdir(parents=True, exist_ok=True)
    request_path = directory / "request.json"
    state_path = directory / "plan.json"
    if state_path.exists():
        state = json.loads(state_path.read_bytes())
        if _hash(request_path) != state["request_sha256"]:
            raise ValueError("Frozen signal preparation request changed")
        for name, digest in state["files"].items():
            if _hash(directory / name) != digest:
                raise ValueError(f"Frozen signal population changed: {name}")
        return request_path
    if (directory / "signals").exists():
        raise ValueError("Signal checkpoints have no frozen plan; explicit recovery required")
    _save(directory / "population.json", plan["population"])
    _save(directory / "population-exclusions.json", plan["exclusions"])
    request = deepcopy(plan["recipe"])
    request["configuration"].update(session_key=plan["day"].isoformat(),
        session_start_utc=datetime.combine(plan["day"], time(4), NEW_YORK).isoformat(),
        session_end_utc=datetime.combine(plan["day"], time(20), NEW_YORK).isoformat())
    request["tickers"] = [row["ticker"] for row in plan["population"]]
    request["population_authority"].update(universe_date=plan["day"].isoformat(),
        content_sha256=_hash(directory / "population.json"), row_count=len(plan["population"]),
        excluded_count=len(plan["exclusions"]), exclusions_sha256=_hash(directory / "population-exclusions.json"))
    from collections import Counter
    request["population_authority"]["exclusion_counts"] = dict(Counter(r["ticker_type_id"] for r in plan["exclusions"]))
    _save(request_path, request)
    _save(state_path, dict(request_sha256=_hash(request_path),
                          files={name: _hash(directory / name) for name in ("population.json", "population-exclusions.json")}))
    return request_path


async def _produce(plan: dict[str, Any], *, progress: Callable, stopped: Callable) -> dict[str, Any]:
    request_path = await asyncio.to_thread(_freeze_request, plan)
    directory = plan["directory"]
    manifest_path = directory / "signals/manifest.json"
    pin_path = directory / "artifact.json"
    if pin_path.exists():
        resolved = {**plan["stream"], "historical_occurrence_artifact": json.loads(pin_path.read_bytes())}
        _, manifest = certified_signal_manifest(resolved)
        if manifest.get("request_sha256") != _hash(request_path):
            raise ValueError("Prepared signal authority differs from the frozen request")
        return await asyncio.to_thread(historical_source_native_signal_occurrences, resolved,
                                        start=plan["start"], end=plan["end"])
    if not manifest_path.exists():
        env = dict(os.environ, QMD_HISTORY_ARCHIVE_CLOCK_POLICY="canonical_sip", TRADINGML_RUNTIME_ROOT=str(_root()))
        _load_repository_env()
        env.update({k: v for k, v in os.environ.items() if k not in env})
        with (directory / "producer.log").open("ab") as log:
            async with preparation_process(str(plan["binary"]), str(request_path),
                    str(manifest_path.parent), env=env, stdout=log, stderr=log,
                    **({"creationflags": 0x08000000} if os.name == "nt" else {})) as process:
                while process.poll() is None:
                    if stopped():
                        raise asyncio.CancelledError("Signal preparation stopped")
                    status_path = manifest_path.parent / "status.json"
                    status = json.loads(status_path.read_bytes()) if status_path.exists() else {}
                    progress({**status, "session": plan["day"].isoformat(), "stage": "signal_preparation"})
                    await asyncio.sleep(0.5)
                if process.returncode != 0:
                    raise RuntimeError(f"Canonical signal preparation failed for {plan['day']}; see {directory / 'producer.log'}")
    resolved = {**plan["stream"], "historical_occurrence_artifact":
                dict(manifest_path=str(manifest_path), manifest_sha256=_hash(manifest_path))}
    _, manifest = certified_signal_manifest(resolved)
    if manifest.get("request_sha256") != _hash(request_path):
        raise ValueError("Prepared signal authority differs from the frozen request")
    loaded = await asyncio.to_thread(historical_source_native_signal_occurrences, resolved,
                                     start=plan["start"], end=plan["end"])
    _save(directory / "artifact.json", resolved["historical_occurrence_artifact"])
    return loaded


async def prepared_signal_occurrences(stream: dict[str, Any], *, start: datetime, end: datetime,
                                      progress: Callable = lambda status: None,
                                      stopped: Callable = lambda: False) -> dict[str, Any]:
    plans = await asyncio.to_thread(signal_session_plans, stream, start=start, end=end)
    return await _execute_plans(stream, plans, progress=progress, stopped=stopped)


async def reconstruct_configured_signal_occurrences(stream: dict[str, Any], *, configuration: dict[str, Any],
        start: datetime, end: datetime, progress: Callable = lambda status: None,
        stopped: Callable = lambda: False) -> dict[str, Any]:
    """Certify missing native history using the exact saved detector, without price filters."""
    if stream.get('occurrence_source') != 'qmd_squeeze_episode':
        raise ValueError('Canonical reconstruction requires a supported native detector')
    activation = configuration['signal_activation']
    rule_ids = set(stream.get('inclusion_rule_sets', [])) | set(stream.get('exclusion_rule_sets', []))
    rules = [deepcopy(r) for r in activation['rule_sets'] if r['rule_set_id'] in rule_ids]
    if {r['rule_set_id'] for r in rules} != rule_ids:
        raise ValueError('Canonical signal reconstruction is missing saved detector rules')
    recipe = dict(configuration=dict(streams=[deepcopy(stream)], rule_sets=rules,
        column_catalog=deepcopy(activation['column_catalog'])), maximum_price_exclusive=None,
        population_authority=dict(table='q_live.feature_tradable_universe_v1',
            classification_table='q_live.id_symbol_v1', accepted_types=['STK'],
            policy='published_tradable_canonical_us_stocks_v1'))
    binary = await asyncio.to_thread(_binary)
    identity = hashlib.sha256(json.dumps(dict(recipe=recipe, producer_sha256=_hash(binary)),
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    recipe['configuration']['configuration_revision'] = identity
    plans = []
    for day in market_sessions(start.astimezone(NEW_YORK).date(), end.astimezone(NEW_YORK).date()):
        left = max(start, datetime.combine(day, time(4), NEW_YORK))
        right = min(end, datetime.combine(day, time(20), NEW_YORK))
        if left >= right:
            continue
        directory = _root() / 'trading/signal-preparation/configured-v1' / identity / day.isoformat()
        if (directory / 'plan.json').exists():
            # _freeze_request verifies these frozen inputs under the producer
            # lock. Cache reuse must not depend on today's reference database.
            population, exclusions = [], []
            plans.append(dict(stream=stream, start=left, end=right, ready=False, directory=directory,
                recipe=recipe, day=day, population=population, exclusions=exclusions, binary=binary))
            continue
        population, exclusions = await asyncio.to_thread(_population, day, common_only=False)
        plans.append(dict(stream=stream, start=left, end=right, ready=False, directory=directory,
            recipe=recipe, day=day, population=population, exclusions=exclusions, binary=binary))
    if not plans:
        raise ValueError('No market sessions in requested signal reconstruction')
    return await _execute_plans(stream, plans, progress=progress, stopped=stopped)


async def _execute_plans(stream, plans, *, progress, stopped):
    results = []
    for plan in plans:
        if stopped():
            raise asyncio.CancelledError("Signal preparation stopped")
        if plan["ready"]:
            loaded = await asyncio.to_thread(historical_source_native_signal_occurrences, plan["stream"],
                                             start=plan["start"], end=plan["end"])
        else:
            lock_root = _root() / "trading/signal-preparation"
            lock_root.mkdir(parents=True, exist_ok=True)
            # OS locks release after process failure, so stale lock files cannot
            # block recovery. One producer across all dates and API processes.
            with (lock_root / "producer.lock").open("a+b") as handle:
                if handle.seek(0, 2) == 0:
                    handle.write(b"0")
                    handle.flush()
                while not _try_lock(handle):
                    if stopped():
                        raise asyncio.CancelledError("Signal preparation stopped while queued")
                    progress(dict(stage="signal_preparation_queued", session=plan["day"].isoformat()))
                    await asyncio.sleep(0.5)
                loaded = await _produce(plan, progress=progress, stopped=stopped)
        results.append(loaded)
    if len(results) == 1:
        return results[0]
    occurrences = sorted((event for result in results for event in result["occurrences"]),
                         key=lambda event: (_clock(event["available_at"], "available_at"), event["ticker"]))
    authority = dict(authority="qmd_certified_signal_sessions_v1", signal_stream_id=stream["signal_stream_id"],
                     sessions=[result["authority"] for result in results], row_count=len(occurrences))
    authority["content_hash"] = hashlib.sha256(json.dumps(authority, sort_keys=True).encode()).hexdigest()
    return dict(occurrences=occurrences, authority=authority)
