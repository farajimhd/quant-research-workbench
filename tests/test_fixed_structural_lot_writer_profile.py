"""Actual selected writer/cold-profile routing with explicit owner issuance seam."""
from types import SimpleNamespace
import pytest
from src.trading_runtime import arte_journal_writer as writer
from src.backend.backtest_fixed_journal_bootstrap import _v4_cold_reader_preflight
from src.trading_runtime.fixed_structural_lot_profile import (
    issue_fixed_structural_lot_profile,FixedStructuralLotDeclaredProfile,selected_fixed_structural_lot_tables)


def issued(monkeypatch):
    from src.backend.backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
    from src.backend.backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
    from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
    source=object.__new__(PreparedFixedStructuralLotSource)
    for key,value in dict(installed_json='{"component_owner":true}',selected_configuration_hash='a'*64,
            run_id='controlled-run',session_date='controlled-day',policy=FixedStructuralLotPolicy()).items():
        object.__setattr__(source,key,value)
    # Explicit synthetic prepared/installed issuance seam, not a source proof.
    monkeypatch.setattr(PreparedFixedStructuralLotSource,'require_prepared_source',lambda self:self)
    monkeypatch.setattr(PreparedFixedStructuralLotSource,'require_installed_admission',lambda self:None)
    return issue_fixed_structural_lot_profile(NativeFixedStructuralLotOperation(source))


def client(profile=None,principal='backtest_v4_fixed_structural_lot_runner'):
    class Client:
        automatic_ladder_profile=False
        entry_spread_risk_profile=False
        ladder_geometry_policy=None
        confirmed_original_risk_policy=None
        fixed_structural_lot_profile=profile
        def execute(self,sql):
            if sql=='SELECT currentUser()':return principal
            if "getSetting('readonly')" in sql:return '1'
            if 'system.tables' in sql:return ''
            raise AssertionError(sql)
    return Client()


def test_real_preflight_adds_exact_six_and_preserves_baseline(monkeypatch):
    profile=issued(monkeypatch);storage=[];permissions=[]
    monkeypatch.setattr(writer,'storage_preflight',lambda c,*,tables:storage.append(tuple(tables)))
    monkeypatch.setattr(writer,'journal_permission_preflight',lambda c,**kwargs:permissions.append(kwargs))
    seal=writer._v4_preflight(client(profile))
    assert seal.fixed_structural_lot_profile is profile
    assert storage==[writer.v4_storage_contracts(),selected_fixed_structural_lot_tables(profile)]
    assert permissions[0]['journal_tables']==writer.v4_journal_write_tables()|frozenset(t.name for t in storage[1])


@pytest.mark.parametrize('change',['foreign_principal','forged','mixed'])
def test_actual_preflight_rejects_selected_identity_before_grants(monkeypatch,change):
    profile=issued(monkeypatch);c=client(profile)
    monkeypatch.setattr(writer,'storage_preflight',lambda *a,**k:None)
    monkeypatch.setattr(writer,'journal_permission_preflight',lambda *a,**k:pytest.fail('invalid selected grants reached'))
    if change=='foreign_principal':c=client(profile,'backtest_v4_runner')
    elif change=='forged':c.fixed_structural_lot_profile=FixedStructuralLotDeclaredProfile(profile.operation)
    else:c.entry_spread_risk_profile=True
    with pytest.raises((ValueError,RuntimeError)):writer._v4_preflight(c)


def test_default_preflight_has_no_selected_tables(monkeypatch):
    calls=[]
    monkeypatch.setattr(writer,'storage_preflight',lambda c,*,tables:calls.append(tuple(tables)))
    monkeypatch.setattr(writer,'journal_permission_preflight',lambda *a,**k:None)
    assert writer._v4_preflight(client(None,'backtest_v4_runner')).fixed_structural_lot_profile is None
    assert calls==[writer.v4_storage_contracts()]


def test_selected_cold_reader_uses_same_issued_profile(monkeypatch):
    profile=issued(monkeypatch)
    _v4_cold_reader_preflight(client(profile))
    with pytest.raises(RuntimeError,match='unexpected principal'):
        _v4_cold_reader_preflight(client(profile,'backtest_v4_runner'))


def test_selected_credentials_require_file_before_private_loader(monkeypatch):
    profile=issued(monkeypatch)
    monkeypatch.delenv('BACKTEST_V4_FIXED_STRUCTURAL_LOT_RUNNER_CREDENTIAL_FILE',raising=False)
    monkeypatch.setattr(writer,'_dedicated_clickhouse_credentials',lambda *a:pytest.fail('private read attempted'))
    with pytest.raises(ValueError,match='credential FILE'):
        writer._v4_runner_credentials(fixed_structural_lot_profile=profile)
