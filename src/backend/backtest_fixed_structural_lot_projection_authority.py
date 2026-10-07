"""Issued fixed-lot projection scope, separate from the exact runtime config."""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from threading import RLock
from weakref import WeakKeyDictionary

from src.trading_runtime.journal_contract import canonical_json

PROJECTION_RULE = 'fixed-structural-lot-projection-authority@2'
_ISSUED = WeakKeyDictionary()
_LOCK = RLock()


def uses_projection_authority(source):
    from .backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    from src.trading_runtime.strategy_registry import numbered_strategy
    require_native_fixed_structural_lot_source(source)
    if source.installed_payload is None:
        return False
    release = numbered_strategy(source.installed_payload['strategy']['revision'])
    return PROJECTION_RULE in release.rule_set_contracts


def runtime_config_from_context(context):
    """Reconstruct the canonical Start projection from the fenced typed schema."""
    from src.trading_runtime.runtime import RunConfig, RunMode, typed_run_config_payload
    for key in ('safety_supervisor_enabled', 'write_progress_checkpoints'):
        if type(context[key]) not in (bool, int) or context[key] not in (0, 1):
            raise ValueError('Typed runtime boolean is invalid')
    config = RunConfig(mode=RunMode(context['mode']), strategy_id=context['strategy_id'],
        strategy_revision=int(context['strategy_revision']), account_ids=tuple(context['account_ids']),
        anchor_date=date.fromisoformat(str(context['anchor_date'])), run_id=context['run_id'],
        run_plan_id=context['run_plan_id'], safety_supervisor_enabled=bool(context['safety_supervisor_enabled']),
        checkpoint_interval_events=int(context['checkpoint_interval_events']),
        write_progress_checkpoints=bool(context['write_progress_checkpoints']))
    return typed_run_config_payload(config)


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class FixedLotProjectionAuthority:
    source: object
    run_id: str
    context_json: str
    runtime_config_json: str
    parent_configuration_hash: str
    selected_configuration_hash: str


def issue_fixed_lot_projection_authority(client, source, expected_config):
    from src.trading_runtime.arte_journal_writer import load_typed_run_context
    if not uses_projection_authority(source):
        raise ValueError('Separate projection authority is not declared')
    source.require_installed_admission()
    context = load_typed_run_context(client, source.run_id)
    payload = source.installed_payload
    config = runtime_config_from_context(context)
    if (context['mode'] != 'backtest' or context['run_id'] != source.run_id
            or str(context['session_date']) != source.session_date.isoformat()
            or config['strategy_id'] != payload['strategy']['strategy_id']
            or config['strategy_revision'] != payload['strategy']['revision']
            or context['configuration_hash'] != sha256(canonical_json(payload).encode('utf-8')).hexdigest()
            or context['code_hash'] != payload['strategy']['numbered_release']['approved_code_fingerprint']
            or canonical_json(expected_config) != canonical_json(config)):
        raise ValueError('Fixed-lot projection differs from published run/configuration/source')
    authority = FixedLotProjectionAuthority(source, source.run_id, canonical_json(context), canonical_json(config),
        source.parent_payload_hash, source.selected_configuration_hash)
    with _LOCK:
        _ISSUED[authority] = (source, authority.run_id, authority.context_json, authority.runtime_config_json,
            authority.parent_configuration_hash, authority.selected_configuration_hash)
    return authority


def require_fixed_lot_projection_authority(authority, *, source, run_id, expected_config):
    if type(authority) is not FixedLotProjectionAuthority:
        raise ValueError('Issued fixed-lot projection authority required')
    with _LOCK:
        issued = _ISSUED.get(authority)
    if (issued != (authority.source, authority.run_id, authority.context_json, authority.runtime_config_json,
            authority.parent_configuration_hash, authority.selected_configuration_hash)
            or authority.source is not source or authority.run_id != run_id
            or canonical_json(expected_config) != authority.runtime_config_json
            or authority.parent_configuration_hash != source.parent_payload_hash
            or authority.selected_configuration_hash != source.selected_configuration_hash):
        raise ValueError('Fixed-lot projection authority crosses source/run/configuration')
    source.require_installed_admission()
    return authority
