"""Reference authority handoffs cannot silently import or reinterpret history."""
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4

import pytest

from pipelines.certified_research import facts
from pipelines.certified_research.handlers import ReferenceAuthorityHandlers
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.execution import Unit


def inputs():
    dataset=str(uuid4())
    row=dict(dataset_id=dataset,entity_kind=2,entity_key=11,family='corporate_actions',
        range_start='2025-01-01',range_end='2026-10-02',parent_hash='a'*64,catalog_hash=facts.catalog_hash(),
        output_hash='b'*64,attempt_id=str(uuid4()))
    plan=PlannedUnit(Unit('corporate','corporate_actions'),dict(entity_key=11,start='2025-01-01',end='2026-10-02',receipt=row))
    client=SimpleNamespace(query=Mock(),close=Mock());clients=Mock(return_value=client)
    handler=ReferenceAuthorityHandlers(dataset,'arte',SimpleNamespace(check=Mock()),clients,Mock())
    return handler,plan,row,client,clients


def test_current_dataset_scope_is_reused_without_writes_and_missing_authority_blocks():
    handler,plan,row,client,clients=inputs()
    with patch('pipelines.certified_research.schema.verify_storage'),patch(
            'pipelines.certified_research.handlers.ReferencePublisher') as publisher:
        publisher.return_value.receipt.return_value=row
        assert handler.verifier(plan,{})==row
        publisher.return_value.receipt.return_value=None
        with pytest.raises(ValueError,match='independently verified'):handler.verifier(plan,{})
    assert client.close.call_count==2
    client.query.assert_not_called()


@pytest.mark.parametrize('field,value',(('dataset_id',str(uuid4())),('entity_key',12),('catalog_hash','f'*64),('range_start','2025-01-02')))
def test_scope_mismatch_rejects_before_opening_client(field,value):
    handler,plan,row,client,clients=inputs();row[field]=value
    with pytest.raises(ValueError,match='identity/range/catalog'):handler.verifier(plan,{})
    clients.assert_not_called()
