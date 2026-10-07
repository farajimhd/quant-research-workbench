"""Reader binding controls; controlled owner seam is not source certification."""
from datetime import date
from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.backend import backtest_fixed_structural_lot_saved_source as saved
from src.backend.backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
from src.backend.backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.fixed_structural_lot_profile import (
    FixedStructuralLotDeclaredProfile, issue_fixed_structural_lot_profile,
)
from src.trading_runtime.journal_contract import canonical_json


def controlled_profile(monkeypatch):
    payload = {'strategy': {'strategy_id': 'early-squeeze-strategy', 'revision': 81,
        'numbered_release': {'approved_code_fingerprint': 'b'*64}}}
    source = object.__new__(PreparedFixedStructuralLotSource)
    for name, value in {'installed_json': canonical_json(payload),
            'selected_configuration_hash': 'a'*64,
            'run_id': '00000000-0000-0000-0000-000000000081',
            'session_date': date(2026,8,4), 'policy': FixedStructuralLotPolicy()}.items():
        object.__setattr__(source,name,value)
    # Explicit owner isolation only; production factories and cold walks are
    # tested separately. The actual issued profile registry remains intact.
    monkeypatch.setattr(PreparedFixedStructuralLotSource,'require_prepared_source',lambda self: None)
    monkeypatch.setattr(PreparedFixedStructuralLotSource,'require_installed_admission',lambda self: None)
    profile=issue_fixed_structural_lot_profile(NativeFixedStructuralLotOperation(source))
    digest=sha256(canonical_json(payload).encode()).hexdigest()
    context={'run_id':source.run_id,'session_date':'2026-08-04',
        'strategy_id':'early-squeeze-strategy','strategy_revision':81,
        'configuration_hash':digest,'code_hash':'b'*64}
    release=SimpleNamespace(payload=payload,payload_hash=digest)
    return profile,context,release


def test_constructed_saved_profile_fails_before_any_read():
    with pytest.raises(ValueError,match='not issued'):
        saved.require_saved_profile(FixedStructuralLotDeclaredProfile(object()),{},None)


def test_matching_issued_reader_profile_is_retained_without_repreparation(monkeypatch):
    profile,context,release=controlled_profile(monkeypatch)
    assert saved.fixed_lot_saved_read_options(SimpleNamespace(fixed_structural_lot_profile=profile),
        context,release)=={}


@pytest.mark.parametrize('field,value',[('run_id','foreign'),('session_date','2026-08-05'),
    ('strategy_id','foreign'),('strategy_revision',80),('configuration_hash','f'*64),
    ('code_hash','f'*64)])
def test_existing_profile_cannot_cross_saved_identity(monkeypatch,field,value):
    profile,context,release=controlled_profile(monkeypatch)
    context={**context,field:value}
    with pytest.raises(ValueError,match='crosses'):
        saved.fixed_lot_saved_read_options(SimpleNamespace(fixed_structural_lot_profile=profile),
            context,release)


def test_report_select_wrapper_retains_selected_profile_and_blocks_mutations():
    from scripts.clickhouse.report_strategy_one_trades import SelectOnly
    marker=object()
    class Transport:
        base_url='controlled';user='controlled';password='controlled'
        fixed_structural_lot_profile=marker
        def execute(self,query):
            raise AssertionError('Mutation reached transport')
    wrapper=SelectOnly(Transport())
    assert wrapper.fixed_structural_lot_profile is marker
    assert wrapper.declared_read_wrapper(Transport()).fixed_structural_lot_profile is marker
    with pytest.raises(ValueError):wrapper.execute('INSERT INTO arte.example VALUES (1)')


def test_saved_reader_rejects_mixed_declared_profiles(monkeypatch):
    profile,context,release=controlled_profile(monkeypatch)
    with pytest.raises(ValueError,match='cannot mix'):
        saved.fixed_lot_saved_read_options(SimpleNamespace(fixed_structural_lot_profile=profile,
            automatic_ladder_profile=True),context,release)
