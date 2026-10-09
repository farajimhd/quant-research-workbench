"""Complete installed source admission for declared management preparation."""
from .backtest_fixed_structural_lot_native_v5 import (
    sha256, canonical_json, encode_nodes, node_hash,
    certify_numbered_configuration, CertifiedStrategyOneConfiguration,
    numbered_strategy, numbered_strategy_parent, fixed_strategy_executor,
    verify_current_installed_source, parse_fixed_structural_lot_policy,
    NativeFixedStructuralLotOperation, _issue_installed_source,
)


def verify_installed_configuration(parent, own, release, parent_release):
    from src.trading_runtime.declared_native_manifest import registered_manifest_authority
    from src.trading_runtime.fixed_lot_management_native_preparation import uses_management_native_preparation
    from src.trading_runtime.fixed_lot_management_reuse_policy import declared_management_reuse_policy
    from src.trading_runtime.fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
    if type(parent) is not CertifiedStrategyOneConfiguration or type(own) is not CertifiedStrategyOneConfiguration:
        raise ValueError('Exact complete installed and parent certificates required')
    release.verify()
    parent_release.verify()
    if not uses_management_native_preparation(release):
        raise ValueError('Installed source lacks declared management native preparation')
    authority = registered_manifest_authority(release.number)
    if (authority is None or authority.parent_number != parent.strategy_number
            or numbered_strategy(parent.strategy_number) != parent_release
            or authority.parent_release_factory() != parent_release
            or numbered_strategy(release.number) != release
            or own.strategy_number != release.number
            or own.payload['strategy']['strategy_id'] != release.executor_strategy_id):
        raise ValueError('Installed native preparation differs from exact registered authority')
    registered = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision).contract_factory()
    if type(registered) is not FixedStructuralLotSelectedExitStrategyContract or registered.release != release:
        raise ValueError('Installed management factory differs from exact typed contract')
    registered.__post_init__()
    for certificate in (parent, own):
        if (sha256(canonical_json(certificate.payload).encode()).hexdigest() != certificate.payload_hash
                or node_hash(encode_nodes(certificate.payload)) != certificate.node_hash):
            raise ValueError('Full installed configuration nodes/content differ')
    # Complete code-owned inheritance replaces no legacy validator or source pin.
    expected = authority.verify_manifest(parent, own.payload)
    if (expected['payload'] != own.payload or expected['payload_hash'] != own.payload_hash
            or expected['node_hash'] != own.node_hash
            or expected['source_candidate_id'] != own.source_candidate_id
            or expected['source_candidate_hash'] != own.source_candidate_hash):
        raise ValueError('Installed preparation differs from complete inherited compiler tree')
    params = own.payload['strategy']['parameters']
    management = declared_management_reuse_policy(release, params.get('management_reuse_policy'))
    if management is None or management != registered.management_reuse_policy:
        raise ValueError('Installed preparation lacks exact inherited management policy')
    policy = parse_fixed_structural_lot_policy(params.get('fixed_structural_lot_policy'))
    if policy != registered.fixed_structural_lot_policy:
        raise ValueError('Installed lot policy differs from exact factory')
    return policy


def load_installed_configuration(client, *, number, parent):
    if type(number) is not int or number <= 0 or numbered_strategy_parent(number) != parent.strategy_number:
        raise ValueError('Installed preparation has a different registered parent')
    release = numbered_strategy(number)
    own = certify_numbered_configuration(client, number)
    policy = verify_installed_configuration(parent, own, release, numbered_strategy(parent.strategy_number))
    from .backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    proof = certify_numbered_fixed_v4_projection(number)
    if type(proof) is not str or len(proof) != 64 or any(c not in '0123456789abcdef' for c in proof):
        raise ValueError('Actual complete installed source proof required')
    verify_current_installed_source(own)
    return own, policy, proof


def prepare_native_fixed_structural_lot_operation(client, *, number, run_id,
        session_date, plans, price_authority, through_boundary_ms=57600000):
    from .backtest_fixed_structural_lot_source_v20 import prepare_fixed_structural_lot_source
    source = prepare_fixed_structural_lot_source(client, run_id=run_id,
        parent_number=numbered_strategy_parent(number), session_date=session_date,
        policy=None, plans=plans, price_authority=price_authority,
        installed_number=number, through_boundary_ms=through_boundary_ms)
    return NativeFixedStructuralLotOperation(source)
