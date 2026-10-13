"""Explicit checkpoint roots cannot escape frozen source admission."""
from datetime import date
from types import SimpleNamespace
from threading import Event
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research.handlers import V7SeedHandlers
from pipelines.certified_research.execution import Unit
from pipelines.certified_research.campaign import PlannedUnit


def test_v7_seed_requires_frozen_source_and_closes_reader_on_invalid_state():
    day=date(2026,1,2);parent=dict(session_date=str(day))
    plan=PlannedUnit(Unit('seed','v7_seed'),dict(dependency=parent))
    client=SimpleNamespace(query=Mock(),close=Mock());clients=Mock(return_value=client)
    runtime=SimpleNamespace(sources={},recipe_hash='a'*64,rules=())
    handler=V7SeedHandlers('arte',SimpleNamespace(check=Mock()),clients,Mock(),runtime)
    with pytest.raises(ValueError,match='outside frozen'):handler.verifier(plan,{})
    clients.assert_not_called()
    runtime.sources[day]='b'*64
    with patch('pipelines.certified_research.v7.verify_seed',side_effect=ValueError('Invalid checkpoint')):
        with pytest.raises(ValueError,match='Invalid checkpoint'):handler.verifier(plan,{})
    client.close.assert_called_once()
    stop=Event();stop.set()
    with pytest.raises(InterruptedError):handler.producer(plan,{},stop)
    assert clients.call_count==1


def test_v7_discovery_requires_independent_adjustment_context_and_bounds_candidates():
    from pipelines.certified_research.v7 import discover
    day=date(2026,1,2);claim=SimpleNamespace(check=Mock());query=Mock(return_value=[{},{}])
    context={day:dict(source_hash='b'*64,ticker='TEST',adjustment_hash='')}
    with pytest.raises(ValueError,match='independently frozen'):
        discover(query,'arte',claim,1,date(2026,1,5),'a'*64,({'rule':1},),context)
    query.assert_not_called()
    context[day]['adjustment_hash']='c'*64
    with patch('pipelines.certified_research.publication.Publisher'):
        with pytest.raises(ValueError,match='candidate bound'):
            discover(query,'arte',claim,1,date(2026,1,5),'a'*64,({'rule':1},),context,maximum_candidates=1)
    assert query.call_count==1 and 'LIMIT 2' in query.call_args.args[0]
