from uuid import uuid4

from scripts.prepare_indicator_warmup import prepare


def test_missing_real_dependencies_reported_without_keeper_or_bar_scan():
    calls = []

    def query(sql):
        calls.append(sql)
        return [{'security_key': 2}] if 'research_membership_v1' in sql else []

    def forbidden_claim():
        raise AssertionError('No Keeper claim needed for blocked metadata preflight')

    result = prepare(query, 'arte', str(uuid4()), 1, forbidden_claim)
    assert result['status'] == 'blocked'
    assert result['execution_started'] is False
    assert result['blocking_dependencies'] == [
        'certified_december_bar_receipts_missing',
        'unambiguous_certified_corporate_adjustment_coverage_missing']
    assert len(calls) == 3
    assert not any('FROM arte.research_bars_v1' in sql for sql in calls)


def test_ambiguous_identity_blocks_without_invented_security():
    def query(sql):
        return [{'security_key': 2}, {'security_key': 3}] if 'membership' in sql else []

    result = prepare(query, 'arte', str(uuid4()), 1, lambda: None)
    assert result['status'] == 'blocked'
    assert 'unambiguous_certified_listing_membership_missing' in result['blocking_dependencies']
