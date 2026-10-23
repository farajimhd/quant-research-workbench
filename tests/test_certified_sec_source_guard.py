from unittest.mock import Mock
import pytest
from pipelines.certified_research.sec_source_guard import SourceGuard


def test_unchanged_physical_sources_reuse_audit_and_changed_sources_revalidate():
    audit=Mock();guard=SourceGuard(Mock(),audit,Mock())
    guard.fingerprint=Mock(side_effect=['a','a','a','b','b'])
    guard();guard();guard()
    assert audit.call_count==2 and guard.cache_hits==1 and guard.full_audits==2


def test_source_change_cannot_be_hidden_by_cached_validation():
    audit=Mock(side_effect=ValueError('content changed'));guard=SourceGuard(Mock(),audit,Mock())
    guard.signature='a';guard.fingerprint=Mock(return_value='b')
    with pytest.raises(ValueError,match='content changed'):guard()
    assert guard.signature=='a'


def test_unstable_parts_and_pending_mutations_fail_closed():
    guard=SourceGuard(Mock(),Mock(),Mock());guard.fingerprint=Mock(side_effect=['a','b','c'])
    with pytest.raises(ValueError,match='throughout'):guard()
    query=Mock(side_effect=[[dict(engine='MergeTree')]*4,[dict(mutation_id='pending')]])
    with pytest.raises(ValueError,match='mutation'):SourceGuard(query,Mock(),Mock())()
