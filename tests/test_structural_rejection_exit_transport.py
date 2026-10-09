"""Real journal→typed batch; source/financial checkpoint fixtures are seams."""
from dataclasses import replace
from datetime import date
from types import MappingProxyType,SimpleNamespace

import pytest

from test_backtest_structural_rejection_journal import journal_fixture
from test_arte_structural_rejection_exit_v1 import PARENT
from test_profit_armed_structural_rejection_publication import BATCH
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.trading_runtime.structural_rejection_exit_transport import (
    require_structural_rejection_exit_batch,prepare_structural_rejection_exit_batch)


def case(monkeypatch):
    journal,confirmation,intent,context=journal_fixture(monkeypatch)
    record=journal.append_structural_rejection_exit(confirmation=confirmation,intent=intent)
    unit,=project_pending_backtest_v4_prefix(journal,attempt_id=BATCH,run_month=date(2026,8,1),
        prior_sequence=9,prior_batch_id=PARENT,through_sequence=10,
        expected_config={'mode':'backtest','strategy_id':'early-squeeze-strategy','strategy_revision':57})
    return unit,journal,confirmation,context,record


def test_exact_typed_batch_preserves_evidence_and_is_usable_after_pending_retirement(monkeypatch):
    unit,journal,confirmation,context,record=case(monkeypatch)
    assert require_structural_rejection_exit_batch(unit) is unit
    assert unit.base.first_sequence==unit.base.last_sequence==10
    assert unit.evidence['parent_record_id']==record.record_id
    assert unit.evidence['batch_id']==unit.base.batch_id
    assert unit.evidence['source_manager_checkpoint_sequence']==9
    context.profile.owner.complete_requests((confirmation.request,),boundary_ms=25000)
    context.profile.owner.retire(('DU1','A1','AAA'))
    assert require_structural_rejection_exit_batch(unit) is unit
    assert len(journal.records(journal.run_id,after_sequence=9))==1


@pytest.mark.parametrize('kind',('copy','base-identity','row-identity','nested-parent','nested-event'))
def test_typed_batch_cannot_be_forged_or_mutated_during_async_use(monkeypatch,kind):
    unit,*_=case(monkeypatch)
    if kind=='copy': unit=replace(unit)
    elif kind=='base-identity': object.__setattr__(unit,'base',replace(unit.base))
    elif kind=='row-identity': object.__setattr__(unit,'evidence',MappingProxyType(dict(unit.evidence)))
    elif kind=='nested-parent': object.__setattr__(unit.base,'intents',(
        MappingProxyType(dict(unit.base.intents[0],execution_deadline_ms=751)),))
    else: object.__setattr__(unit.base,'events',(MappingProxyType(dict(unit.base.events[0],sequence=11)),))
    with pytest.raises(ValueError): require_structural_rejection_exit_batch(unit)


@pytest.mark.parametrize('kind',('policy','account','sequence','status','foreign-family'))
def test_transport_checks_complete_parent_before_issuing_unit(monkeypatch,kind):
    unit,journal,_,_,record=case(monkeypatch)
    base=unit.base
    if kind=='policy': base=replace(base,intents=(dict(base.intents[0],execution_deadline_ms=751),))
    elif kind=='account': base=replace(base,events=(dict(base.events[0],account_id='foreign'),))
    elif kind=='sequence': base=replace(base,first_sequence=9,last_sequence=9)
    elif kind=='status': base=replace(base,status='completed')
    else: base=replace(base,backtest_cursors=({'foreign':True},))
    with pytest.raises(ValueError):
        prepare_structural_rejection_exit_batch(base,journal.structural_rejection_exit_for_record(record.record_id))


def test_scalar_schema_does_not_allow_bare_insert_before_native_writer_admission(monkeypatch):
    from src.trading_runtime.arte_journal_writer import _insert
    from src.trading_runtime.arte_structural_rejection_exit_v1 import EXIT
    unit,*_=case(monkeypatch)
    client=SimpleNamespace(execute=lambda *a:pytest.fail('Bare exit INSERT reached transport'))
    with pytest.raises(ValueError,match='own native writer admission'):
        _insert(client,EXIT.name,(dict(unit.evidence),),'token',journal_profile='backtest_v4')


def test_unwrapped_base_cannot_publish_without_own_native_companion(monkeypatch):
    from src.trading_runtime.arte_journal_commit_v4 import _publish_typed_batch_v4
    unit,*_=case(monkeypatch)
    with pytest.raises(ValueError,match='own native publication route'):
        _publish_typed_batch_v4(None,unit.base)
