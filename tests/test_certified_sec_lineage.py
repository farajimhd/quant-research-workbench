"""Partial SEC lifecycle runs must never become complete acquisition proof."""
from datetime import date
import json
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest

from pipelines.sec.edgar import sec_acquisition_contract as contract
from pipelines.sec.edgar import sec_historical_gap_fill as runner
from pipelines.certified_research import sec_lineage as lineage


def raw(name,*,stage=True,**changes):
    row=dict(coverage_id=name,source='sec',coverage_kind='sec_stage_'+name.replace('-','_') if stage else name,
        coverage_start_utc='2026-01-02 00:00:00.000',coverage_end_utc='2026-01-03 00:00:00.000',
        status='completed',row_count=0,file_count=0,error_count=0,run_id='fixture',host_role='fixture',
        metadata_json=json.dumps(dict(source='sec_historical_gap_fill',stage=name,command=['python','stage.py'])),
        started_at_utc='2026-01-03 20:00:00.000',updated_at_utc='2026-01-04 00:00:00.000',
        completed_at_utc='2026-01-03 21:00:00.000' if stage else '2026-01-04 00:00:00.000',proof_hash='a'*64)
    row.update(changes);return row


def stages():return [contract.stage(raw(name)) for name in sorted(contract.ALL)]


@pytest.mark.parametrize('failure',('missing','bootstrap','future','limited','gap','errors'))
def test_parent_claim_requires_complete_prior_unlimited_stage_coverage(failure):
    parent=raw('sec_integrity_audit',stage=False);rows=[raw(name) for name in sorted(contract.ALL)]
    if failure=='missing':rows.pop()
    elif failure=='bootstrap':rows[0]['status']='coverage_bootstrap'
    elif failure=='future':rows[0]['completed_at_utc']='2026-01-04 00:00:00.001'
    elif failure=='limited':rows[0]['metadata_json']=json.dumps(dict(source='sec_historical_gap_fill',stage=sorted(contract.ALL)[0],
        command=['python','stage.py','--limit-ciks','10']))
    elif failure=='gap':rows[0]['coverage_start_utc']='2026-01-02 00:00:00.001'
    else:rows[0]['error_count']=1
    accepted=[value for row in rows if (value:=contract.stage(row)) is not None]
    assert contract.witnesses(parent,accepted) is None


def test_full_stage_lineage_binds_parent_and_each_witness_and_rejects_mutation():
    parent=raw('sec_integrity_audit',stage=False);source=stages();witnesses=contract.witnesses(parent,source)
    certificate=lineage.binding(parent,witnesses)
    assert lineage.verify(certificate,parent,source)==certificate
    changed=[dict(s,hash='b'*64) if s['name']=='bulk-ingest' else s for s in source]
    with pytest.raises(ValueError,match='witness changed'):lineage.verify(certificate,parent,changed)


def test_partial_finalization_writes_no_broad_coverage_or_live_feed_claim():
    result=SimpleNamespace(stage='integrity-audit',status='ok',returncode=0,command=['python','audit.py'])
    with patch.object(runner,'ClickHouseHttpClient') as client:
        assert runner.write_coverage(SimpleNamespace(),'fixture',[result]) is False
    client.assert_not_called()
    full=[SimpleNamespace(stage=name,status='ok',returncode=0,command=['python','stage.py'])
        for name in sorted(contract.ALL-{'integrity-audit'})]+[result]
    assert set(contract.completed_families(full))==set(contract.REQUIRED)
    assert 'sec_live_feed' not in contract.completed_families(full)
    assert not contract.completed_families([result,*full[:-1]]) # Audit before acquisition.


def test_source_truncation_is_distinct_from_bounded_batching_and_audit_sampling():
    assert contract.limited(['python','stage.py','--limit-ciks=1'])
    assert not contract.limited(['python','stage.py','--limit-ciks','0','--batch-size','20','--sample-limit','100'])


def test_stop_never_reads_or_writes_acquisition_data():
    from threading import Event
    stop=Event();stop.set();query=Mock()
    with pytest.raises(InterruptedError):lineage.qualify(query,Mock(),date(2026,1,2),date(2026,1,2),stop=stop)
    query.assert_not_called()
