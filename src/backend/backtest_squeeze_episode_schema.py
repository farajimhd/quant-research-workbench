"""Staged Backtest-only episode table, excluded from active Live journal preflight.

Operator provisioning and versioned commit/reader rollout must precede enabling
this family. DDL is returned for review and never executed here.
"""
from src.trading_runtime.arte_journal_schema import (
    TableContract, VERSIONED_JOURNAL_V2_TABLES,
)
from src.backend.backtest_reconciliation_v3 import CHILD as RECONCILIATION_DIFFERENCE


SQUEEZE_EPISODE = TableContract(
    "trading_backtest_squeeze_episode_v1",
    (("record_id", "UUID"), ("run_id", "String"),
     ("event_month", "Date"), ("batch_id", "UUID"),
     ("account_id", "String"), ("episode_id", "FixedString(64)"),
     ("signal_stream_id", "String"),
     ("ticker", "String"), ("market_plan_token", "FixedString(64)"),
     ("query_sha256", "FixedString(64)"),
     ("source_authority", "String"),
     ("episode_started_at", "DateTime64(6, 'UTC')"),
     ("expires_at", "DateTime64(6, 'UTC')"),
     ("last_price", "Decimal(38, 18)"),
     ("anchor_price", "Decimal(38, 18)"),
     ("move_pct", "Decimal(38, 18)"),
     ("high_water_pct", "Decimal(38, 18)"),
     ("trade_count_change", "UInt64"),
     ("volume_change", "Decimal(38, 18)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id, event_month, record_id",
)


RESERVATION_REASON = TableContract(
    "trading_portfolio_reservation_reason_v1",
    (("record_id", "UUID"), ("run_id", "String"),
     ("event_month", "Date"), ("batch_id", "UUID"),
     ("parent_record_id", "UUID"), ("account_id", "String"),
     ("ordinal", "UInt16"), ("reason", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id, parent_record_id, ordinal, record_id",
)


# This is a replacement commit, never an ALTER of occupied V1 or staged V2.
# The new count/hash are part of the V3 seal's exact ordered column contract.
_V2_COMMIT = next(table for table in VERSIONED_JOURNAL_V2_TABLES
                  if table.name == "trading_commit_v2")
_V2_COMMIT_COLUMNS = _V2_COMMIT.columns
_V3_EXTENSION = (
    ("backtest_squeeze_episode_count", "UInt32"),
    ("backtest_squeeze_episode_hash", "FixedString(64)"),
    ("portfolio_reservation_reason_count", "UInt32"),
    ("portfolio_reservation_reason_hash", "FixedString(64)"),
    ("portfolio_reconciliation_difference_count", "UInt32"),
    ("portfolio_reconciliation_difference_hash", "FixedString(64)"),
)
SQUEEZE_COMMIT_V3 = TableContract(
    "trading_commit_v3",
    _V2_COMMIT_COLUMNS[:-3] + _V3_EXTENSION + _V2_COMMIT_COLUMNS[-3:],
    _V2_COMMIT.partition, _V2_COMMIT.order,
)


def staged_v3_ddl() -> tuple[str, ...]:
    """Operator-review DDL only; never run or validate at live startup."""
    return (SQUEEZE_EPISODE.ddl(), RESERVATION_REASON.ddl(),
            RECONCILIATION_DIFFERENCE.ddl(), SQUEEZE_COMMIT_V3.ddl())


def staged_reconciliation_difference_ddl() -> tuple[str, ...]:
    """Operator-only additive upgrade after a direct zero-row V3 fence audit."""
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        RECONCILIATION_DIFFERENCE.ddl(),
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        "portfolio_reconciliation_difference_count UInt32 DEFAULT 0 "
        "AFTER portfolio_reservation_reason_hash",
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        f"portfolio_reconciliation_difference_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER portfolio_reconciliation_difference_count",
    )


def staged_reservation_reason_ddl() -> tuple[str, ...]:
    """Operator-only V3 upgrade; requires a direct zero-row V3 fence audit."""
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        RESERVATION_REASON.ddl(),
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        "portfolio_reservation_reason_count UInt32 DEFAULT 0 "
        "AFTER backtest_squeeze_episode_hash",
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        f"portfolio_reservation_reason_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER portfolio_reservation_reason_count",
    )


def staged_upgrade_ddl() -> tuple[str, ...]:
    """Return only the isolated family DDL.

    A versioned Backtest-only commit/read contract is still required. Adding
    fields to the active v1 commit table would change historical row hashes and
    live startup requirements, so this helper never proposes that migration.
    """
    return (SQUEEZE_EPISODE.ddl(),)
