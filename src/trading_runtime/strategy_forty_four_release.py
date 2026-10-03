"""Independent sealed Strategy 44 policy; no inherited numbered trading rules."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .strategy_forty_four_rules import (
    SOURCE_CANDIDATE_ID, SOURCE_CODE_COMMIT, SOURCE_SESSION, STRATEGY_ID,
    STRATEGY_NUMBER, verify_selection,
)
from .strategy_registry import NumberedStrategyRelease


BEHAVIOR = (
    "Backtest only. Modified September 3 2026 premarket Torch v2-3 candidate "
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
    "target. Each filled parent is one separately reported position with exact native acquisition ownership. Submission lock survives rejection, cancellation and exit. Shared "
    "Portfolio free cash after pending reservations and conservative protective "
    "fees, additional 75-dollar fee reserve, decreasing 1/log1p(rank) weights, "
    "floor quantities, buy cap ask*1.01, pending until the session entry cutoff, nearest target fully filled before the next, no reprice. Rank first "
    "with exact research incoming score and ticker tie-break; do not fall back "
    "when the selected ticker cannot be sized. No adds, re-entry or replacement. "
    "Adaptive leg-specific trail after ten seconds from actual native first "
    "fill: peak excluding the first-filled 1-second bucket minus max(three times "
    "producer mean of ten consecutive observed absolute close changes, 1% of "
    "native leg average entry), capped at fresh bid minus one cent and never "
    "decreasing. At five minutes before session end cancel pending acquisitions. An observed completed low at or below the frozen original stop cancels every unfilled acquisition in that batch. At one minute before session end "
    "submit native managed liquidation on the first fresh quote for each held leg with a one-cent sell floor; residual exposure fails qualification. "
    "Native order prices use the journal's ten-decimal precision: buy caps and "
    "stops floor, targets ceil; raw producer facts and ranking stay unchanged. "
    "Prior-session V7 checkpoint available before open then completed intraday "
    "1s streaming; no tested-day EOD checkpoint. Producer coverage fails closed. "
    "Keeper-fenced normalized native journal; no live or public resume admission."
)
POLICY_DIGEST = sha256(BEHAVIOR.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class StrategyFortyFourContract:
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
            "arte.bars_v1@completed_1s_trade_count",
            "arte.liquidity_100ms_v1@100ms",
            "arte.liquidity_execution_price_100ms_v1@100ms",
        ),
        rule_set_contracts=("strategy-44-signal-batch-v1", "strategy-44-adaptive-leg-v1",
                            "strategy-44-portfolio-fee-reserve-v1"),
        behavior_specification=(
            "Independent Backtest-only Strategy 44; full reviewed policy SHA-256 "
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


def configuration(*, approved_code_commit, approved_code_fingerprint, approval_reference):
    """Build this release from its own policy, without a numbered parent."""
    from dataclasses import asdict
    import re
    from .portfolio import PortfolioPolicy
    from .journal_contract import canonical_json
    if (not re.fullmatch(r"[0-9a-f]{40}", approved_code_commit or "")
            or not re.fullmatch(r"[0-9a-f]{64}", approved_code_fingerprint or "")
            or not isinstance(approval_reference, str) or not 1 <= len(approval_reference) <= 256):
        raise ValueError("Strategy 44 requires explicit reviewed source provenance")
    release = release_contract()
    manifest = dict(contract=release.canonical_payload(), approved_digest=release.approved_digest,
        policy_digest=POLICY_DIGEST, approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference,
        publication_mode="backtest_only", selection=selection_provenance())
    manifest["manifest_hash"] = sha256(canonical_json(manifest).encode()).hexdigest()
    policy = PortfolioPolicy(policy_id="strategy-44-cash", revision=44,
        entry_fee_buffer_bps=0., maximum_position_fraction=1., maximum_ticker_fraction=1.,
        maximum_planned_risk_fraction=1., maximum_open_risk_fraction=1.,
        maximum_open_positions=10_000, allow_outside_rth=True, allow_overnight=False,
        allowed_currencies=("USD",), restricted_symbols=("LGHL",))
    payload = dict(market_day_build_id="1521ba7702a9ee0783916f706f4885a24a3f32a91630b04ff738a90e65bc9dd5",
        strategy=dict(strategy_id=STRATEGY_ID, strategy_number=44, revision=44,
            name="Squeeze Grid Strategy 44", profile_id="squeeze-grid-44", profile_revision=44,
            execution_interval="100ms", decision_interval_ms=1000, parameters={}, numbered_release=manifest),
        strategy_profile=dict(profile_id="squeeze-grid-44", revision=44, definition_revision=44,
            name="Squeeze Grid Strategy 44", description=release.behavior_specification,
            lifecycle=dict(trading_behavior=dict(eligible_sessions=["premarket"]))),
        run_plan=dict(run_plan_id="squeeze-grid-44-backtest", name="Strategy 44 Backtest",
            profile_id="squeeze-grid-44", initial_cash=10_000.,
            safety_supervisor=dict(enabled_by_environment=dict(backtest=False))),
        accounts=dict(bindings=[dict(account_key="strategy-44", enabled=True, modes=["backtest"],
            account_class="simulated", portfolio_policy_id=policy.policy_id,
            base_currency="USD", strategy_allocation=1.)]),
        portfolio=dict(policies=[asdict(policy)], mandates=[], groups=[]),
        assignments=[], features=[])
    import json
    return json.loads(canonical_json(payload))


def verify_manifest(strategy):
    """Reject live mode, source drift, foreign policy or mutable parameters."""
    from .journal_contract import canonical_json
    if not isinstance(strategy, dict):
        raise ValueError("Strategy 44 configuration is absent")
    manifest = strategy.get("numbered_release")
    if not isinstance(manifest, dict):
        raise ValueError("Strategy 44 immutable manifest is absent")
    expected = configuration(approved_code_commit=manifest.get("approved_code_commit"),
        approved_code_fingerprint=manifest.get("approved_code_fingerprint"),
        approval_reference=manifest.get("approval_reference"))["strategy"]
    if canonical_json(strategy) != canonical_json(expected):
        raise ValueError("Strategy 44 differs from its independent reviewed policy")
    return dict(manifest)
