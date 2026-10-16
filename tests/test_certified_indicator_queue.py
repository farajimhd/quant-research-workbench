from copy import deepcopy
from datetime import date
import pytest
from scripts.run_certified_indicator_queue import choose_observations,minimum_frames,frozen_queue,identity_for_day,FRAMES


def row(frame,index,day='2025-12-31'):
    return dict(session_date=day,resolution_ms=frame,bucket_index=index,low_int=100,close_int=105,high_int=110)


def test_seed_uses_exact_last_50_independently_per_frame():
    rows=[row(frame,i) for frame in FRAMES for i in range(60)]
    selected,missing=choose_observations(reversed(rows))
    assert missing=={}
    assert all([r['bucket_index'] for r in values]==list(range(10,60)) for values in selected.values())


def test_sparse_frames_report_precise_missing_observations():
    selected,missing=choose_observations([row(3600000,i) for i in range(16)])
    assert missing[3600000]==34
    assert missing[100]==50
    assert len(selected[3600000])==16


@pytest.mark.parametrize('rows',[
    [row(100,0),row(100,0)], [row(100,0,'2026-01-02')],
    [dict(row(100,0),close_int=111)]])
def test_duplicate_january_or_invalid_observations_fail_closed(rows):
    with pytest.raises(ValueError,match='December seed'):choose_observations(rows)


def test_only_missing_frames_are_extracted_before_final_december_session():
    assert minimum_frames(date(2025,12,31))==FRAMES
    assert minimum_frames(date(2025,12,30),{3600000:34})==(3600000,)
    assert minimum_frames(date(2025,12,30),{100:7,3600000:34})==(100,3600000)


def test_frozen_source_queue_ignores_progress_but_not_population_changes():
    q=dict(request={'version':'v1'},partition={'groups':[[1]]},full_source_hash='a',source_epoch='b',
        cohorts=[dict(dataset_id='d',issuer_keys=[1],status='queued')])
    other=deepcopy(q);other['cohorts'][0]['status']='complete'
    assert frozen_queue(q)==frozen_queue(other)
    other['cohorts'][0]['issuer_keys']=[2]
    assert frozen_queue(q)!=frozen_queue(other)


def test_no_current_ticker_substitution_across_lifecycle_boundary():
    mapping=[dict(listing_key=1,security_key=2,ticker='OLD',start='2025-12-01',end_exclusive='2025-12-30'),
             dict(listing_key=1,security_key=2,ticker='NEW',start='2025-12-30',end_exclusive='2026-03-01')]
    assert identity_for_day(mapping,1,date(2025,12,29))['ticker']=='OLD'
    assert identity_for_day(mapping,1,date(2025,12,30))['ticker']=='NEW'
    assert identity_for_day(mapping,1,date(2025,11,28)) is None
