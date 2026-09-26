"""Producer-owned normalized candidate product for immutable Strategy 1.

The Backtest principal may SELECT these two tables but never INSERT into them.
Every certified ticker-day, including one with no candidates, needs exactly one
coverage row. Coverage is published only after all child rows are validated.
An unreferenced attempt is invisible to Backtest and safe to abandon.
"""
from __future__ import annotations


CANDIDATE_TABLE = "arte.strategy_one_candidate_v1"
COVERAGE_TABLE = "arte.strategy_one_candidate_coverage_v1"
STORAGE_POLICY = "live_market_ssd"


def ddl() -> tuple[str, str]:
    """Separate sparse decision boundaries from their source/contract seal."""
    return (
        f"""CREATE TABLE IF NOT EXISTS {CANDIDATE_TABLE} (
          source_build_id String,
          session_date Date,
          ticker LowCardinality(String),
          derivation_attempt_id UUID,
          boundary_ms UInt32,
          source_row_index UInt32,
          episode_start_ms UInt32,
          macd_1s_boundary_ms UInt32,
          macd_5s_boundary_ms UInt32,
          macd_10s_boundary_ms UInt32,
          macd_30s_boundary_ms UInt32,
          stop_30s_boundary_ms UInt32,
          stop_low_int UInt64
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,ticker,derivation_attempt_id,boundary_ms)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
        f"""CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE} (
          source_build_id String,
          session_date Date,
          ticker LowCardinality(String),
          derivation_attempt_id UUID,
          bars_attempt_id UUID,
          technical_attempt_id UUID,
          liquidity_attempt_id UUID,
          strategy_digest FixedString(64),
          candidate_count UInt32,
          content_hash FixedString(64),
          certified_at DateTime64(6,'UTC')
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,ticker,derivation_attempt_id)
          SETTINGS storage_policy='{STORAGE_POLICY}'""",
    )
