from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import saved_execution
from pipelines.certified_research.contracts import CampaignConfig


@pytest.mark.parametrize('observe',(False,True))
def test_saved_runner_uses_frozen_resources_and_passes_restart_controls(observe):
    config=CampaignConfig(date(2026,1,2),date(2026,1,5),workers=2)
    saved=SimpleNamespace(config=config,units=(),dataset_id='dataset',source_hash='a'*64,plan_hash='b'*64)
    claim=Mock();clients=Mock();capacity=Mock();factory=Mock(return_value=capacity)
    guard=Mock();stop=Mock();progress=Mock()
    observer=Mock() if observe else None
    with patch.object(saved_execution.campaign,'load_plan',return_value=saved), \
            patch.object(saved_execution.verification,'source_guard',return_value=guard), \
            patch.object(saved_execution.runtime_plan,'runtime') as runtime, \
            patch.object(saved_execution,'ExecutionLaunch') as launch:
        launch.return_value.controller.plan_hash=saved.plan_hash
        launch.return_value.run.return_value={'status':'complete'}
        assert saved_execution.run(Mock(),'arte','dataset',claim,clients,Mock(),factory,
            stop=stop,progress=progress,storage_observer=observer)=={'status':'complete'}
        assert launch.call_args.kwargs['storage_observer'] is observer
        factory.assert_called_once_with(config,())
        runtime.call_args.args[2]()
        clients.assert_called_once_with(config)
        guard.assert_called_once_with()
        launch.return_value.run.assert_called_once_with(stop=stop,progress=progress)


def test_missing_capacity_or_changed_source_prevents_runtime_construction():
    with pytest.raises(ValueError,match='qualified capacity'):
        saved_execution.run(Mock(),'arte','dataset',Mock(),Mock(),Mock(),None)
    saved=Mock()
    with patch.object(saved_execution.campaign,'load_plan',return_value=saved), \
            patch.object(saved_execution.verification,'source_guard',return_value=Mock(side_effect=ValueError('source changed'))), \
            patch.object(saved_execution.runtime_plan,'runtime') as runtime:
        with pytest.raises(ValueError,match='source changed'):
            saved_execution.run(Mock(),'arte','dataset',Mock(),Mock(),Mock(),Mock())
        runtime.assert_not_called()
