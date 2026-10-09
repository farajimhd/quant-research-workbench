"""Issued source/client image and fresh head checks; cold readers are seams."""
from dataclasses import replace
from types import MappingProxyType,SimpleNamespace

import pytest

from test_structural_rejection_exit_transport import case
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.profit_armed_structural_rejection_publication import _bound
from src.trading_runtime import structural_rejection_exit_verified_publication as verified


def fixture(monkeypatch):
    unit,_,_,context,_=case(monkeypatch)
    client=_bound(context)[1]
    prefix=V4CommittedPrefix(unit.base.run_id,9,unit.base.prior_batch_id,'cursor','running',(unit.base.prior_batch_id,))
    current={'prefix':prefix};calls=[]
    def load(*a,**k):
        calls.append('head');return current['prefix']
    def native(client_arg,rows,intents,events,**kwargs):
        assert client_arg is client and intents is unit.base.intents and events is unit.base.events
        assert kwargs['verified_prefix']==prefix
        calls.append('native');return tuple(MappingProxyType(dict(row)) for row in rows)
    monkeypatch.setattr('src.trading_runtime.arte_journal_commit_v4.load_writer_v4_snapshot_prefix',load)
    monkeypatch.setattr('src.trading_runtime.structural_rejection_exit_publication.prepare_native_structural_rejection_exit_rows',native)
    issued=verified.issue_verified_structural_rejection_exit_publication(client,unit,
        verified_prior_prefix=prefix,first_price_source=context.profile.owner.price_authority)
    return issued,client,current,calls


def test_exact_image_rechecks_all_native_gates_before_companion_insert(monkeypatch):
    issued,client,_,calls=fixture(monkeypatch)
    assert calls==['head','native','head']
    assert verified.require_verified_structural_rejection_exit_publication(issued,client=client) is issued
    assert calls==['head','native','head']
    verified.verify_structural_rejection_exit_insert(issued,client=client,rows=issued.rows,
        run_id=issued.unit.base.run_id,sequence=10,batch_id=issued.unit.base.batch_id)
    assert calls==['head','native','head','head','native','head']


@pytest.mark.parametrize('kind',('copy','rows','unit','profile','client','nested-unit'))
def test_context_cannot_be_forged_rebound_or_mutated(monkeypatch,kind):
    issued,client,_,_=fixture(monkeypatch)
    if kind=='copy': issued=replace(issued)
    elif kind=='rows': object.__setattr__(issued,'rows',tuple(dict(row) for row in issued.rows))
    elif kind=='unit': object.__setattr__(issued,'unit',replace(issued.unit))
    elif kind=='profile': object.__setattr__(issued,'profile',object())
    elif kind=='client': client=SimpleNamespace(structural_rejection_profile=issued.profile)
    else: object.__setattr__(issued.unit.base,'events',())
    with pytest.raises(ValueError): verified.require_verified_structural_rejection_exit_publication(issued,client=client)


@pytest.mark.parametrize('kind',('head','native','row','sequence','batch','run'))
def test_fresh_native_or_exact_insert_mismatch_fails_closed(monkeypatch,kind):
    issued,client,current,_=fixture(monkeypatch)
    rows=issued.rows;sequence=10;batch=issued.unit.base.batch_id;run=issued.unit.base.run_id
    if kind=='head': current['prefix']=replace(current['prefix'],last_sequence=10)
    elif kind=='native':
        def fail(*a,**k): raise ValueError('native financial/source evidence changed')
        monkeypatch.setattr('src.trading_runtime.structural_rejection_exit_publication.prepare_native_structural_rejection_exit_rows',fail)
    elif kind=='row': rows=({**dict(rows[0]),'position_quantity':100.},)
    elif kind=='sequence': sequence=11
    elif kind=='batch': batch='foreign'
    else: run='foreign'
    with pytest.raises(ValueError):
        verified.verify_structural_rejection_exit_insert(issued,client=client,rows=rows,
            run_id=run,sequence=sequence,batch_id=batch)


def test_native_head_change_during_initial_verification_never_issues_context(monkeypatch):
    issued,client,current,_=fixture(monkeypatch)
    def advance(*a,**k):
        current['prefix']=replace(current['prefix'],last_sequence=11)
        return issued.rows
    monkeypatch.setattr('src.trading_runtime.structural_rejection_exit_publication.prepare_native_structural_rejection_exit_rows',advance)
    with pytest.raises(ValueError,match='predecessor'):
        verified.issue_verified_structural_rejection_exit_publication(client,issued.unit,
            verified_prior_prefix=current['prefix'],first_price_source=issued.profile.owner.price_authority)


def test_insert_context_cannot_authorize_foreign_table_or_mixed_snapshot_channels(monkeypatch):
    from src.trading_runtime.arte_journal_writer import _insert
    from src.trading_runtime.arte_structural_rejection_exit_v1 import EXIT
    issued,client,_,_=fixture(monkeypatch)
    with pytest.raises(ValueError,match='foreign table'):
        _insert(client,'trading_event_v1',(),'token',journal_profile='backtest_v4',
            dispatch_structural_rejection_exit_context=issued)
    with pytest.raises(ValueError,match='own native writer admission'):
        _insert(client,EXIT.name,issued.rows,'token',journal_profile='backtest_v4',
            dispatch_sequence=10,dispatch_batch_id=issued.unit.base.batch_id,
            dispatch_structural_rejection_exit_context=issued,dispatch_run_context=True)
