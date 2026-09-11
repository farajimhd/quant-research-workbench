from scripts.audit_structural_baseline import outcome
from scripts.evaluate_structural_timing import adaptive_exit, analyze, timing_metrics
from scripts.summarize_structural_timing import move_coverage


def q(at,bid=10):
    return dict(at=at,bid=bid,ask=bid+.01)


def row(at,close=10,state=None):
    level=dict(lower=9.5,upper=9.6,side=-1,unified_level_id='x',confirmed_at_ms=90000)
    return dict(at=at,bar=dict(time=at-1,end=at,low=close-.02,high=close+.02,close=close),
                events=[dict(state=state,level=level)] if state else [],
                levels=[level,dict(lower=11,upper=11.1,side=-1)],sequence=[],direction='up',progression={},
                shares=200000,dollars=2000000,rate10=10,rate60=10)


def sample(quotes):
    s=dict(at=100,stop=9.4,target=11,close=10,level=dict(upper=9.6))
    s['outcome']=outcome(s,quotes,[q['at'] for q in quotes])
    return s


def test_stall_requires_weakness_and_execution_latency():
    quotes=[q(100),q(100.1),q(104,10.1),q(111,10),q(111.05,10),q(111.1,9.99),q(130,9.9)]
    rows=[row(108,10.1),row(109,10.05),row(110,10)]
    s=sample(quotes)
    r=adaptive_exit(s,rows,quotes)
    assert r['reason']=='stalled_failure'
    assert r['exit_at']==111.1
    assert r['entry']==s['outcome']['entry']
    assert r['trace'][0]['at']==111


def test_retrace_without_weakness_does_not_exit():
    quotes=[q(100),q(100.1),q(104,10.1),q(111,10),q(130,9.9)]
    s=sample(quotes)
    r=adaptive_exit(s,[],quotes)
    assert r['exit_at']==s['outcome']['exit_at']
    assert r['trace']==[]


def test_stop_wins_over_pending_exit():
    quotes=[q(100),q(100.1),q(104,10.1),q(111,10),q(111.1,9.3),q(130,9.3)]
    r=adaptive_exit(sample(quotes),[row(108,10.1),row(109,10.05),row(110,10)],quotes)
    assert r['reason']=='stop'


def test_future_rows_do_not_change_earlier_exit():
    quotes=[q(100),q(100.1),q(104,10.1),q(111,10),q(111.1,9.99),q(130,9.9)]
    rows=[row(108,10.1),row(109,10.05),row(110,10)]
    first=adaptive_exit(sample(quotes),rows,quotes)
    later=adaptive_exit(sample(quotes),rows+[row(112,11),row(113,12)],quotes)
    assert first==later


def test_gapped_or_stale_falling_candles_are_not_fresh_weakness():
    quotes=[q(100),q(100.1),q(104,10.1),q(111,10),q(130,9.9)]
    for rows in ([row(106,10.1),row(108,10.05),row(110,10)],
                 [row(105,10.1),row(106,10.05),row(107,10)]):
        assert adaptive_exit(sample(quotes),rows,quotes)['trace']==[]


def test_post_exit_missing_coverage_is_not_zero_continuation():
    quotes=[q(100),q(100.1),q(130,10)]
    metrics=timing_metrics(sample(quotes),quotes)
    assert metrics['post_exit_observed'] is False
    assert metrics['post_exit_max_extension_bps'] is None


def test_cohort_matches_baseline_entry_fills():
    data=dict(rows=[row(100,10,'breakout_accepted'),row(110,10,'breakout_accepted')],
              quotes=[q(100),q(100.1),q(110),q(110.1),q(130),q(140)])
    result=analyze(data)
    assert result['summary']['paired']['count']==result['summary']['entries']['breakout_accepted']['trades']==1
    for pair in result['paired_exits']:
        assert pair['baseline']['entry_at']==pair['experimental']['entry_at']
        assert pair['baseline']['entry']==pair['experimental']['entry']


def test_fixed_tiles_do_not_select_future_lows():
    tiles=move_coverage([q(100,10),q(105,9),q(110,9.5),q(130,9.5)],[],100,130)
    assert tiles[0]['status']=='observed'
    assert tiles[0]['move_100bps'] is False


def test_move_coverage_requires_signal_before_threshold():
    quotes=[q(100,10),q(101,10.2),q(130,10.2)]
    signal=dict(at=100.5,family='breakout',blocked=[],outcome=dict(status='resolved',entry_at=101.1))
    tile=move_coverage(quotes,[signal],100,130)[0]
    assert tile['move_100bps']
    assert tile['signal_before_hit']['breakout'] is False


def test_missing_tile_endpoint_is_explicit():
    assert move_coverage([q(100),q(101,11)],[],100,130)[0]['status']=='unobserved'
