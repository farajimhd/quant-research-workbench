"""Runtime resumes from frozen settings/rules, not current defaults."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from pipelines.certified_research import recipe,runtime_plan
from pipelines.certified_research.contracts import CampaignConfig
from pipelines.certified_research.campaign import PlannedUnit,StoredPlan
from pipelines.certified_research.execution import Unit
from src.market_engine.historical_session_levels import Settings


def frozen():
    config=CampaignConfig(date(2026,1,2),date(2026,1,5))
    rules=tuple(dict(token_id=i,modifier_int=0,update_last=1,update_high_low=1,update_volume=1) for i in range(80))
    definition=recipe.market_recipe(config,rules,settings=Settings(tick=.02),coverage=.75)['definition']
    return config,rules,definition,runtime_plan.units(config,definition,rules)


def test_descriptor_chunks_obey_existing_row_bound_and_preserve_custom_parameters():
    config,rules,definition,plans=frozen()
    assert len(plans)==4
    assert all(len(p.row(str(uuid4()))['arguments'].encode())<=8192 for p in plans)
    resolved=runtime_plan.resolve(config,plans)
    assert resolved['definition']==definition and resolved['rules']==rules
    assert resolved['definition']['v7_parameters']['coverage']==.75
    with pytest.raises(ValueError,match='missing or incompatible'):runtime_plan.resolve(config,plans[1:])
    with pytest.raises(ValueError,match='contract differs'):
        runtime_plan.resolve(replace(config,volume_windows_seconds=(10,120)),plans)


def test_saved_runtime_uses_frozen_canonical_day_bindings_without_opening_clients():
    config,rules,definition,plans=frozen()
    plans+=(PlannedUnit(Unit('source:2026-01-02','canonical_source'),dict(day='2026-01-02',source_hash='b'*64)),)
    saved=StoredPlan(str(uuid4()),config,plans,'c'*64,'d'*64)
    clients=Mock();claim=SimpleNamespace(check=Mock());guard=Mock()
    result=runtime_plan.runtime(saved,claim,clients,guard)
    assert result.definition==definition and result.rules==rules
    assert result.sources=={date(2026,1,2):'b'*64}
    clients.assert_not_called()
    with pytest.raises(ValueError,match='descriptor'):runtime_plan.resolve(config,())
