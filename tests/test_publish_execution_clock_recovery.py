import json

import pytest

from pipelines.market_sip.publish_execution_clock_recovery import checked_stage,clock_rows,coverage_row,placement
from pipelines.market_sip.execution_clock_recovery import digest,VERSION
from pipelines.market_sip.activate_execution_clock_recovery import exchange_needed


def test_clock_rows_preserve_exact_times():
    result=clock_rows('JUNS','2026-08-24',[dict(ordinal=4,sip_timestamp_us=2000000,execution_timestamp_us=1999999)])
    assert result[0]['execution_timestamp_us']==1999999
    assert result[0]['source_date']=='2026-08-24'
    assert result[0]['ordinal']==4


def test_certificate_counts_delayed_seconds_and_full_event_bounds():
    rows=[dict(sip_timestamp_us=2000000,execution_timestamp_us=1999999),
          dict(sip_timestamp_us=2000999,execution_timestamp_us=2000001)]
    r=coverage_row('JUNS','2026-08-24',dict(bounds=dict(events=20,begin=4,end=24)),rows)
    assert r['event_count']==20 and r['clock_count']==2
    assert r['delayed_trade_report_count']==1
    assert r['first_ordinal']==4 and r['next_ordinal']==24
    assert r['source_filter_key'].startswith('rest_clock_recovery_v2|')
    assert r['source_filter_key'].endswith('|delayed_audit_v1')


@pytest.mark.parametrize('policy,disk',[('default','default'),('live_market_ssd','default'),('sip_raw_ssd','sip_raw_ssd')])
def test_wrong_policy_or_physical_disk_fails(policy,disk):
    class Client:
        def json_rows(self,sql,**kwargs):
            return [{'storage_policy':policy}] if 'system.tables' in sql else [{'disk_name':disk}]
    with pytest.raises(ValueError):placement(Client(),'test')


def test_stage_tampering_fails_before_publication(tmp_path):
    data={'plan.json':dict(version=VERSION),
          'validation.json':dict(complete=True,canonical_sha256=digest({}),matched_sha256=digest([])),
          'canonical-snapshot.json':{},'matched-staging.json':[dict(ordinal=99)]}
    for name,value in data.items():(tmp_path/name).write_text(json.dumps(value))
    with pytest.raises(ValueError,match='hash mismatch'):checked_stage(tmp_path)


def test_resume_after_exchange_does_not_exchange_back():
    assert exchange_needed('new','old','new') is False
    assert exchange_needed('old','old','new') is True
    with pytest.raises(ValueError,match='UUID'):
        exchange_needed('foreign','old','new')
