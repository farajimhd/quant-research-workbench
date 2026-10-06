"""Real typed66 read/selector; only external certified42 acquisition is synthetic."""
from copy import deepcopy
import pytest
from test_strategy_sixty_six_release import source_fixture, APPROVAL
from test_strategy_two_configuration import Reader
from src.backend import backtest_strategy_one_configuration as module
from src.trading_runtime.strategy_sixty_six_release import derive_strategy_sixty_six_configuration


def prepared(monkeypatch):
    parent=source_fixture()
    envelope=derive_strategy_sixty_six_configuration(parent,**APPROVAL)
    client=Reader()
    client.payloads[66]=deepcopy(envelope['payload'])
    client.sources[66]=(envelope['source_candidate_id'],envelope['source_candidate_hash'])
    actual=module.certify_numbered_configuration
    def certified(reader,number=1):
        assert reader is client
        return parent if number==42 else actual(reader,number)
    monkeypatch.setattr(module,'certify_numbered_configuration',certified)
    result=actual(client,66)
    assert result.strategy_number==66 and result.payload==envelope['payload']
    assert result.payload_hash==envelope['payload_hash'] and result.node_hash==envelope['node_hash']
    return client,result


def test_actual_selected66_revision_returns_exact_certified_typed_release(monkeypatch):
    client,result=prepared(monkeypatch)
    revision=result.revision()
    assert module.selected_numbered_revision(client=client,revision_id=revision['revision_id'],
        run_plan_id=revision['run_plan_id'])==revision
    assert all(query.startswith('SELECT ') for query in client.queries)


@pytest.mark.parametrize('foreign',['uuid','run_plan'])
def test_actual_selected66_revision_rejects_foreign_identity_after_certificate(monkeypatch,foreign):
    client,result=prepared(monkeypatch)
    revision=result.revision()
    revision_id=revision['revision_id']
    run_plan_id=revision['run_plan_id']
    if foreign=='uuid':revision_id='strategy-one-66:00000000-0000-0000-0000-000000000001'
    else:run_plan_id='foreign-plan'
    count=len(client.queries)
    with pytest.raises(ValueError,match='differs from its immutable release'):
        module.selected_numbered_revision(client=client,revision_id=revision_id,run_plan_id=run_plan_id)
    assert len(client.queries)==count+2
