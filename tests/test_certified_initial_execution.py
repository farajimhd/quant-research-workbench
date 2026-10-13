from datetime import date
from unittest.mock import Mock,patch
from uuid import uuid4

import pytest

from pipelines.certified_research import initial_plan,recipe,runtime_plan
from pipelines.certified_research.contracts import CampaignConfig


def test_initial_execution_checks_fresh_source_before_construction_and_preserves_resources():
    config=CampaignConfig(date(2026,1,2),date(2026,1,2),workers=2)
    rules=(dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1),)
    frozen=recipe.market_recipe(config,rules)
    plans=runtime_plan.units(config,frozen['definition'],rules)
    assembled=dict(plans=plans,recipe_hash=frozen['hash'],source_hash='a'*64)
    query=Mock();claim=Mock();clients=Mock();capacity=Mock();factory=Mock(return_value=capacity)
    guard=Mock();stop=Mock();progress=Mock()
    with patch('pipelines.certified_research.verification.source_guard',return_value=guard), \
            patch.object(runtime_plan,'runtime') as runtime, \
            patch('pipelines.certified_research.launch.ExecutionLaunch') as launch:
        initial_plan.run(query,config,str(uuid4()),assembled,claim,clients,Mock(),factory,stop=stop,progress=progress)
        factory.assert_called_once_with(config,plans)
        runtime.call_args.args[2]()
        clients.assert_called_once_with(config)
        launch.return_value.run.assert_called_once_with(stop=stop,progress=progress)
        guard.assert_called_once_with();query.assert_not_called()
        guard.side_effect=ValueError('source changed')
        runtime.reset_mock();factory.reset_mock()
        with pytest.raises(ValueError,match='source changed'):
            initial_plan.run(query,config,str(uuid4()),assembled,claim,clients,Mock(),factory)
        runtime.assert_not_called();factory.assert_not_called()


def test_initial_factory_cannot_mutate_the_frozen_plan():
    config=CampaignConfig(date(2026,1,2),date(2026,1,2))
    rules=(dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1),)
    frozen=recipe.market_recipe(config,rules);plans=runtime_plan.units(config,frozen['definition'],rules)
    assembled=dict(plans=plans,recipe_hash=frozen['hash'],source_hash='a'*64)
    def mutate(*args):
        plans[0].arguments['rows'][0]['update_volume']=0
        return Mock()
    with patch('pipelines.certified_research.verification.source_guard',return_value=Mock()), \
            patch.object(runtime_plan,'runtime',side_effect=mutate), \
            patch('pipelines.certified_research.launch.ExecutionLaunch') as launch:
        with pytest.raises(ValueError,match='changed during'):
            initial_plan.run(Mock(),config,str(uuid4()),assembled,Mock(),Mock(),Mock(),Mock(return_value=Mock()))
        launch.assert_not_called()
