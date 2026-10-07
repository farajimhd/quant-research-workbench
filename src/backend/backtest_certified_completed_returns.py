"""Installed/certified attempt authority; never caller-authored output witnesses."""
from datetime import date
from src.backend.backtest_completed_endpoint_returns import load_completed_return_projection
from src.market_engine.completed_endpoint_return_contract import (
    CompletedReturnSourcePlan, ReturnSourceRequest,
)
from src.market_engine.completed_return_campaign_contract import certify_native_population


def load_installed_completed_returns(market, packet_index, client, *, source_kind, authority,
                                    structural_declaration=None,structural_authority=None):
    """Derive keys/attempt/seals from certified population and installed receipt.

    Separate native-release dependency registration remains mandatory. This
    loader does not declare itself a substitute for the saved-run preflight.
    """
    # Producer publication functions are not imported or called by this reader.
    from src.backend.backtest_completed_return_campaign_store import read_certificate, storage_preflight
    population = certify_native_population(market, client, source_kind=source_kind,
        structural_declaration=structural_declaration,structural_authority=structural_authority)
    storage_preflight(client)
    keys, attempt = population.packet(packet_index)
    request = ReturnSourceRequest(market, keys)
    installed = read_certificate(client, request, attempt)
    if installed.num_rows != 1 or any(installed[name].null_count for name in installed.schema.names):
        raise ValueError('Missing installed return packet certification')
    receipt = installed.to_pylist()[0]
    expected = dict(build_id=market.build_id, session_date=date.fromisoformat(market.sessions[0]),
        feature_attempt_id=attempt, population_token=population.token, population_keys_hash=population.keys_hash,
        decision_source_kind=population.source_kind.value,
        decision_source_token=population.candidate_token, population_count=len(population.keys),
        packet_index=packet_index, packet_count=population.packet_count, requested_count=len(keys),
        producer_source_hash=population.producer_source_hash, campaign_source_hash=population.campaign_source_hash,
        policy_digest=request.policy.digest)
    if any(receipt[name] != value for name, value in expected.items()):
        raise ValueError('Installed return certification differs from native population/source identity')
    source = CompletedReturnSourcePlan(request, attempt, receipt['producer_source_hash'],
        receipt['feature_hash'], receipt['coverage_hash'], receipt['projection_token'])
    from src.market_engine.completed_return_insert_authority import KeeperProductInsertAuthority
    if type(authority) is not KeeperProductInsertAuthority:
        raise ValueError("Installed reader requires typed producer completion authority")
    authority.assert_complete(attempt, source.token)
    return load_completed_return_projection(source, client)
