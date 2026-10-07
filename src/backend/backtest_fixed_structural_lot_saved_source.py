"""SELECT-only terminal authority from the declared installed lot source.

Persisted proposals are rederived from producer certificates before the cold
walk verifies the journal. No manager Keeper head or live recovery is issued.
"""
from hashlib import sha256

from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.fixed_structural_lot_profile import require_fixed_structural_lot_profile


def require_saved_profile(profile, context, release):
    profile = require_fixed_structural_lot_profile(profile)
    source = profile.operation.source
    payload = source.installed_payload
    if (source.run_id != context['run_id']
            or source.session_date.isoformat() != str(context['session_date'])
            or payload['strategy']['strategy_id'] != context['strategy_id']
            or payload['strategy']['revision'] != int(context['strategy_revision'])
            or sha256(canonical_json(payload).encode()).hexdigest() != context['configuration_hash']
            or release is None or release.payload_hash != context['configuration_hash']
            or release.payload != payload):
        raise ValueError('Saved fixed-lot profile crosses run/session/configuration')
    manifest = payload['strategy'].get('numbered_release', {})
    if context.get('code_hash') != manifest.get('approved_code_fingerprint'):
        raise ValueError('Saved fixed-lot profile crosses approved execution source')
    return profile


def fixed_lot_saved_read_options(client, context, release):
    if release is None:
        from contextlib import closing
        from .backtest_market_data import readonly_clickhouse_client
        from .backtest_strategy_one_configuration import certify_numbered_configuration
        with closing(readonly_clickhouse_client(v3_read_principal=True)) as reader:
            release = certify_numbered_configuration(reader, int(context['strategy_revision']))
    existing = getattr(client, 'fixed_structural_lot_profile', None)
    if existing is not None:
        require_saved_profile(existing, context, release)
        from src.trading_runtime.arte_journal_writer import _validate_fixed_structural_lot_profile
        _validate_fixed_structural_lot_profile(existing,
            automatic_ladder=getattr(client, 'automatic_ladder_profile', False),
            entry_spread_risk=getattr(client, 'entry_spread_risk_profile', False),
            ladder_geometry_policy=getattr(client, 'ladder_geometry_policy', None),
            risk_policy=getattr(client, 'confirmed_original_risk_policy', None))
        return {}
    from .backtest_v4_saved_review import _saved_twenty_price_source
    prepared = _saved_twenty_price_source(client, context['run_id'], context, release,
                                         fixed_lot_session=True)
    prepared._require_issued()
    require_saved_profile(prepared.profile, context, release)
    return {'fixed_structural_lot_profile': prepared.profile}


def load_fixed_lot_saved_prefix(client, run_id, context, release):
    if run_id != context['run_id']:
        raise ValueError('Saved fixed-lot prefix crosses run')
    profile = require_saved_profile(getattr(client, 'fixed_structural_lot_profile', None),
                                   context, release)
    source = profile.operation.source
    from .backtest_fixed_structural_lot_resume import load_persisted_fixed_lot_contexts
    contexts = load_persisted_fixed_lot_contexts(client, source=source)
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    return load_verified_v4_prefix(client, run_id, first_price_source=source.price_authority,
        fixed_lot_contexts=contexts, _fixed_lot_cold_source=source,
        _cold_recovery_context_sink=[])
