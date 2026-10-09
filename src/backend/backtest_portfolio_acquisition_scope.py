"""Original committed native window authority for declared Portfolio quotas."""
from dataclasses import dataclass
from types import MappingProxyType
from weakref import WeakKeyDictionary
from datetime import date
from hashlib import sha256
from src.trading_runtime.portfolio_acquisition_limit import SessionAcquisitionLimit
from src.trading_runtime.portfolio_acquisition_policy import declared_acquisition_limit

_ISSUED = WeakKeyDictionary()

@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class NativeSessionAcquisitionAuthority:
    run_id: str
    configuration_hash: str
    definition_hash: str
    strategy_id: str
    strategy_revision: int
    scopes: MappingProxyType


def require_acquisition_authority(value, *, run_id, account_ids, strategy_id, strategy_revision):
    if type(value) is not NativeSessionAcquisitionAuthority or value not in _ISSUED:
        raise ValueError('Native acquisition scope requires committed issued authority')
    binding=(value.run_id,value.configuration_hash,value.definition_hash,value.strategy_id,value.strategy_revision,tuple((account,scope.run_id,scope.account_id,scope.begins_at,scope.ends_at,scope.maximum) for account,scope in value.scopes.items()))
    if binding != _ISSUED[value] or value.run_id != run_id or set(value.scopes) != set(account_ids) or (value.strategy_id,value.strategy_revision)!=(strategy_id,strategy_revision):
        raise ValueError('Native acquisition scope ownership/content differs')
    for scope in value.scopes.values():
        if type(scope) is not SessionAcquisitionLimit:raise ValueError('Native acquisition scope type differs')
        scope.__post_init__()
    return value


def issue_native_acquisition_authority(client, *, definition, run_id, account_ids):
    """Read independently committed definition; never use cursor/anchor clocks."""
    from src.backend.replay_run_service import ReplayRunDefinition, RunMode
    from src.trading_runtime.arte_journal_writer import load_typed_run_context
    from src.trading_runtime.arte_backtest_definition import load_backtest_definition, prepare_backtest_definition
    from src.trading_runtime.journal_contract import canonical_json
    if type(definition) is not ReplayRunDefinition or definition.mode != RunMode.BACKTEST:
        raise ValueError('Native acquisition quota requires an exact Backtest definition')
    payload=definition.configuration_revision.get('payload')
    maximum=declared_acquisition_limit(payload)
    if maximum is None:return None
    if (type(account_ids) is not tuple or not account_ids or len(set(account_ids))!=len(account_ids)
            or (definition.final_session_date or definition.session_date)!=definition.session_date):
        raise ValueError('Native acquisition quota requires one independently owned session')
    context=load_typed_run_context(client,run_id)
    if tuple(context['account_ids'])!=account_ids:
        raise ValueError('Native acquisition quota accounts differ from committed run')
    saved=load_backtest_definition(client,run_id,run_context=context)
    expected=prepare_backtest_definition(run_id,definition,run_month=date.fromisoformat(context['run_month']))
    # Complete normalized definition, not just the two window scalars.
    if (sha256(canonical_json(payload).encode()).hexdigest()!=context['configuration_hash']
            or context['configuration_hash']!=definition.configuration_revision['content_hash']
            or context['session_date']!=definition.session_date.isoformat()
            or canonical_json(saved)!=canonical_json(expected)):
        raise ValueError('Native acquisition quota differs from original committed definition/configuration')
    value=NativeSessionAcquisitionAuthority(run_id,context['configuration_hash'],saved['definition']['content_hash'],context['strategy_id'],context['strategy_revision'],
        MappingProxyType({account:SessionAcquisitionLimit(run_id,account,definition.requested_start,
            definition.session_end,maximum) for account in account_ids}))
    _ISSUED[value]=(value.run_id,value.configuration_hash,value.definition_hash,value.strategy_id,value.strategy_revision,tuple((account,scope.run_id,scope.account_id,scope.begins_at,scope.ends_at,scope.maximum) for account,scope in value.scopes.items()))
    return require_acquisition_authority(value,run_id=run_id,account_ids=account_ids,strategy_id=context['strategy_id'],strategy_revision=context['strategy_revision'])
