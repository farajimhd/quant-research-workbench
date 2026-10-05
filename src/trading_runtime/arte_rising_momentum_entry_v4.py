"""Two normalized completed MACD observations per Strategy 13 entry."""
import struct
from uuid import UUID, NAMESPACE_URL, uuid5
from .arte_journal_schema import TableContract
from .strategy_rising_momentum_witness import (
    CompletedMomentumObservation, RisingMomentumWitness, numbered_momentum_entry,
)

VALUES = ("current_line", "current_signal", "prior_line", "prior_signal")
MOMENTUM = TableContract("trading_rising_momentum_entry_v4", (
    ("record_id", "UUID"), ("parent_record_id", "UUID"), ("run_id", "String"),
    ("event_month", "Date"), ("batch_id", "UUID"), ("strategy_number", "UInt32"),
    ("ticker", "String"), ("boundary_ms", "UInt32"), ("resolution_ms", "UInt32"),
    ("source_build_id", "FixedString(64)"), ("source_attempt_id", "UUID"),
    ("market_plan_token", "FixedString(64)"), ("current_boundary_ms", "UInt32"),
    ("prior_boundary_ms", "UInt32"), *((name, "Nullable(Float64)") for name in VALUES),
    ("content_hash", "FixedString(64)")), "toYYYYMM(event_month)",
    "run_id, parent_record_id, resolution_ms, record_id")
TABLES = (MOMENTUM,)


def momentum_select_columns() -> str:
    """Read IEEE bits beside nullable scalars so JSON formatting cannot round MACD."""
    return ",".join(name for name, _ in MOMENTUM.columns) + "," + ",".join(
        f"if(isNull({name}),NULL,reinterpretAsUInt64(assumeNotNull({name}))) AS {name}_bits"
        for name in VALUES)


def decode_momentum_row(row):
    result = dict(row)
    for name in VALUES:
        bits = result.pop(name + "_bits")
        if (bits is None) != (result[name] is None):
            raise ValueError("Momentum null scalar differs from its bit projection")
        result[name] = None if bits is None else struct.unpack(">d", int(bits).to_bytes(8, "big"))[0]
    return result


def project_rising_momentum_entry(proposal, *, run_id, batch_id, parent_record_id, event_month):
    if proposal.strategy_number not in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61):
        if proposal.momentum is not None:
            raise ValueError("Old entry cannot carry Strategy 13 momentum")
        return ()
    witness = proposal.momentum
    if (not numbered_momentum_entry(witness, proposal.strategy_number) or witness.ticker != proposal.ticker
            or witness.boundary_ms != proposal.boundary_ms):
        raise ValueError("Strategy 13 entry requires rising completed momentum")
    parent, batch = str(UUID(parent_record_id)), str(UUID(batch_id))
    return tuple({"record_id": str(uuid5(NAMESPACE_URL, f"{parent}:rising-momentum:{o.resolution_ms}")),
        "parent_record_id": parent, "run_id": run_id, "event_month": str(event_month),
        "batch_id": batch, "strategy_number": proposal.strategy_number, "ticker": witness.ticker,
        "boundary_ms": witness.boundary_ms, "resolution_ms": o.resolution_ms,
        "source_build_id": witness.source_build_id, "source_attempt_id": witness.source_attempt_id,
        "market_plan_token": witness.market_plan_token,
        "current_boundary_ms": o.current_boundary_ms, "prior_boundary_ms": o.prior_boundary_ms,
        **{name: getattr(o, name) for name in VALUES}} for o in witness.observations)


def restore_rising_momentum(rows, *, ticker, boundary_ms, strategy_number=None):
    if len(rows) != 2 or {r["resolution_ms"] for r in rows} != {1000, 10000}:
        raise ValueError("Strategy 13 momentum requires exact two resolutions")
    ordered = sorted(rows, key=lambda r: r["resolution_ms"])
    first = ordered[0]
    if strategy_number is not None and (type(strategy_number) is not int
            or strategy_number != first["strategy_number"]):
        raise ValueError("Momentum strategy number differs from original entry source")
    identity = ("parent_record_id", "run_id", "event_month", "batch_id", "strategy_number",
                "ticker", "boundary_ms", "source_build_id", "source_attempt_id", "market_plan_token")
    if (first["strategy_number"] not in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or first["ticker"] != ticker or first["boundary_ms"] != boundary_ms
            or any(any(r[k] != first[k] for k in identity) for r in ordered)
            or any(r["record_id"] != str(uuid5(NAMESPACE_URL,
                f"{r['parent_record_id']}:rising-momentum:{r['resolution_ms']}")) for r in ordered)):
        raise ValueError("Strategy 13 momentum identity or parent differs")
    witness = RisingMomentumWitness(ticker, boundary_ms, str(first["source_build_id"]),
        str(first["source_attempt_id"]), first["market_plan_token"], tuple(
            CompletedMomentumObservation(r["resolution_ms"], r["current_boundary_ms"],
                r["prior_boundary_ms"], *(r[name] for name in VALUES)) for r in ordered))
    if not numbered_momentum_entry(witness, first["strategy_number"]):
        raise ValueError("Strategy 13 entry requires rising completed momentum")
    return witness


def seal_rising_momentum_rows(rows, entries, intents, events):
    """Require exact 2:1 companion authority before any direct or compound write."""
    from .arte_journal_writer import typed_row
    sealed = tuple(typed_row(MOMENTUM.name, {k: v for k, v in row.items() if k != "content_hash"}) for row in rows)
    if any("content_hash" in source and source["content_hash"] != row["content_hash"]
           for source, row in zip(rows, sealed)):
        raise ValueError("Strategy 13 momentum scalar seal changed")
    required = {r["parent_record_id"]: r for r in entries if r["strategy_number"] in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)}
    parents = {r["record_id"]: r for r in intents if r["reason"] == "strategy_one_entry"}
    source_events = {r["record_id"]: r for r in events}
    if len(sealed) != 2 * len(required) or any(r["parent_record_id"] not in required for r in sealed):
        raise ValueError("Strategy 13 entry has missing or extra momentum companions")
    for parent, entry in required.items():
        selected = tuple(r for r in sealed if r["parent_record_id"] == parent)
        intent = parents.get(parent)
        if intent is None or any(r["strategy_number"] != entry["strategy_number"]
                                 or r["run_id"] != entry["run_id"] or r["batch_id"] != entry["batch_id"]
                                 or r["event_month"] != entry["event_month"] for r in selected):
            raise ValueError("Strategy 13 momentum has an unrelated typed parent")
        restore_rising_momentum(selected, ticker=intent["ticker"], boundary_ms=entry["boundary_ms"])
        event = source_events.get(parent)
        if event is None:
            raise ValueError("Strategy 13 momentum entry lacks its typed source clock")
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        at = datetime.fromisoformat(str(event["event_time"]).replace("Z", "+00:00"))
        local = (at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)).astimezone(ZoneInfo("America/New_York"))
        identity = (f"strategy-{entry['strategy_number']}:{local.date().isoformat()}:{entry['assignment_id']}:"
                    f"{intent['account_id']}:{intent['ticker']}:{entry['boundary_ms']}:"
                    f"{entry['episode_start_ms']}")
        if intent["intent_id"] != str(uuid5(NAMESPACE_URL, identity)):
            raise ValueError("Strategy 13 momentum parent is not its numbered entry intent")

    return sealed
