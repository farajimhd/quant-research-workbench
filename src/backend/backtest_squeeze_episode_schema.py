"""Staged Backtest-only episode table, excluded from active Live journal preflight.

Operator provisioning and versioned commit/reader rollout must precede enabling
this family. DDL is returned for review and never executed here.
"""
from src.trading_runtime.arte_journal_schema import (
    TableContract, VERSIONED_JOURNAL_V2_TABLES,
)
from src.backend.backtest_reconciliation_v3 import CHILD as RECONCILIATION_DIFFERENCE
from src.backend.backtest_portfolio_control_v3 import CONTROL as PORTFOLIO_CONTROL
from src.backend.backtest_trade_proposal_v3 import TABLES as TRADE_PROPOSAL_TABLES
from src.backend.backtest_broker_shortability_v3 import SHORT_ORDER_SKIP
from src.backend.backtest_broker_policy_v3 import POLICY_EVENT, MESSAGE as POLICY_MESSAGE
from src.backend.backtest_entry_reprice_deferred_v3 import DEFERRED as ENTRY_REPRICE_DEFERRED
from src.backend.backtest_entry_reprice_capacity_v3 import TABLES as ENTRY_REPRICE_CAPACITY_TABLES
from src.backend.backtest_entry_reprice_rejected_v3 import REJECTED as ENTRY_REPRICE_REJECTED
from src.backend.backtest_protected_exit_satisfied_v3 import SATISFIED as PROTECTED_EXIT_SATISFIED
from src.backend.backtest_protection_change_v3 import TABLES as PROTECTION_CHANGE_TABLES
from src.backend.backtest_protected_exit_snapshot_v3 import SNAPSHOT as PROTECTED_EXIT_SNAPSHOT

BROKER_OMS_TABLES = (SHORT_ORDER_SKIP, POLICY_EVENT, POLICY_MESSAGE,
                     ENTRY_REPRICE_DEFERRED)


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
    ("portfolio_control_count", "UInt32"),
    ("portfolio_control_hash", "FixedString(64)"),
    ("trade_proposal_child_count", "UInt32"),
    ("trade_proposal_child_hash", "FixedString(64)"),
    ("broker_short_order_skip_count", "UInt32"),
    ("broker_short_order_skip_hash", "FixedString(64)"),
    ("broker_reply_policy_event_count", "UInt32"),
    ("broker_reply_policy_event_hash", "FixedString(64)"),
    ("broker_reply_policy_message_count", "UInt32"),
    ("broker_reply_policy_message_hash", "FixedString(64)"),
    ("entry_reprice_deferred_count", "UInt32"),
    ("entry_reprice_deferred_hash", "FixedString(64)"),
    ("entry_reprice_capacity_count", "UInt32"),
    ("entry_reprice_capacity_hash", "FixedString(64)"),
    ("entry_reprice_capacity_reason_count", "UInt32"),
    ("entry_reprice_capacity_reason_hash", "FixedString(64)"),
    ("entry_reprice_rejected_count", "UInt32"),
    ("entry_reprice_rejected_hash", "FixedString(64)"),
    ("protected_exit_satisfied_count", "UInt32"),
    ("protected_exit_satisfied_hash", "FixedString(64)"),
    ("protection_change_count", "UInt32"),
    ("protection_change_hash", "FixedString(64)"),
    ("protected_exit_snapshot_count", "UInt32"),
    ("protected_exit_snapshot_hash", "FixedString(64)"),
)
SQUEEZE_COMMIT_V3 = TableContract(
    "trading_commit_v3",
    _V2_COMMIT_COLUMNS[:-3] + _V3_EXTENSION + _V2_COMMIT_COLUMNS[-3:],
    _V2_COMMIT.partition, _V2_COMMIT.order,
)


def staged_v3_ddl() -> tuple[str, ...]:
    """Operator-review DDL only; never run or validate at live startup."""
    return (SQUEEZE_EPISODE.ddl(), RESERVATION_REASON.ddl(),
            RECONCILIATION_DIFFERENCE.ddl(), PORTFOLIO_CONTROL.ddl(),
            *(table.ddl() for table in TRADE_PROPOSAL_TABLES),
            *(table.ddl() for table in BROKER_OMS_TABLES),
            *(table.ddl() for table in ENTRY_REPRICE_CAPACITY_TABLES),
            ENTRY_REPRICE_REJECTED.ddl(),
            PROTECTED_EXIT_SATISFIED.ddl(),
            *(table.ddl() for table in PROTECTION_CHANGE_TABLES),
            PROTECTED_EXIT_SNAPSHOT.ddl(),
            SQUEEZE_COMMIT_V3.ddl())


def staged_protected_exit_snapshot_ddl() -> tuple[str, ...]:
    """Review-only additive DDL; operator must prove the V3 fence is empty."""
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        PROTECTED_EXIT_SNAPSHOT.ddl(),
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        "protected_exit_snapshot_count UInt32 DEFAULT 0 "
        "AFTER protection_change_hash",
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        f"protected_exit_snapshot_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER protected_exit_snapshot_count",
    )


def staged_protection_change_ddl() -> tuple[str, ...]:
    """Review-only additive DDL; operator must prove an empty V3 fence."""
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        *(table.ddl() for table in PROTECTION_CHANGE_TABLES),
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        "protection_change_count UInt32 DEFAULT 0 "
        "AFTER protected_exit_satisfied_hash",
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        f"protection_change_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER protection_change_count",
    )


def staged_protected_exit_satisfied_ddl() -> tuple[str, ...]:
    """Review-only additive DDL; operator must prove the V3 fence is empty."""
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        PROTECTED_EXIT_SATISFIED.ddl(),
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        "protected_exit_satisfied_count UInt32 DEFAULT 0 "
        "AFTER entry_reprice_rejected_hash",
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        f"protected_exit_satisfied_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER protected_exit_satisfied_count",
    )


def staged_entry_reprice_rejected_ddl() -> tuple[str, ...]:
    """Review-only additive DDL; operator must prove the V3 fence is empty."""
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        ENTRY_REPRICE_REJECTED.ddl(),
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        "entry_reprice_rejected_count UInt32 DEFAULT 0 "
        "AFTER entry_reprice_capacity_reason_hash",
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        f"entry_reprice_rejected_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER entry_reprice_rejected_count",
    )


def staged_entry_reprice_capacity_ddl() -> tuple[str, ...]:
    """Review-only additive DDL; caller must prove an empty V3 fence."""
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    fields = (
        ("entry_reprice_capacity_count", "UInt32", "0", "entry_reprice_deferred_hash"),
        ("entry_reprice_capacity_hash", "FixedString(64)", f"'{empty_hash}'",
         "entry_reprice_capacity_count"),
        ("entry_reprice_capacity_reason_count", "UInt32", "0",
         "entry_reprice_capacity_hash"),
        ("entry_reprice_capacity_reason_hash", "FixedString(64)",
         f"'{empty_hash}'", "entry_reprice_capacity_reason_count"),
    )
    return tuple(table.ddl() for table in ENTRY_REPRICE_CAPACITY_TABLES) + tuple(
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        f"{name} {kind} DEFAULT {default} AFTER {prior}"
        for name, kind, default, prior in fields)


def staged_broker_oms_ddl() -> tuple[str, ...]:
    """Operator-only additive upgrade after direct zero-row V3 fence proof."""
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    fields = (
        ("broker_short_order_skip", "trade_proposal_child_hash"),
        ("broker_reply_policy_event", "broker_short_order_skip_hash"),
        ("broker_reply_policy_message", "broker_reply_policy_event_hash"),
        ("entry_reprice_deferred", "broker_reply_policy_message_hash"),
    )
    alters = []
    for prefix, prior in fields:
        alters.extend((
            "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
            f"{prefix}_count UInt32 DEFAULT 0 AFTER {prior}",
            "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
            f"{prefix}_hash FixedString(64) DEFAULT '{empty_hash}' "
            f"AFTER {prefix}_count",
        ))
    return tuple(table.ddl() for table in BROKER_OMS_TABLES) + tuple(alters)


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


def staged_portfolio_control_ddl() -> tuple[str, ...]:
    """Operator-only additive upgrade; a populated V3 fence needs V4."""
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        PORTFOLIO_CONTROL.ddl(),
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        "portfolio_control_count UInt32 DEFAULT 0 "
        "AFTER portfolio_reconciliation_difference_hash",
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        f"portfolio_control_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER portfolio_control_count",
    )


def staged_trade_proposal_ddl() -> tuple[str, ...]:
    """Operator-only additive V3 upgrade after an exact empty-fence audit."""
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        *(table.ddl() for table in TRADE_PROPOSAL_TABLES),
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        "trade_proposal_child_count UInt32 DEFAULT 0 AFTER portfolio_control_hash",
        "ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "
        f"trade_proposal_child_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER trade_proposal_child_count",
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
