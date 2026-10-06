"""Prepared Strategy 35 scalar persistence; no table/writer admission yet.

The distinct family retains all four native trade counts and the completed
observation clock. Hash, producer-attempt, committed-prefix and live financial
verification remain mandatory integration gates, not claims of this mapper.
"""
from src.trading_runtime.numbered_fixed_strategy import declared_fixed_rule
from dataclasses import fields
from types import MappingProxyType
from collections.abc import Mapping
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from .arte_journal_schema import TableContract
from .strategy_liquidity_fade_failure import LiquidityFadeCandle, LiquidityFadeFailure
from .strategy_liquidity_fade_exit import liquidity_fade_exit_intent, validate_liquidity_fade_witness


LIQUIDITY_FADE_FAILURE = TableContract("trading_liquidity_fade_failure_v4", (
    ("record_id", "UUID"), ("parent_record_id", "UUID"), ("run_id", "String"),
    ("event_month", "Date"), ("batch_id", "UUID"), ("strategy_number", "UInt32"),
    ("source_entry_intent_id", "UUID"), ("assignment_id", "String"),
    ("source_build_id", "String"), ("source_bars_attempt_id", "UUID"),
    ("source_indicators_attempt_id", "UUID"), ("source_liquidity_attempt_id", "UUID"),
    ("source_market_plan_token", "FixedString(64)"),
    ("source_manager_snapshot_id", "UUID"),
    ("source_manager_checkpoint_sequence", "UInt64"),
    ("source_manager_snapshot_hash", "FixedString(64)"),
    ("source_broker_snapshot_id", "UUID"),
    ("source_broker_snapshot_hash", "FixedString(64)"),
    ("boundary_ms", "UInt32"), ("first_held_boundary_ms", "UInt32"),
    ("reference_ask", "Decimal(38, 18)"), ("initial_stop", "Decimal(38, 18)"),
    ("completed_five_second_boundary_ms", "UInt32"), ("completed_close_int", "UInt64"),
    ("macd_line", "Float64"), ("macd_signal", "Float64"),
    ("bid", "Decimal(38, 18)"), ("ask", "Decimal(38, 18)"), ("quote_age_us", "UInt64"),
    ("trade_count_0", "UInt64"), ("trade_count_1", "UInt64"),
    ("trade_count_2", "UInt64"), ("trade_count_3", "UInt64"),
    ("content_hash", "FixedString(64)"),
), "toYYYYMM(event_month)", "run_id, parent_record_id, record_id")
TABLES = (LIQUIDITY_FADE_FAILURE,)

CHECKPOINT_REFERENCE_FIELDS = (
    "source_manager_snapshot_id", "source_manager_checkpoint_sequence", "source_manager_snapshot_hash",
    "source_broker_snapshot_id", "source_broker_snapshot_hash",
)


def validate_liquidity_checkpoint_reference(row):
    """Require exact manager/broker pointers; native verification is separate."""
    sequence = row.get("source_manager_checkpoint_sequence")
    if type(sequence) is not int or not 0 < sequence < 2**64:
        raise ValueError("Liquidity fade lacks an exact checkpoint sequence")
    for family in ("manager", "broker"):
        identity = row.get(f"source_{family}_snapshot_id")
        digest = row.get(f"source_{family}_snapshot_hash")
        if (type(identity) is not str or str(UUID(identity)) != identity or UUID(identity).int == 0
                or type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("Liquidity fade lacks an exact " + family + " checkpoint reference")


def validate_liquidity_observation_source(row):
    """Typed identity validation only; caller must independently bind source."""
    if (not isinstance(row, Mapping) or type(row.get("source_build_id")) is not str
            or not re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", row["source_build_id"])
            or type(row.get("source_market_plan_token")) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", row["source_market_plan_token"])
            or any(type(row.get(name)) is not str
                   or str(UUID(row[name])) != row[name] for name in
                   ("source_bars_attempt_id", "source_indicators_attempt_id", "source_liquidity_attempt_id"))):
        raise ValueError("Liquidity fade lacks exact producer source identity")


def project_liquidity_fade_failure(
    witness, intent, financial, *, session_date, source_entry_intent_id,
    run_id, batch_id, parent_record_id, source_build_id, source_bars_attempt_id,
    source_indicators_attempt_id, source_liquidity_attempt_id, source_market_plan_token,
    source_manager_snapshot_id, source_manager_checkpoint_sequence, source_manager_snapshot_hash,
    source_broker_snapshot_id, source_broker_snapshot_hash,
    strategy_number=35,
):
    """Project the exact prepared factory intent, retaining producer identities."""
    expected = liquidity_fade_exit_intent(witness, financial,
        session_date=session_date, source_entry_intent_id=source_entry_intent_id,
        strategy_number=strategy_number)
    if intent != expected or type(run_id) is not str or not run_id:
        raise ValueError("Liquidity fade differs from its exact factory intent")
    for value in (batch_id, parent_record_id, source_entry_intent_id):
        UUID(value)
    source = dict(source_build_id=source_build_id, source_bars_attempt_id=source_bars_attempt_id,
                  source_indicators_attempt_id=source_indicators_attempt_id,
                  source_liquidity_attempt_id=source_liquidity_attempt_id,
                  source_market_plan_token=source_market_plan_token)
    validate_liquidity_observation_source(source)
    checkpoint = dict(source_manager_snapshot_id=source_manager_snapshot_id,
                      source_manager_checkpoint_sequence=source_manager_checkpoint_sequence,
                      source_manager_snapshot_hash=source_manager_snapshot_hash,
                      source_broker_snapshot_id=source_broker_snapshot_id,
                      source_broker_snapshot_hash=source_broker_snapshot_hash)
    validate_liquidity_checkpoint_reference(checkpoint)
    return dict(
        record_id=str(uuid5(NAMESPACE_URL, f"{run_id}:{parent_record_id}:liquidity-fade-failure")),
        parent_record_id=parent_record_id, run_id=run_id,
        event_month=intent.event_time.strftime("%Y-%m-01"), batch_id=batch_id,
        strategy_number=strategy_number, source_entry_intent_id=source_entry_intent_id,
        assignment_id=financial.assignment_id, **source, **checkpoint,
        **{f.name: getattr(witness, f.name) for f in fields(LiquidityFadeFailure) if f.name != "candles"},
        **{f"trade_count_{i}": c.trade_count for i, c in enumerate(witness.candles)},
    )


def restore_liquidity_fade_failure(row):
    """Replay complete scalars after raw stored hashes and UInt adaptation."""
    columns = {name for name, _ in LIQUIDITY_FADE_FAILURE.columns} - {"content_hash"}
    if set(row) - {"content_hash"} != columns or type(row.get("strategy_number")) is not int or (row["strategy_number"] not in (35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) and not declared_fixed_rule(row["strategy_number"], 'strategy-thirty-five-completed-liquidity-fade-v1')):
        raise ValueError("Liquidity fade requires its complete version-bound Strategy 35 through 42 family")
    validate_liquidity_observation_source(row)
    validate_liquidity_checkpoint_reference(row)
    integers = {name for name, kind in LIQUIDITY_FADE_FAILURE.columns if kind.startswith("UInt")}
    if any(type(row[name]) is not int for name in integers):
        raise ValueError("Liquidity fade requires exact integer producer authority")
    values = {}
    for f in fields(LiquidityFadeFailure):
        if f.name == "candles":
            continue
        value = row[f.name]
        if type(value) is bool:
            raise ValueError("Liquidity fade scalar cannot be boolean")
        values[f.name] = value if f.name in integers else float(value)
    end = row["completed_five_second_boundary_ms"]
    values["candles"] = tuple(LiquidityFadeCandle(end - offset, row[f"trade_count_{i}"])
                             for i, offset in enumerate((15_000, 10_000, 5_000, 0)))
    # Strategy39's existing normalized number distinguishes its extended
    # policy. Counts deterministically select the inherited quarter-rate
    # witness first; the additional witness can appear only outside that
    # activity condition. Complete scalar replay below checks every remaining
    # price, clock and freshness condition; a row-contained label cannot pass.
    witness_type = LiquidityFadeFailure
    prior = values['candles'][0].trade_count + values['candles'][1].trade_count
    recent = values['candles'][2].trade_count + values['candles'][3].trade_count
    if (row['strategy_number'] in (39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(row['strategy_number'], 'strategy-thirty-nine-half-risk-liquidity-failure-v1')) and 4 * recent > prior:
        from .strategy_half_risk_liquidity_fade import HalfRiskLiquidityFadeFailure
        witness_type = HalfRiskLiquidityFadeFailure
    witness = witness_type(**values)
    validate_liquidity_fade_witness(witness, strategy_number=row['strategy_number'])
    return witness


def prepared_liquidity_fade_row(row):
    """Freeze a scalar-verified row; this is not a native commit seal."""
    restore_liquidity_fade_failure(row)
    return MappingProxyType({k: v for k, v in row.items() if k != "content_hash"})
