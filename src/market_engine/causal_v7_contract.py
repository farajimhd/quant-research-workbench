"""Shared identity for the persisted causal V7 producer and SELECT-only reader."""

VERSION = "causal-v7-market-day-1s-v1"
SOURCE_POLICY = "arte-priced-1s-bars-0405-et-v1"
STORAGE_POLICY = "live_market_ssd"
STATE_TABLE = "arte.causal_v7_state_1s_v1"
LEVEL_TABLE = "arte.causal_v7_levels_v1"
COVERAGE_TABLE = "arte.causal_v7_coverage_v1"
