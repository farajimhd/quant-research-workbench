from datetime import date
from unittest.mock import patch

import pytest

from pipelines.certified_research import sec_coverage as sec


def rows():
    return [dict(coverage_id=k,coverage_kind=k,coverage_start_utc='2025-01-01 00:00:00.000',
        coverage_end_utc='2026-01-03 00:00:00.000',status='completed',error_count=0,
        completed_at_utc='2026-01-03 01:00:00.000',proof_hash='a'*64) for k in sec.KINDS]


def test_completed_ranges_require_every_acquisition_and_integrity_family():
    start=end=date(2026,1,2)
    assert sec.evaluate(rows(),start,end)['ready']
    assert not sec.evaluate(rows()[:-1],start,end)['ready']
    for change in (dict(status='running'),dict(status='coverage_bootstrap'),dict(error_count=1),
                   dict(completed_at_utc=None)):
        source=rows();source[0].update(change)
        result=sec.evaluate(source,start,end)
        assert not result['ready'] and result['rejected_intervals']==1


def test_half_open_union_accepts_adjacent_ranges_but_preserves_subsecond_gaps():
    source=rows();first=source.pop(0)
    source.extend((dict(first,coverage_end_utc='2026-01-02 12:00:00.000'),
        dict(first,coverage_id='second',coverage_start_utc='2026-01-02 12:00:00.000')))
    assert sec.evaluate(source,date(2026,1,2),date(2026,1,2))['ready']
    source[-1]['coverage_start_utc']='2026-01-02 12:00:00.001'
    result=sec.evaluate(source,date(2026,1,2),date(2026,1,2))
    assert result['gaps'][sec.KINDS[0]]==(('2026-01-02T12:00:00+00:00','2026-01-02T12:00:00.001000+00:00'),)
    with pytest.raises(ValueError,match='duplicated'):sec.evaluate(source+[source[-1]],date(2026,1,2),date(2026,1,2))


def test_manifest_absence_and_overflow_fail_without_scanning_sec_facts():
    logs=[]
    def missing(sql):logs.append(sql);return [dict(n=0)]
    result=sec.inventory(missing,date(2026,1,2),date(2026,1,2))
    assert not result['ready'] and len(logs)==1
    with patch.object(sec.schema,'verify_auxiliary_storage'):
        def query(sql):
            logs.append(sql)
            return [dict(n=1)] if 'system.tables' in sql else rows()
        with pytest.raises(ValueError,match='exceeds'):sec.inventory(query,date(2026,1,2),date(2026,1,2),maximum_intervals=1)
    assert all('sec_xbrl_company_fact' not in sql for sql in logs)
