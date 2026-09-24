"""Staged Backtest-only episode table, excluded from active Live journal preflight.

Operator provisioning and versioned commit/reader rollout must precede enabling
this family. DDL is returned for review and never executed here.
"""
from src.trading_runtime.arte_journal_schema import TableContract


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


def staged_upgrade_ddl() -> tuple[str, ...]:
    """Return only the isolated family DDL.

    A versioned Backtest-only commit/read contract is still required. Adding
    fields to the active v1 commit table would change historical row hashes and
    live startup requirements, so this helper never proposes that migration.
    """
    return (SQUEEZE_EPISODE.ddl(),)
