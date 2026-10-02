"""Bounded laptop or workstation integration probe for Strategy 1 Backtest.

This invokes the public controller start path after full market preflight.
--apply persists a new normalized ClickHouse test journal, never a run-local
file or market product.
It is for integration validation only, not a user-facing launch workaround.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing, contextmanager
import cProfile
from datetime import date, datetime, time, timedelta
from io import StringIO
import json
import os
from pathlib import Path
import platform
import pstats
import re
import sys
from threading import Lock
from time import perf_counter
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True


SECRET_ROOT = Path(r"D:\TradingML\secrets")
RUNTIME_ROOT = Path(r"D:\TradingML\runtimes")


def _load_private_credentials() -> None:
    from src.backend.managed_backtest_credentials import _FILES, _private_values

    if not RUNTIME_ROOT.is_dir():
        raise RuntimeError("Managed runtime root is unavailable")
    if platform.node().upper() == "DESKTOP-SAAI85T":
        paths = {name: SECRET_ROOT / name for name in _FILES}
        reader = SECRET_ROOT / "backtest_v3_read.env"
        keeper = {}
    else:
        catalog = json.loads((ROOT / "scripts" / "service_catalog.json")
                             .read_text(encoding="utf-8"))
        managed = catalog["services"]["backend"]["environment"]
        paths = {
            "trading_journal.env": Path(managed["TRADING_JOURNAL_CREDENTIAL_FILE"]),
            "backtest_v4_runner.env": Path(managed["BACKTEST_V4_RUNNER_CREDENTIAL_FILE"]),
        }
        reader = Path(managed["BACKTEST_V3_READ_CREDENTIAL_FILE"])
        keeper = {key: managed[key] for key in (
            "TRADING_KEEPER_LAN_HOST", "TRADING_KEEPER_LAN_PORT",
            "TRADING_KEEPER_CA_FILE", "TRADING_KEEPER_CLIENT_CERT_FILE",
            "TRADING_KEEPER_CLIENT_KEY_FILE")}
    if not reader.is_file() or any(not path.is_file() for path in paths.values()):
        raise RuntimeError("Managed Backtest credential files are unavailable")
    if (os.environ.get("BACKTEST_V3_READ_CREDENTIAL_FILE")
            and os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] != str(reader)):
        raise RuntimeError("Managed Backtest reader path conflicts with the environment")
    if any(key in os.environ and os.environ[key] != value
           for key, value in keeper.items()):
        raise RuntimeError("Managed Keeper settings conflict with the environment")
    values = {key: value for name, path in paths.items()
              for key, value in _private_values(path, _FILES[name]).items()}
    if any(key in os.environ and os.environ[key] != value
           for key, value in values.items()):
        raise RuntimeError("Managed Backtest credentials conflict with the environment")
    os.environ.update(keeper)
    os.environ.update(values)
    os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] = str(reader)


def _print_completed_profile(controller) -> None:
    """Report bounded wall stages and writer work, without dumping journal data."""
    for name, row in sorted(controller._stage_timings.items()):
        print(f"Stage {name}: calls={row['calls']} "
              f"wall_s={row['seconds']:.3f} "
              f"max_call_s={row['maximum_seconds']:.3f}", flush=True)
    metrics = getattr(controller, "_journal_writer_final_metrics", None)
    if (not isinstance(metrics, dict) or metrics.get("committed_units", 0) < 1
            or metrics.get("failed_units") != 0 or metrics.get("failed")
            or metrics.get("queue_depth") != 0):
        raise RuntimeError("Completed Backtest lacks a drained typed journal profile")
    print("Journal writer: "
          f"committed_units={metrics['committed_units']} "
          f"event_rows={metrics['committed_event_rows']} "
          f"failed_units={metrics['failed_units']} "
          f"worker_s={metrics['publish_ns_total'] / 1e9:.3f} "
          f"max_unit_s={metrics['publish_ns_max'] / 1e9:.3f} "
          f"queue_capacity={metrics['queue_capacity']}", flush=True)
    if (metrics.get("compound_prepare_ns_total")
            or metrics.get("compound_publish_ns_total")):
        print("  Compound commit: "
              f"prepare_s={metrics['compound_prepare_ns_total'] / 1e9:.3f} "
              f"publish_s={metrics['compound_publish_ns_total'] / 1e9:.3f}", flush=True)
        for stage, duration_ns in sorted(
                metrics.get("compound_publish_stages_ns", {}).items(),
                key=lambda item: item[1], reverse=True):
            print(f"    {stage}: {duration_ns / 1e9:.3f}s", flush=True)
    by_unit = metrics.get("publish_by_unit")
    if (not isinstance(by_unit, dict)
            or sum(row["units"] for row in by_unit.values())
            != metrics["committed_units"]
            or sum(row["event_rows"] for row in by_unit.values())
            != metrics["committed_event_rows"]
            or sum(row["publish_ns_total"] for row in by_unit.values())
            != metrics["publish_ns_total"]):
        raise RuntimeError("Journal family timings do not reconcile with committed units")
    for family, row in sorted(
            by_unit.items(), key=lambda item: -item[1]["publish_ns_total"]):
        print(f"  {family}: units={row['units']} "
              f"event_rows={row['event_rows']} "
              f"worker_s={row['publish_ns_total'] / 1e9:.3f} "
              f"max_s={row['publish_ns_max'] / 1e9:.3f}", flush=True)


def _audit_causal_journal(run_id: str) -> None:
    """Read every verified page and reject backdated decision descendants."""
    from src.backend.backtest_v4_saved_review import load_v4_terminal_review_page
    from src.trading_runtime.arte_journal_writer import (
        _literal, backtest_v4_operator_client_from_env,
    )

    from collections import Counter

    intents: dict[str, datetime] = {}
    event_families: Counter[tuple[str, str, str]] = Counter()
    risk_state_by_account: dict[str, tuple[tuple[str, str], ...]] = {}
    repeated_risk_scalars = 0
    risk_records: list[tuple[str, str, int, tuple[tuple[str, str], ...]]] = []
    linked = sequence = 0
    with closing(backtest_v4_operator_client_from_env()) as client:
        while True:
            page = load_v4_terminal_review_page(
                client, run_id, after_sequence=sequence, limit=1000)
            if (page["status"] != "completed"
                    or not page["market_cursor_verified"]
                    or page["limitations"]):
                raise RuntimeError("Strategy 1 terminal authority is incomplete")
            for row in page["events"]:
                event, detail = row["event"], row["detail"]
                if int(event["sequence"]) != sequence + 1:
                    raise RuntimeError("Strategy 1 terminal journal has a sequence gap")
                sequence += 1
                at = datetime.fromisoformat(event["event_time"])
                family = row["detail_family"]
                event_families[(event["category"], event["entity_type"], family)] += 1
                if family == "trading_account_risk_state_v1":
                    account = str(detail["account_id"])
                    # Measure only exact normalized scalar repetition. Reasons
                    # and timestamps remain distinct journal evidence; this
                    # statistic does not authorize suppressing any record.
                    scalar = tuple(sorted((key, str(value)) for key, value in
                        detail.items() if key not in {
                            "record_id", "run_id", "event_month", "batch_id",
                            "source_event_time", "content_hash", "reason_count",
                        }))
                    if risk_state_by_account.get(account) == scalar:
                        repeated_risk_scalars += 1
                    risk_state_by_account[account] = scalar
                    risk_records.append((account, str(detail["record_id"]),
                                         int(detail["reason_count"]), scalar))
                if family == "trading_strategy_intent_v1":
                    identity = detail["intent_id"]
                    if identity in intents:
                        raise RuntimeError("Strategy 1 journal repeats an intent")
                    intents[identity] = at
                else:
                    key = {
                        "trading_portfolio_decision_v1": "request_id",
                        "trading_portfolio_reservation_event_v1": "intent_id",
                        "trading_oms_group_state_v1": "strategy_intent_id",
                    }.get(family)
                    identity = detail.get(key) if key else None
                    if identity:
                        source = intents.get(identity)
                        if source is None or at < source:
                            raise RuntimeError(
                                "Strategy 1 journal action precedes its intent")
                        linked += 1
            if page["complete"]:
                if (sequence != page["verified_sequence"]
                        or page["next_sequence"] != sequence
                        or not intents or not linked):
                    raise RuntimeError("Strategy 1 terminal journal audit is incomplete")
                break
            if page["next_sequence"] != sequence:
                raise RuntimeError("Strategy 1 terminal page did not advance")
        reason_rows = [json.loads(line) for line in client.execute(
            "SELECT parent_record_id,ordinal,reason "
            "FROM arte.trading_account_risk_reason_v1 "
            f"WHERE run_id={_literal(run_id)} "
            "ORDER BY parent_record_id,ordinal FORMAT JSONEachRow"
        ).splitlines() if line.strip()]
    reasons_by_parent: dict[str, list[tuple[int, str]]] = {}
    for row in reason_rows:
        if set(row) != {"parent_record_id", "ordinal", "reason"}:
            raise RuntimeError("Risk reason audit returned an invalid typed row")
        reasons_by_parent.setdefault(str(row["parent_record_id"]), []).append(
            (int(row["ordinal"]), str(row["reason"])))
    prior_risk: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, ...]]] = {}
    exact_risk_repeats = 0
    for account, record_id, reason_count, scalar in risk_records:
        reasons = reasons_by_parent.pop(record_id, [])
        if (len(reasons) != reason_count
                or [ordinal for ordinal, _ in reasons] != list(range(reason_count))):
            raise RuntimeError("Risk reason audit differs from its verified parent")
        state = (scalar, tuple(reason for _, reason in reasons))
        exact_risk_repeats += prior_risk.get(account) == state
        prior_risk[account] = state
    if reasons_by_parent:
        raise RuntimeError("Risk reason audit found an orphan typed parent")
    print(f"Causal journal: events={sequence} intents={len(intents)} "
          f"linked_actions={linked} backdated=0", flush=True)
    if sum(event_families.values()) != sequence:
        raise RuntimeError("Strategy 1 journal family inventory is incomplete")
    for (category, entity_type, family), count in event_families.most_common():
        print(f"  event_family {category}/{entity_type}/{family}: {count}", flush=True)
    print(f"  risk_scalar_repeats={repeated_risk_scalars}/"
          f"{sum(count for (_, _, family), count in event_families.items() if family == 'trading_account_risk_state_v1')}",
          flush=True)
    print(f"  risk_exact_state_repeats={exact_risk_repeats}/{len(risk_records)}",
          flush=True)


def _profile_preflight_call(call, **kwargs):
    """Profile the worker-thread preflight itself, not its asyncio caller."""
    profile = cProfile.Profile()
    sql_profile = _SqlCallProfile(by_source=True)
    try:
        with _profile_sql_calls(sql_profile):
            return profile.runcall(call, **kwargs)
    finally:
        sql_profile.print_summary(limit=12)
        output = StringIO()
        pstats.Stats(profile, stream=output).sort_stats("cumulative").print_stats(25)
        print("Preflight call profile (top 25 cumulative seconds):", flush=True)
        print(output.getvalue(), flush=True)


class _SqlCallProfile:
    """Per-process HTTP timing; SQL text and credentials are never retained."""

    def __init__(self, *, by_source: bool = False) -> None:
        self._lock = Lock()
        self._by_source = by_source
        self._bins: dict[str, tuple[int, float]] = {}
        self._v7_stream_reads = 0
        self._v7_stream_iteration_seconds = 0.0

    @staticmethod
    def statement(sql: str | bytes) -> str:
        """Classify only a bounded binary request header, never its row payload."""
        if isinstance(sql, bytes):
            return sql[:8192].split(b"\n", 1)[0].decode("utf-8", errors="replace")
        return sql

    @staticmethod
    def category(sql: str | bytes) -> str:
        sql = _SqlCallProfile.statement(sql)
        if "FROM arte.bars_v1" in sql and "AND resolution_ms=1000" in sql:
            return "v7_completed_second_read"
        journal = "arte.trading_" in sql.lower()
        insert = sql.lstrip().upper().startswith("INSERT ")
        return ("journal" if journal else "market_or_control") + (
            "_insert" if insert else "_read")

    @staticmethod
    def source_category(sql: str | bytes) -> str:
        """Bounded diagnostic label; never retain SQL text or row values."""
        sql = _SqlCallProfile.statement(sql)
        sources = set(re.findall(
            r"\b(?:FROM|JOIN)\s+((?:arte|system|q_live)\.[a-z_][a-z0-9_]*)\b",
            sql, flags=re.IGNORECASE))
        if not sources:
            return "other_select"
        if len(sources) == 1:
            return next(iter(sources)).lower()
        return "multi_source_select"

    def record(self, sql: str | bytes, elapsed: float) -> None:
        category = (self.source_category(sql) if self._by_source
                    else self.category(sql))
        with self._lock:
            calls, seconds = self._bins.get(category, (0, 0.0))
            self._bins[category] = (calls + 1, seconds + elapsed)

    def record_stream(self, sql: str) -> None:
        if self.category(sql) == "v7_completed_second_read":
            with self._lock:
                self._v7_stream_reads += 1

    def record_stream_iteration(self, sql: str, elapsed: float) -> None:
        if self.category(sql) == "v7_completed_second_read":
            with self._lock:
                self._v7_stream_iteration_seconds += elapsed

    def print_summary(self, *, limit: int | None = None) -> None:
        rows = sorted(self._bins.items(), key=(
            (lambda item: -item[1][1]) if self._by_source
            else (lambda item: item[0])))
        for category, (calls, seconds) in rows[:limit]:
            print(f"ClickHouse {category}: calls={calls} "
                  f"client_s={seconds:.3f}", flush=True)
        if limit is not None and len(rows) > limit:
            print(f"ClickHouse other sources: {len(rows) - limit} hidden", flush=True)
        if not self._by_source:
            print(f"ClickHouse v7_completed_second_stream: "
                  f"calls={self._v7_stream_reads} "
                  f"iterator_s={self._v7_stream_iteration_seconds:.3f}", flush=True)


@contextmanager
def _profile_sql_calls(profile: _SqlCallProfile):
    from research.mlops.clickhouse import ClickHouseHttpClient

    original = ClickHouseHttpClient.execute
    original_stream = ClickHouseHttpClient.iter_json_each_row

    def timed_execute(client, sql, *args, **kwargs):
        started = perf_counter()
        try:
            return original(client, sql, *args, **kwargs)
        finally:
            profile.record(sql, perf_counter() - started)

    def counted_stream(client, sql, *args, **kwargs):
        result = original_stream(client, sql, *args, **kwargs)
        profile.record_stream(sql)

        def measured_rows():
            iterator = iter(result)
            iteration_seconds = 0.0
            try:
                while True:
                    started = perf_counter()
                    try:
                        row = next(iterator)
                    except StopIteration:
                        iteration_seconds += perf_counter() - started
                        return
                    except BaseException:
                        iteration_seconds += perf_counter() - started
                        raise
                    iteration_seconds += perf_counter() - started
                    yield row
            finally:
                profile.record_stream_iteration(sql, iteration_seconds)
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()

        return measured_rows()

    ClickHouseHttpClient.execute = timed_execute
    ClickHouseHttpClient.iter_json_each_row = counted_stream
    try:
        yield
    finally:
        ClickHouseHttpClient.execute = original
        ClickHouseHttpClient.iter_json_each_row = original_stream


async def _run(day: date, ticker: str, *, apply: bool, minutes: int,
               initial_cash: int = 10_000,
               profile_preflight: bool = False,
               profile_execution: bool = False,
               preflight_repeats: int = 1,
               crash_after_checkpoint: bool = False) -> None:
    from src.backend.replay_run_service import (
        ReplayRunController, ReplayRunDefinition, backtest_preflight,
    )
    from src.backend.trading_configuration_service import backtest_configuration_snapshot
    from src.trading_runtime.runtime import RunMode

    revision = backtest_configuration_snapshot()
    selected = (ticker,) if ticker else ()
    end_time = (datetime.combine(day, time(4))
                + timedelta(minutes=minutes)).time()
    if preflight_repeats not in (1, 2) or (apply and preflight_repeats != 1):
        raise ValueError("Read-only preflight repeats must be one or two")
    if type(initial_cash) is not int or not 1_000 <= initial_cash <= 1_000_000_000:
        raise ValueError("Initial cash must match the Backtest UI's supported range")
    preflight = None
    first_tokens = None
    for repeat in range(preflight_repeats):
        began = perf_counter()
        current = await asyncio.to_thread(
            _profile_preflight_call if profile_preflight else backtest_preflight,
            **({"call": backtest_preflight} if profile_preflight else {}),
            anchor_date=day + timedelta(days=1),
            session_count=1, start_time=time(4), end_time=end_time,
            tickers=selected, configuration_revision=revision)
        window = tuple(current["window"]["sessions"])
        if window != (day.isoformat(),):
            raise RuntimeError("Strategy 1 integration selected a different exchange day")
        blocked = {row["id"]: row["summary"] for row in current["checks"]
                   if row.get("required", True) and row["status"] != "ready"}
        print(f"Preflight {repeat+1}/{preflight_repeats} {day} "
              f"{ticker or 'full-market'}: {perf_counter()-began:.3f}s; "
              f"unresolved={tuple(blocked)}", flush=True)
        if blocked or not current["ready"]:
            raise RuntimeError("Strategy 1 integration lacks a required input: "
                               + "; ".join(f"{key}: {value}" for key, value in blocked.items()))
        tokens = (current["market_data_plan"]["token"],
                  current["causal_v7_plan"]["token"])
        if first_tokens is not None and tokens != first_tokens:
            raise RuntimeError("Repeated preflight changed its certified source plans")
        first_tokens = tokens
        preflight = current
    assert preflight is not None
    if not apply:
        print("Plan only: no typed journal or run was created", flush=True)
        return
    definition = ReplayRunDefinition(
        session_date=day, final_session_date=day,
        start_time=time(4), end_time=end_time, initial_cash=initial_cash,
        configuration_revision=revision, execution_interval="100ms",
        market_data_plan=dict(preflight["market_data_plan"]),
        causal_v7_plan=dict(preflight["causal_v7_plan"]),
        mode=RunMode.BACKTEST, simulation_profile="baseline",
        new_order_activation_delay_ms=0.0,
        experimental_structure_book="level-book-v7", tickers=selected)
    controller = ReplayRunController(definition, runtime_root=RUNTIME_ROOT)
    open_journal = controller._open_fixed_journal

    async def traced_open_journal():
        try:
            await open_journal()
            if crash_after_checkpoint:
                publisher = controller._journal_publisher
                if publisher is None:
                    raise RuntimeError("Crash probe has no typed journal publisher")
                enqueue = publisher.enqueue_checkpoint

                def crash_after_durable_receipt(**kwargs):
                    receipt = enqueue(**kwargs)

                    def completed(done):
                        durable = done.result()
                        if controller.processed_events > 0:
                            print("Intentional crash after durable V4 checkpoint: "
                                  f"run={controller.run_id} "
                                  f"sequence={durable.last_sequence}", flush=True)
                            os._exit(77)

                    receipt.add_done_callback(completed)
                    return receipt

                publisher.enqueue_checkpoint = crash_after_durable_receipt
        except Exception:
            # Emit a bounded Python stack, never SQL, request headers,
            # credential values, or private files.
            traceback.print_exc(limit=12)
            raise

    controller._open_fixed_journal = traced_open_journal
    if crash_after_checkpoint:
        print(f"Crash probe run_id={controller.run_id}; expected_exit=77", flush=True)
    began = perf_counter()
    sql_profile = _SqlCallProfile()
    execution_profile = cProfile.Profile() if profile_execution else None
    if execution_profile is not None:
        execution_profile.enable()
    try:
        with _profile_sql_calls(sql_profile):
            await controller.start()
            if controller._task is None:
                raise RuntimeError("Public Backtest start did not schedule execution")
            await controller._task
    finally:
        if execution_profile is not None:
            execution_profile.disable()
            output = StringIO()
            pstats.Stats(execution_profile, stream=output).sort_stats(
                "cumulative").print_stats(35)
            pstats.Stats(execution_profile, stream=output).sort_stats(
                "cumulative").print_stats("streaming_level_book", 30)
            pstats.Stats(execution_profile, stream=output).sort_stats(
                "tottime").print_stats("streaming_level_book", 30)
            print("Execution event-loop profile (top 35; worker threads excluded):",
                  flush=True)
            print(output.getvalue(), flush=True)
    elapsed = perf_counter() - began
    print(f"Strategy 1 probe initial_cash={initial_cash} run_id={controller.run_id} "
          f"status={controller.status} elapsed_s={elapsed:.3f} "
          f"processed_rows={controller.processed_events} "
          f"error={controller.error[:300]}", flush=True)
    if controller.status != "completed":
        # A diagnostic full-session run may reach a failed terminal
        # because positions remain open at 20:00. Report bounded timings, but
        # retain the nonzero exit and never label that run release-accepted.
        for name, row in sorted(controller._stage_timings.items()):
            print(f"Stage {name}: calls={row['calls']} "
                  f"wall_s={row['seconds']:.3f} "
                  f"max_call_s={row['maximum_seconds']:.3f}", flush=True)
        sql_profile.print_summary()
        raise RuntimeError("Strategy 1 integration run did not complete")
    if controller.run_dir.exists():
        raise RuntimeError("Strategy 1 integration wrote a run-local directory")
    _print_completed_profile(controller)
    _audit_causal_journal(controller.run_id)
    sql_profile.print_summary()


async def _resume_interrupted(run_id: str, *, via_service: bool = False) -> None:
    """Exercise the cold actor/writer handoff before opening the app route."""
    from src.backend.replay_run_service import ReplayRunService
    from src.backend import backtest_typed_projection
    from src.trading_runtime.runtime import TradingRuntime

    project_record = backtest_typed_projection.project_journal_record

    def trace_project_record(record, **kwargs):
        try:
            return project_record(record, **kwargs)
        except ValueError:
            if (record.category, record.entity_type) == ("strategy", "strategy_intent"):
                payload = record.payload
                expected = kwargs.get("expected_config") or {}
                print("Cold intent projection mismatch: "
                      f"sequence={record.sequence} action={payload.get('action')} "
                      f"keys={sorted(payload)} "
                      f"identity={(payload.get('strategy_id'), payload.get('strategy_revision'))} "
                      f"expected={(expected.get('strategy_id'), expected.get('strategy_revision'))}",
                      flush=True)
            raise

    backtest_typed_projection.project_journal_record = trace_project_record

    execute_intents = TradingRuntime._execute_intents

    async def trace_protection(self, *args, **kwargs):
        result = await execute_intents(self, *args, **kwargs)
        if kwargs.get("strategy_one_assignment_id") and result:
            for row in result:
                if row.get("order_group") is None:
                    decision = row.get("decision") or {}
                    print("Cold protection result: "
                          f"status={decision.get('status')} "
                          f"reason={str(decision.get('reason') or '')[:300]}",
                          flush=True)
        return result

    TradingRuntime._execute_intents = trace_protection

    service = ReplayRunService(
        runtime_root=RUNTIME_ROOT, allow_typed_backtest_resume=via_service)
    if via_service:
        controller = await service.resume(run_id)
    else:
        definition = await asyncio.to_thread(
            service._load_typed_backtest_resume_definition, run_id)
        if definition is None:
            raise RuntimeError("Interrupted V4 Backtest definition is absent")
        controller = await service._prepare_typed_v4_resume(run_id, definition)
    began = perf_counter()
    try:
        if not via_service:
            await controller.start()
        if controller._task is None:
            raise RuntimeError("Recovered Backtest did not schedule execution")
        await controller._task
    finally:
        if controller._task is None:
            await controller._close_fixed_journal()
    elapsed = perf_counter() - began
    print(f"Strategy 1 cold resume run_id={run_id} status={controller.status} "
          f"execution_s={elapsed:.3f} processed_rows={controller.processed_events} "
          f"error={controller.error[:300]}", flush=True)
    if controller.status != "completed" or controller.run_dir.exists():
        raise RuntimeError("Cold V4 Backtest did not complete without a disk run")
    _print_completed_profile(controller)
    _audit_causal_journal(run_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=date.fromisoformat,
                        default=date(2026, 8, 18))
    parser.add_argument("--ticker", default="",
                        help="optional single-symbol probe; omit for the full market")
    parser.add_argument("--minutes", type=int, default=10,
                        help="whole minutes from 04:00 ET, at most 960 (20:00)")
    parser.add_argument("--initial-cash", type=int, default=10_000,
                        help="simulated account cash; default matches the Backtest UI")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--audit-run-id", default="",
                        help="read-only causal audit of a completed Strategy 1 run")
    parser.add_argument("--resume-run-id", default="",
                        help="integration-only cold resume of an interrupted V4 run")
    parser.add_argument("--resume-via-service", action="store_true",
                        help="exercise the public Backtest service resume path")
    parser.add_argument("--profile-preflight", action="store_true",
                        help="show the slowest preflight calls; does not create market data")
    parser.add_argument("--preflight-repeats", type=int, choices=(1, 2), default=1,
                        help="repeat read-only preflight in one process to measure cache reuse")
    parser.add_argument("--profile-execution", action="store_true",
                        help="show main event-loop calls; profile overhead affects wall time")
    parser.add_argument("--crash-after-checkpoint", action="store_true",
                        help="integration-only: exit 77 after a durable running cursor")
    args = parser.parse_args()
    if args.resume_run_id:
        if args.apply or args.audit_run_id or args.crash_after_checkpoint:
            parser.error("Cold resume is exclusive of launch, audit, and crash modes")
        _load_private_credentials()
        asyncio.run(_resume_interrupted(
            args.resume_run_id, via_service=args.resume_via_service))
        return
    if args.resume_via_service:
        parser.error("--resume-via-service requires --resume-run-id")
    if args.audit_run_id:
        if args.apply or args.profile_preflight or args.profile_execution:
            parser.error("Saved-run audit is read-only and cannot start a probe")
        _load_private_credentials()
        _audit_causal_journal(args.audit_run_id)
        return
    if args.apply and args.preflight_repeats != 1:
        parser.error("Repeated preflight is read-only; omit --apply")
    if args.crash_after_checkpoint and not args.apply:
        parser.error("Intentional crash requires an explicit --apply test run")
    if args.ticker and (not args.ticker.isascii() or not args.ticker.isalnum()):
        raise ValueError("Integration ticker must be an ASCII market symbol")
    if not 1 <= args.minutes <= 960:
        raise ValueError("Integration horizon must be one to 960 minutes")
    if not 1_000 <= args.initial_cash <= 1_000_000_000:
        parser.error("Initial cash must be between 1,000 and 1,000,000,000")
    _load_private_credentials()
    asyncio.run(_run(args.session, args.ticker, apply=args.apply,
                     minutes=args.minutes, initial_cash=args.initial_cash,
                     profile_preflight=args.profile_preflight,
                     profile_execution=args.profile_execution,
                     preflight_repeats=args.preflight_repeats,
                     crash_after_checkpoint=args.crash_after_checkpoint))


if __name__ == "__main__":
    main()
