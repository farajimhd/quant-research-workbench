"""Independently issued channel exit images; generic publication stays closed."""
from dataclasses import dataclass
from weakref import WeakKeyDictionary
import json

from .native_channel_exit_journal_context import NativeChannelExitJournalContext
from .native_channel_management_policy import selected_exit_contracts
from .native_channel_writer_profile import validate_channel_writer_profile
from .journal_contract import canonical_json


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class NativeChannelExitPublication:
    run_id: str
    batch_id: str
    sequence: int
    rows_json: str


_ISSUED=WeakKeyDictionary()


def _writer_profile(client):
    return tuple(getattr(client,key,default) for key,default in (
        ('confirmed_original_risk_policy',None),('entry_spread_risk_profile',False),
        ('automatic_ladder_profile',False),('ladder_geometry_policy',None),
        ('fixed_structural_lot_profile',None)))


def _wire_families(unit,policy):
    from .arte_journal_writer import _CONTRACTS,_wire_row,verify_typed_contract_row
    contracts={table.name:table for table in selected_exit_contracts(policy)}
    return {name:[_wire_row(name,row) if name in _CONTRACTS else
        verify_typed_contract_row(contracts[name],row,stored_utc=True) for row in rows]
        for name,rows in unit.sealed_families() if rows}


def issue_native_channel_exit_publication(writer_client,read_client,manager_keeper,broker_keeper,
        receipt,context,*,pending_record_id,native_channel_contexts=()):
    """Rebuild from current native heads before issuing an owner-bound image.

    This does not reserve a journal batch, INSERT, publish a commit or submit
    orders. The dedicated fenced publisher must still verify its predecessor,
    refresh current source/holdings and complete children-before-commit readback.
    """
    from src.backend.backtest_native_channel_exit_unit import prepare_confirmed_native_channel_exit_unit
    if (type(context) is not NativeChannelExitJournalContext
            or getattr(writer_client,'native_channel_management_policy',None)!=context.policy):
        raise ValueError('Channel exit issuance requires exact selected writer and complete context')
    validate_channel_writer_profile(context.policy,
        risk_policy=getattr(writer_client,'confirmed_original_risk_policy',None),
        entry_spread_risk=getattr(writer_client,'entry_spread_risk_profile',False),
        automatic_ladder=getattr(writer_client,'automatic_ladder_profile',False),
        ladder_geometry_policy=getattr(writer_client,'ladder_geometry_policy',None),
        fixed_structural_lot_profile=getattr(writer_client,'fixed_structural_lot_profile',None))
    fresh=prepare_confirmed_native_channel_exit_unit(read_client,manager_keeper,broker_keeper,receipt,
        run_id=context.unit.base.run_id,management_policy=context.policy,contexts=context.contexts,
        pending_record_id=pending_record_id,first_price_source=context.first_price_source,
        recorded_at=context.recorded_at,attempt_id=context.attempt_id,batch_id=context.batch_id,
        parent_record_id=context.parent_record_id,decision_record_id=context.decision_record_id,
        correlation_id=context.correlation_id,causation_id=context.causation_id,
        native_channel_contexts=native_channel_contexts)
    if (fresh.base!=context.unit.base
            or fresh.sealed_families()!=context.unit.sealed_families()):
        raise ValueError('Channel exit publication differs from independently confirmed native unit')
    image=NativeChannelExitPublication(fresh.base.run_id,fresh.base.batch_id,fresh.base.last_sequence,
        canonical_json(_wire_families(fresh,context.policy)))
    _ISSUED[image]=(writer_client,read_client,context,manager_keeper,broker_keeper,receipt,
        pending_record_id,image.run_id,image.batch_id,image.sequence,image.rows_json,
        _writer_profile(writer_client),native_channel_contexts)
    return image


def require_native_channel_exit_publication(image,*,client):
    if type(image) is not NativeChannelExitPublication:
        raise ValueError('Exact independently issued channel exit image required')
    binding=_ISSUED.get(image)
    if (binding is None or binding[0] is not client
            or binding[2].policy!=getattr(client,'native_channel_management_policy',None)
            or binding[2].policy!=getattr(binding[1],'native_channel_management_policy',None)
            or binding[7:11]!=(image.run_id,image.batch_id,image.sequence,image.rows_json)
            or binding[11]!=_writer_profile(client)):
        raise ValueError('Channel exit image is unissued, changed or belongs to another writer')
    return binding[2],json.loads(image.rows_json)


def verify_native_channel_exit_insert(image,*,client,table,rows,sequence,batch_id,token):
    """Admit only a complete frozen family at the issued scalar journal cursor."""
    from .arte_journal_writer import _CONTRACTS,_wire_row,verify_typed_contract_row
    context,families=require_native_channel_exit_publication(image,client=client)
    contracts={contract.name:contract for contract in selected_exit_contracts(context.policy)}
    contract=_CONTRACTS.get(table) or contracts.get(table)
    if (contract is None or table not in families or type(rows) is not tuple
            or (sequence,batch_id)!=(image.sequence,image.batch_id)
            or token!=f'{image.batch_id}:{table}:v4'):
        raise ValueError('Channel exit INSERT differs from issued family or journal cursor')
    wire=tuple(_wire_row(table,row) if table in _CONTRACTS else
        verify_typed_contract_row(contract,row,stored_utc=True) for row in rows)
    if canonical_json(list(wire))!=canonical_json(families[table]):
        raise ValueError('Channel exit INSERT differs from complete frozen native family')
    return contract,wire


def refresh_native_channel_exit_publication(image,*,client):
    """Reprove current heads/holdings before any future writer reservation."""
    context,_=require_native_channel_exit_publication(image,client=client)
    binding=_ISSUED[image]
    from src.backend.backtest_native_channel_exit_unit import prepare_confirmed_native_channel_exit_unit
    fresh=prepare_confirmed_native_channel_exit_unit(binding[1],binding[3],binding[4],binding[5],
        run_id=image.run_id,management_policy=context.policy,contexts=context.contexts,
        pending_record_id=binding[6],first_price_source=context.first_price_source,
        recorded_at=context.recorded_at,attempt_id=context.attempt_id,batch_id=context.batch_id,
        parent_record_id=context.parent_record_id,decision_record_id=context.decision_record_id,
        correlation_id=context.correlation_id,causation_id=context.causation_id,
        native_channel_contexts=binding[12])
    if canonical_json(_wire_families(fresh,context.policy))!=image.rows_json:
        raise ValueError('Channel exit publication no longer matches current native authority')
    return context


def verify_native_channel_exit_publication_graph(image,*,client,batch,base_families,families):
    """Bind the complete shared-publisher graph, without reserving or writing.

    Empty regular families are permitted, but repeated names, omitted children,
    additional records and a different journal envelope are never admitted.
    Current-head refresh and historical retry verification remain separate.
    """
    from .arte_journal_writer import _CONTRACTS,_wire_row,verify_typed_contract_row
    context,expected=require_native_channel_exit_publication(image,client=client)
    if batch!=context.unit.base:
        raise ValueError('Channel exit publication has a foreign journal envelope')
    contracts={contract.name:contract for contract in selected_exit_contracts(context.policy)}

    def normalize(graph,*,regular_only=False):
        result={}
        seen=set()
        for name,rows in graph:
            if name in seen:
                raise ValueError('Channel exit publication repeats a family')
            seen.add(name)
            contract=_CONTRACTS.get(name) or (None if regular_only else contracts.get(name))
            if contract is None:
                raise ValueError('Channel exit publication contains an unselected family')
            if rows:
                result[name]=[_wire_row(name,row) if name in _CONTRACTS else
                    verify_typed_contract_row(contract,row,stored_utc=True) for row in rows]
        return result

    expected_base={name:rows for name,rows in expected.items() if name in _CONTRACTS}
    if (canonical_json(normalize(base_families,regular_only=True))!=canonical_json(expected_base)
            or canonical_json(normalize(families))!=image.rows_json):
        raise ValueError('Channel exit publication differs from its complete issued graph')
    return context


def native_channel_exit_publication_reader(image,*,client):
    """Return the independently bound reader, never a caller-supplied substitute."""
    require_native_channel_exit_publication(image,client=client)
    return _ISSUED[image][1]


def publish_native_channel_exit_publication(client,image,*,verified_prior_prefix):
    """Use the shared detail-first, commit-last protocol for an issued exit."""
    from .arte_journal_commit_v4 import _publish_sealed_batch_v4
    from .arte_journal_writer import _CONTRACTS
    context,_=require_native_channel_exit_publication(image,client=client)
    families=context.unit.sealed_families()
    return _publish_sealed_batch_v4(client,context.unit.base,
        tuple((name,rows) for name,rows in families if name in _CONTRACTS),families,
        verified_prior_prefix=verified_prior_prefix,first_price_source=context.first_price_source,
        native_channel_publication=image)
