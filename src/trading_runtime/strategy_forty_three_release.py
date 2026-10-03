"""Independent sealed Strategy 43 policy; no inherited numbered trading rules."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .strategy_forty_three_rules import (
    SOURCE_CANDIDATE_ID, SOURCE_CODE_COMMIT, SOURCE_SESSION, STRATEGY_ID,
    STRATEGY_NUMBER, verify_selection,
)
from .strategy_registry import NumberedStrategyRelease


BEHAVIOR = (
    "Backtest only. Exact eligible September 3 2026 premarket Torch v2 candidate "
    + SOURCE_CANDIDATE_ID + " at source " + SOURCE_CODE_COMMIT + ". "
    "Completed 1-second decisions, native completed 100-ms broker fills strictly "
    "after submission. Full certified preopen tradable population, LGHL excluded. "
    "Immediate first squeeze signal second only; no MACD or VWAP confirmation. "
    "Fresh executable NBBO within 1s, spread <=1%, completed execution notional "
    ">=1000 and trades >=5. Confirmed 2-left/2-right swing, available before the "
    "decision, minus one cent. Fifteen distinct nearest resistance identities "
    "above ask, lower minus one cent, each target above ask*1.01, frozen at entry. "
    "One batch of fifteen independent fixed-quantity entry parents per ticker "
    "per New York session; every parent has one full-size stop and one full-size "
    "target. Submission lock survives rejection, cancellation and exit. Shared "
    "Portfolio free cash after pending reservations and conservative protective "
    "fees, additional 75-dollar fee reserve, decreasing 1/log1p(rank) weights, "
    "floor quantities, buy cap ask*1.01, 5-second deadline, no reprice. Rank first "
    "with exact research incoming score and ticker tie-break; do not fall back "
    "when the selected ticker cannot be sized. No adds, re-entry or replacement. "
    "Adaptive leg-specific trail after ten seconds from actual native first "
    "fill: peak excluding the first-filled 1-second bucket minus max(three times "
    "producer mean of ten consecutive observed absolute close changes, 1% of "
    "native leg average entry), capped at fresh bid minus one cent and never "
    "decreasing. At ten seconds before session end cancel pending acquisitions "
    "and submit native managed liquidation; residual exposure fails qualification. "
    "Native order prices use the journal's ten-decimal precision: buy caps and "
    "stops floor, targets ceil; raw producer facts and ranking stay unchanged. "
    "Prior-session V7 checkpoint available before open then completed intraday "
    "1s streaming; no tested-day EOD checkpoint. Producer coverage fails closed. "
    "Keeper-fenced normalized native journal; no live or public resume admission."
)
POLICY_DIGEST = sha256(BEHAVIOR.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class StrategyFortyThreeContract:
    strategy_number: int = STRATEGY_NUMBER
    strategy_id: str = STRATEGY_ID
    execution_interval: str = "100ms"
    decision_interval_ms: int = 1000


def release_contract() -> NumberedStrategyRelease:
    verify_selection()
    values = dict(number=STRATEGY_NUMBER, executor_strategy_id=STRATEGY_ID,
        executor_revision=STRATEGY_NUMBER, evaluation_interval="100ms",
        input_contracts=(
            "arte.strategy_forty_three_second_fact_v1@completed_1s",
            "arte.strategy_forty_three_population_v1@certified_preopen",
            "arte.strategy_one_v7_level_interval_v1@prior_seed_streaming_1s",
            "arte.liquidity_100ms_v1@100ms",
            "arte.liquidity_execution_price_100ms_v1@100ms",
        ),
        rule_set_contracts=("strategy-43-signal-batch-v1", "strategy-43-adaptive-leg-v1",
                            "strategy-43-portfolio-fee-reserve-v1"),
        behavior_specification=(
            "Independent Backtest-only Strategy 43; full reviewed policy SHA-256 "
            + POLICY_DIGEST + ". Exact eligible September 3 premarket research candidate "
            + SOURCE_CANDIDATE_ID + ". 1s decisions / native 100ms fills; fifteen "
            "separate single-stop/target parents, immediate signal entry, decreasing "
            "log sizing, prior-session seeded streaming structural targets, prior "
            "confirmed swing stop, adaptive leg-specific trail, submission lock, "
            "no adds/re-entry/replacement, shared native Portfolio/OMS/journal. "
            "Full tradable population with LGHL excluded. Missing certification "
            "fails closed; no live or public resume admission."))
    draft = NumberedStrategyRelease(**values, approved_digest="")
    sealed = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    sealed.verify()
    return sealed


def selection_provenance() -> dict:
    return {"candidate_id": SOURCE_CANDIDATE_ID, "code_commit": SOURCE_CODE_COMMIT,
            "session": SOURCE_SESSION, "session_kind": "premarket",
            "decision_interval_ms": 1000, "broker_interval_ms": 100}
