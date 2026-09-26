"""Producer-owned, tabular authority for a disjoint V7 archive supplement.

Backtest only SELECTs this certificate. A supplemental ticker cannot be
silently interpreted as part of the primary retrospective campaign.
"""
from __future__ import annotations

import json
from typing import Any


TABLE = "arte.structural_v7_supplement_lineage_v1"
STORAGE_POLICY = "live_market_ssd"


def ddl() -> str:
    return f"""CREATE TABLE IF NOT EXISTS {TABLE} (
      parent_source_plan_hash FixedString(64),
      supplement_source_plan_hash FixedString(64),
      ticker LowCardinality(String),
      verified_at DateTime64(6,'UTC')
    ) ENGINE=MergeTree
      PARTITION BY toYYYYMM(verified_at)
      ORDER BY (parent_source_plan_hash,supplement_source_plan_hash,ticker)
      SETTINGS storage_policy='{STORAGE_POLICY}'"""


def certified_ticker_lineage(client: Any, *, tickers: tuple[str, ...]) -> tuple[dict, ...]:
    """Read exact immutable supplement membership for selected seed tickers."""
    if (not tickers or len(set(tickers)) != len(tickers)
            or any(not name or name != name.upper()
                   or not name.replace(".", "").replace("-", "").isalnum()
                   for name in tickers)):
        raise ValueError("V7 supplement lineage needs unique canonical tickers")
    names = ",".join("'" + name + "'" for name in sorted(tickers))
    rows = [json.loads(line) for line in client.execute(
        "SELECT parent_source_plan_hash,supplement_source_plan_hash,ticker "
        f"FROM {TABLE} WHERE ticker IN ({names}) "
        "ORDER BY ticker FORMAT JSONEachRow").splitlines() if line.strip()]
    if (len({row.get("ticker") for row in rows}) != len(rows)
            or not {row.get("ticker") for row in rows} <= set(tickers)
            or any(set(row) != {"parent_source_plan_hash",
                                    "supplement_source_plan_hash", "ticker"}
                   for row in rows)):
        raise ValueError("V7 supplement lineage is missing or duplicate")
    return tuple(rows)
