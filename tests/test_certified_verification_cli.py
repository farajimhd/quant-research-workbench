from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from scripts.prepare_certified_research import parse_args
from pipelines.certified_research import verification
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.execution import Unit


def test_verify_parser_requires_dataset_and_rejects_sql_format():
    with pytest.raises(SystemExit):parse_args(['verify'])
    args=parse_args(['verify','--dataset-id','00000000-0000-0000-0000-000000000001','--format','json'])
    assert args.command=='verify'
    with pytest.raises(SystemExit):parse_args(['verify','--dataset-id',str(args.dataset_id),'--format','sql'])
    with pytest.raises(SystemExit):parse_args(['verify','--dataset-id',str(args.dataset_id),'--acquire-missing'])


def test_source_guard_requires_exact_current_day_and_global_epoch():
    day=date(2026,1,2)
    saved=SimpleNamespace(units=(PlannedUnit(Unit('source','canonical_source'),dict(day=str(day),source_hash='a'*64)),),source_hash='b'*64)
    guard=verification.source_guard(Mock(),saved)
    with patch('pipelines.certified_research.source.inventory'),patch('pipelines.certified_research.source.reporting_inventory'),patch(
            'pipelines.certified_research.source.market_day_hashes',return_value={day:'a'*64}),patch(
            'pipelines.certified_research.source.require_ready',return_value='b'*64) as epoch:
        guard();epoch.return_value='c'*64
        with pytest.raises(ValueError,match='epoch changed'):guard()
    with pytest.raises(ValueError,match='explicit canonical'):verification.source_guard(Mock(),SimpleNamespace(units=()))


def test_saved_verification_never_admits_capacity_or_runs_producers():
    saved=SimpleNamespace(config=Mock(),dataset_id='dataset',units=(),source_hash='a'*64,plan_hash='b'*64)
    with patch.object(verification.campaign,'load_plan',return_value=saved),patch.object(verification,'source_guard'),patch.object(
            verification.runtime_plan,'runtime'),patch.object(verification,'ExecutionLaunch') as launch:
        launch.return_value.verify.return_value='c'*64
        query=Mock(return_value=[dict(plan_hash='b'*64,source_hash='a'*64,root_hash='c'*64,units=0)])
        result=verification.verify(query,'arte','dataset',Mock(),Mock(),Mock())
        assert result['status']=='verified' and result['read_only']
        launch.return_value.run.assert_not_called();launch.return_value.admit.assert_not_called()
        query.return_value=[]
        with pytest.raises(ValueError,match='Published campaign seal'):verification.verify(query,'arte','dataset',Mock(),Mock(),Mock())
