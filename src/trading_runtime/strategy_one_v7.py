"""Causal V7 admission for draft Strategy 1 over certified ARTE seeds.

STRATEGY CREATION RULES: V1 provisional and future filtered seeds are distinct
run identities. A Strategy must never silently upgrade, downgrade, or infer
the seed policy from a projected level. The preflight-pinned plan is authority.
This module performs no market queries, materialization, or journal writes.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Mapping

from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.streaming_level_book import VERSION


PROVISIONAL_SEED_POLICY = "legacy-unfiltered"


def admitted_v7_levels(context: Mapping[str, Any], *, as_of: datetime,
                       seed_policy: str) -> tuple[Mapping[str, Any], ...]:
    """Return a fresh, identity-checked structural projection or no levels.

    A no-level result is a strategy rejection, not a synthetic empty book.
    Malformed or policy-mismatched inputs fail loudly so a mixed V1/V2 run
    cannot be accepted as an ordinary missing-signal boundary.
    """
    if (as_of.tzinfo is None
            or seed_policy not in (POLICY, PROVISIONAL_SEED_POLICY)):
        raise ValueError("Strategy 1 V7 needs a pinned aware boundary and seed policy")
    if context.get("qmd_level_book_version") != VERSION:
        raise ValueError("Strategy 1 V7 book version differs from pinned runtime")
    if (context.get("v7_input_policy") != POLICY
            or context.get("v7_seed_input_policy") != seed_policy):
        raise ValueError("Strategy 1 V7 input policy differs from pinned seed plan")
    last = context.get("v7_max_input_timestamp")
    now = as_of.timestamp()
    if (type(last) not in (int, float) or not isfinite(last)
            or not 0 < last <= now):
        raise ValueError("Strategy 1 V7 input clock is invalid or future")
    stale = now - last > 1.000001
    rows = context.get("qmd_structure_unified_levels")
    if not isinstance(rows, list):
        raise ValueError("Strategy 1 V7 projection is not a level list")
    admitted = []
    identities = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Strategy 1 V7 level is malformed")
        identity = row.get("unified_level_id")
        lower, upper = row.get("lower"), row.get("upper")
        confirmed = row.get("confirmed_at_ms")
        if (not isinstance(identity, str) or not identity or identity in identities
                or row.get("book_version") != VERSION
                or row.get("input_policy") != POLICY
                or row.get("seed_input_policy") != seed_policy
                or any(type(value) not in (int, float) or not isfinite(value)
                       for value in (lower, upper, confirmed))
                or not 0 < lower <= upper or not 0 < confirmed <= now * 1_000):
            raise ValueError("Strategy 1 V7 level violates causal seed geometry")
        identities.add(identity)
        admitted.append(row)
    return () if stale else tuple(admitted)
