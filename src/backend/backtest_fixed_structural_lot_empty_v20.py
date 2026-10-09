"""Source@2 positive empty horizon, retaining original empty capability owner."""
from .backtest_fixed_structural_lot_empty import (
    PreparedEmptyFixedStructuralLotSource, _LOCK, _ISSUED, _same_candidates,
    date, UUID, canonical_json, sha256,
)
def prepare_empty_fixed_structural_lot_source(client, *, number,run_id,session_date,
        plans,through_boundary_ms):
    market,candidates = plans.market,plans.candidates
    from .backtest_market_data import CertifiedMarketDayPlan,verify_market_day_plan
    from .backtest_strategy_one_candidate_store import (
        CertifiedCandidatePlan,certify_candidate_plan,exclude_candidate_tickers,
        project_candidate_plan,RULE_DIGEST)
    from .backtest_input_scope import input_exclusions
    from .backtest_fixed_structural_lot_native_v20 import (
        numbered_strategy_parent,certify_numbered_configuration,load_installed_configuration,
        _issue_installed_source)
    from .backtest_fixed_structural_lot_source import derive_fixed_structural_lot_configuration
    if (type(market) is not CertifiedMarketDayPlan or type(candidates) is not CertifiedCandidatePlan
            or type(session_date) is not date or market.sessions!=(session_date.isoformat(),)
            or type(run_id) is not str or str(UUID(run_id))!=run_id
            or type(through_boundary_ms) is not int or not 0<through_boundary_ms<=57_600_000
            or through_boundary_ms%100):
        raise ValueError('Empty native horizon requires exact source identity and certified clock')
    # Actual complete market rows and candidate coverage are rechecked before
    # any scope projection. An absent product is never an empty certificate.
    verify_market_day_plan(market,client=client)
    full=certify_candidate_plan(market,candidate_rule_digest=RULE_DIGEST,
        through_boundary_ms=57_600_000,client=client)
    occupied={row.ticker for row in full.coverage if row.candidate_count>0}
    exclusions=tuple(ticker for ticker in input_exclusions(session_date.isoformat()) if ticker in occupied)
    fresh=exclude_candidate_tickers(full,exclusions)
    if (not _same_candidates(fresh,candidates) or project_candidate_plan(fresh,through_boundary_ms=through_boundary_ms).prepared):
        raise ValueError('Declared empty horizon differs from genuine complete candidate source')
    from .backtest_market_data import project_market_day_plan, project_empty_market_day_plan
    from .backtest_strategy_one_preparation import strategy_one_v7_tickers
    from .backtest_strategy_one_plan import StrategyOneFixedPlans
    selected = strategy_one_v7_tickers(fresh.prepared)
    expected = (project_market_day_plan(market, selected) if selected else
        project_empty_market_day_plan(market, empty_candidate_token=fresh.token))
    if type(plans) is not StrategyOneFixedPlans or plans.execution_market != expected:
        raise ValueError('Source@2 empty horizon carries altered complete candidate execution scope')
    if not selected and any(v is not None for v in (plans.seeds,plans.v7_intervals,
            plans.activations,plans.pivots,plans.hod,plans.entry)):
        raise ValueError('Source@2 positive empty candidates carry foreign nonempty inputs')
    if selected and (plans.seeds is None or plans.v7_intervals is None or plans.entry is None
            or plans.activations is None or plans.pivots is None or plans.hod is None):
        raise ValueError('Source@2 nonempty candidates lack complete certified inputs')
    if selected:
        from .backtest_fixed_structural_lot_source_v5 import verify_complete_scope
        verify_complete_scope(plans, client=client, session_date=session_date,
            through_boundary_ms=through_boundary_ms, require_price_authority=False)
    parent=certify_numbered_configuration(client,numbered_strategy_parent(number))
    own,policy,proof=load_installed_configuration(client,number=number,parent=parent)
    parent_json=canonical_json(parent.payload)
    if sha256(parent_json.encode()).hexdigest()!=parent.payload_hash:
        raise ValueError('Empty source parent content differs from its immutable certificate')
    selected_json=canonical_json(derive_fixed_structural_lot_configuration(parent.payload,
        policy,installed_configuration=own.payload))
    source=PreparedEmptyFixedStructuralLotSource(run_id,session_date,through_boundary_ms,
        market,fresh,parent_json,canonical_json(own.payload),selected_json,policy,
        parent.payload_hash,sha256(selected_json.encode()).hexdigest())
    with _LOCK:_ISSUED[source]=(market,fresh,run_id,session_date,through_boundary_ms,
        source.parent_json,source.installed_json,source.selected_json,policy,
        source.parent_payload_hash,source.selected_configuration_hash)
    _issue_installed_source(source,own,proof)
    return source
