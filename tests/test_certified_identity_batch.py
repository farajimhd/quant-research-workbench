from unittest.mock import Mock,patch
import pytest
from pipelines.certified_research import historical_identity as h

REQUEST=dict(as_of='2026-01-02',ticker='TEST',composite_figi='BBG000000001')


def run(requests,**options):
    return h.prepare_observations('certified_research_test_identity',Mock(),Mock(),Mock(),Mock(),Mock(),requests,**options)


def test_verified_points_reuse_without_acquisition():
    with patch.object(h.AcquisitionHandlers,'verifier',return_value=dict(output_hash='a'*64)),\
         patch.object(h.AcquisitionHandlers,'producer') as producer:
        result=run([REQUEST])
    assert result['status']=='complete' and result['units']['identity:0']=='reused'
    producer.assert_not_called()


def test_missing_points_block_without_opt_in():
    with patch.object(h.AcquisitionHandlers,'verifier',return_value=None),\
         patch.object(h.AcquisitionHandlers,'producer') as producer:
        result=run([REQUEST])
    assert result['status']=='failed' and 'not enabled' in str(result['errors']['identity:0'])
    producer.assert_not_called()


def test_acquired_points_require_independent_readback():
    with patch.object(h.AcquisitionHandlers,'verifier',side_effect=[None,dict(output_hash='a'*64)]),\
         patch.object(h.AcquisitionHandlers,'producer') as producer:
        result=run([REQUEST],acquire_missing=True)
    assert result['status']=='complete';producer.assert_called_once()


def test_batch_bound_and_duplicate_points_fail_before_execution():
    with pytest.raises(ValueError,match='Duplicate'):run([REQUEST,REQUEST])
    with pytest.raises(ValueError,match='1000'):run((REQUEST for _ in range(1001)))
