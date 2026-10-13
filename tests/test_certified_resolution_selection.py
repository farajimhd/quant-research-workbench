from datetime import date
from uuid import uuid4

import pytest

from pipelines.certified_research import market,recipe,producer,indicator_state,indicators
from pipelines.certified_research.contracts import CampaignConfig


def test_selection_changes_recipe_and_uses_only_required_rollup_intermediates():
    rules=(dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1),)
    config=CampaignConfig(date(2026,1,2),date(2026,1,2),resolutions_ms=(1000,60000))
    frozen=recipe.market_recipe(config,rules)
    assert frozen['definition']['frames']==[1000,60000]
    assert recipe.verify_definition(frozen['definition'],rules)==frozen['hash']
    unit=market.MarketUnit(str(uuid4()),config.start,'TEST',1,str(uuid4()))
    sql=market.bar_queries(unit,frames=config.resolutions_ms)
    assert len(sql)==3
    assert 'ARRAY JOIN [1000, 30000]' in sql[1] and 'ARRAY JOIN [60000]' in sql[2]
    projection=market.normalized_projection(unit,'bars',frames=config.resolutions_ms)
    assert 'resolution_ms IN (1000,60000)' in projection
    assert 'resolution_ms IN' not in market.normalized_projection(unit,'broker_100ms',frames=config.resolutions_ms)
    assert len(market.bar_queries(unit,frames=(1000,)))==2
    for frames in ((),(60000,1000),(1000,1000),(123,),(True,)):
        with pytest.raises(ValueError):market.selected_frames(frames)


def test_selected_state_rejects_extra_frames_and_preserves_exact_predecessor_values():
    frames=(1000,60000)
    row={name:0 if name=='sample_count' else 0. for name in indicators.STATE_FIELDS}
    state={frame:dict(row) for frame in frames}
    indicator_state.validate(state,frames)
    with pytest.raises(ValueError,match='declared frame'):indicator_state.validate(dict(state,extra=row),frames)
