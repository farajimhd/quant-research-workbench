"""Native-source declared adapter; complete source@2 scope remains unchanged."""
from .backtest_fixed_structural_lot_source_v5 import (
    SOURCE_INPUT, verify_complete_scope, CertifiedPriceReadbackAuthority,
    FixedStructuralLotPolicy, PreparedFixedStructuralLotSource,
    CertifiedStrategyOneConfiguration, certify_numbered_configuration,
    derive_fixed_structural_lot_configuration, _load_quotes, _ISSUE_LOCK, _ISSUED,
    _source_identity, canonical_json, sha256, encode_nodes, node_hash, UUID, date,
)


def prepare_fixed_structural_lot_source(client, *, run_id, parent_number, session_date, policy, plans, price_authority, tick=None, installed_number=None, through_boundary_ms=57600000):
    """Fresh complete configuration/product preparation, once per operation."""
    if type(run_id) is not str or str(UUID(run_id)) != run_id or type(parent_number) is not int or (type(session_date) is not date) or (type(policy) is not FixedStructuralLotPolicy and (not (policy is None and type(installed_number) is int))) or (type(price_authority) is not CertifiedPriceReadbackAuthority) or (price_authority.run_id != run_id):
        raise ValueError('Exact prepared lot run/source declaration required')
    if policy is not None:
        policy.__post_init__()
    price_authority.__post_init__()
    selected_tickers, intervals = verify_complete_scope(plans, client=client, session_date=session_date, price_authority=price_authority, through_boundary_ms=through_boundary_ms)
    if not selected_tickers:
        raise ValueError('Source@2 empty candidates require the explicit empty factory')
    market = plans.market
    parent = certify_numbered_configuration(client, parent_number)
    if type(parent) is not CertifiedStrategyOneConfiguration:
        raise ValueError('Actual complete parent configuration certificate required')
    payload = parent.payload
    if type(payload) is not dict or payload['strategy']['revision'] != parent_number or sha256(canonical_json(payload).encode()).hexdigest() != parent.payload_hash or (node_hash(encode_nodes(payload)) != parent.node_hash):
        raise ValueError('Parent configuration content differs from its certificate')
    installed = None
    if installed_number is not None:
        from .backtest_fixed_structural_lot_native_v8 import load_installed_configuration
        installed, declared_policy, own_proof = load_installed_configuration(client, number=installed_number, parent=parent)
        if policy is None:
            policy = declared_policy
        elif declared_policy != policy:
            raise ValueError('Caller lot policy differs from actual installed declaration')
    selected = derive_fixed_structural_lot_configuration(payload, policy, installed_configuration=installed.payload if installed is not None else None)
    declared_tick = selected['execution_tick']
    if tick is not None and (type(tick) is not float or tick != declared_tick):
        raise ValueError('Caller tick differs from certified inherited execution')
    tick = declared_tick
    quotes = _load_quotes(market, price_authority, client=client)
    selected_json = canonical_json(selected)
    source = PreparedFixedStructuralLotSource(run_id, session_date, parent.attempt_id, parent.token, parent.payload_hash, parent.node_hash, parent.source_candidate_id, parent.source_candidate_hash, canonical_json(payload), selected_json, sha256(selected_json.encode()).hexdigest(), policy, tick, intervals, price_authority, len(intervals._tickers), quotes, canonical_json(installed.payload) if installed is not None else '')
    with _ISSUE_LOCK:
        _ISSUED[source] = (_source_identity(source), intervals, price_authority, quotes)
    if installed is not None:
        from .backtest_fixed_structural_lot_native import _issue_installed_source
        _issue_installed_source(source, installed, own_proof)
    return source
