"""Whole-plan source@2: complete candidate structural scope, full price authority.

No selected admission survivors may replace the producer candidate projection.
Frozen source@1 remains unchanged for exact historical reproduction.
"""
from .backtest_fixed_structural_lot_source import (
    CertifiedPriceReadbackAuthority, CertifiedStrategyOneConfiguration,
    FixedStructuralLotPolicy, PreparedFixedStructuralLotSource,
    certify_numbered_configuration, derive_fixed_structural_lot_configuration,
    certify_v7_interval_plan, CertifiedV7IntervalPlan, _validate_interval_ticker,
    _load_quotes, _ISSUE_LOCK, _ISSUED, _source_identity,
    canonical_json, sha256, encode_nodes, node_hash, UUID, date,
)
from .backtest_strategy_one_plan import StrategyOneFixedPlans
from .backtest_market_data import project_market_day_plan, project_empty_market_day_plan, verify_market_day_plan
from .backtest_strategy_one_candidate_store import (
    certify_candidate_plan, exclude_candidate_tickers, project_candidate_plan, RULE_DIGEST,
)
from .backtest_strategy_one_preparation import strategy_one_v7_tickers
from .backtest_fixed_structural_lot_empty import _same_candidates
from .structural_v7_seed import CertifiedSeedPlan, certified_seed_plan

from src.trading_runtime.fixed_structural_lot_interval_validator_v2 import (
    VALIDATOR_RULE, validate_fixed_structural_lot_interval_plan,
)

SOURCE_INPUT = 'fixed-structural-lot-source@2'


def verify_complete_scope(plans, *, client, session_date, price_authority=None,
        through_boundary_ms=57_600_000, require_price_authority=True):
    if type(plans) is not StrategyOneFixedPlans or plans.market.sessions != (session_date.isoformat(),):
        raise ValueError('Source@2 requires exact whole certified fixed plans/session')
    market = plans.market
    verify_market_day_plan(market, client=client)
    full = certify_candidate_plan(market, candidate_rule_digest=RULE_DIGEST,
        through_boundary_ms=57_600_000, client=client)
    # Exclusions are the existing certified strategy input policy, never caller scope.
    from .backtest_input_scope import input_exclusions
    occupied = {row.ticker for row in full.coverage if row.candidate_count > 0}
    exclusions = tuple(t for t in input_exclusions(session_date.isoformat()) if t in occupied)
    fresh = exclude_candidate_tickers(full, exclusions)
    if not _same_candidates(fresh, plans.candidates):
        raise ValueError('Source@2 complete candidate source differs')
    selected = strategy_one_v7_tickers(fresh.prepared)
    execution = (project_market_day_plan(market, selected) if selected else
        project_empty_market_day_plan(market, empty_candidate_token=fresh.token))
    if plans.execution_market != execution:
        raise ValueError('Source@2 exact complete candidate execution projection differs')
    if not selected:
        if any(value is not None for value in (plans.seeds, plans.v7_intervals,
                plans.activations, plans.pivots, plans.hod, plans.entry, price_authority)):
            raise ValueError('Source@2 empty candidate source has foreign nonempty inputs')
        return selected, None
    if (type(plans.seeds) is not CertifiedSeedPlan or type(plans.v7_intervals) is not CertifiedV7IntervalPlan
            or plans.entry is None or plans.hod is None or plans.activations is None or plans.pivots is None
            or require_price_authority and type(price_authority) is not CertifiedPriceReadbackAuthority):
        raise ValueError('Source@2 nonempty candidates lack complete certified inputs')
    if price_authority is not None:
        if type(price_authority) is not CertifiedPriceReadbackAuthority:
            raise ValueError('Source@2 price authority type differs')
        price_authority.__post_init__()
        native_market = price_authority.plan.source.market
        if native_market != market:
            raise ValueError('Source@2 native full-market price authority differs')
        if price_authority.plan.entry != plans.entry:
            raise ValueError('Source@2 price authority entry linkage differs')
        native_candidates = price_authority.plan.candidates
        if not _same_candidates(project_candidate_plan(fresh, through_boundary_ms=through_boundary_ms), native_candidates):
            raise ValueError('Source@2 price authority candidate linkage differs')
    seeds = certified_seed_plan(execution, client)
    if seeds != plans.seeds:
        raise ValueError('Source@2 complete candidate seed linkage differs')
    if (seeds.build_id != market.build_id
            or tuple((u['backtest_session'], u['ticker']) for u in seeds.units)
               != tuple((session_date.isoformat(), t) for t in selected)):
        raise ValueError('Source@2 seed inventory differs from exact candidate projection')
    intervals = certify_v7_interval_plan(execution, seeds,
        session_date=session_date.isoformat(), candidate_tickers=selected, client=client)
    if intervals != plans.v7_intervals or intervals._tickers != selected:
        raise ValueError('Source@2 complete candidate interval linkage differs')
    validate_fixed_structural_lot_interval_plan(intervals, session_date=session_date)
    return selected, intervals


def prepare_fixed_structural_lot_source(client, *, run_id, parent_number, session_date,
        policy, plans, price_authority, tick=None, installed_number=None, through_boundary_ms=57_600_000):
    """Fresh complete configuration/product preparation, once per operation."""
    if (type(run_id) is not str or str(UUID(run_id)) != run_id
            or type(parent_number) is not int or type(session_date) is not date
            or (type(policy) is not FixedStructuralLotPolicy and not (policy is None and type(installed_number) is int))
            or type(price_authority) is not CertifiedPriceReadbackAuthority
            or price_authority.run_id != run_id):
        raise ValueError('Exact prepared lot run/source declaration required')
    if policy is not None:
        policy.__post_init__()
    price_authority.__post_init__()
    selected_tickers, intervals = verify_complete_scope(plans, client=client,
        session_date=session_date, price_authority=price_authority, through_boundary_ms=through_boundary_ms)
    if not selected_tickers:
        raise ValueError('Source@2 empty candidates require the explicit empty factory')
    market = plans.market
    parent = certify_numbered_configuration(client, parent_number)
    if type(parent) is not CertifiedStrategyOneConfiguration:
        raise ValueError('Actual complete parent configuration certificate required')
    payload = parent.payload
    if (type(payload) is not dict or payload['strategy']['revision'] != parent_number
            or sha256(canonical_json(payload).encode()).hexdigest() != parent.payload_hash
            or node_hash(encode_nodes(payload)) != parent.node_hash):
        raise ValueError('Parent configuration content differs from its certificate')
    installed=None
    if installed_number is not None:
        from .backtest_fixed_structural_lot_native_v3 import load_installed_configuration
        installed,declared_policy,own_proof=load_installed_configuration(client,number=installed_number,parent=parent)
        if policy is None:
            policy=declared_policy
        elif declared_policy!=policy:
            raise ValueError('Caller lot policy differs from actual installed declaration')
    selected = derive_fixed_structural_lot_configuration(payload, policy,
        installed_configuration=installed.payload if installed is not None else None)
    declared_tick = selected['execution_tick']
    if tick is not None and (type(tick) is not float or tick != declared_tick):
        raise ValueError('Caller tick differs from certified inherited execution')
    tick = declared_tick
    quotes = _load_quotes(market,price_authority,client=client)
    selected_json = canonical_json(selected)
    source = PreparedFixedStructuralLotSource(run_id, session_date, parent.attempt_id, parent.token,
        parent.payload_hash, parent.node_hash, parent.source_candidate_id, parent.source_candidate_hash,
        canonical_json(payload), selected_json, sha256(selected_json.encode()).hexdigest(),
        policy, tick, intervals, price_authority, len(intervals._tickers),quotes,
        canonical_json(installed.payload) if installed is not None else '')
    with _ISSUE_LOCK:
        _ISSUED[source] = (_source_identity(source), intervals, price_authority,quotes)
    if installed is not None:
        from .backtest_fixed_structural_lot_native import _issue_installed_source
        _issue_installed_source(source,installed,own_proof)
    return source
