import pytest
from research.vectorized_backtest.v66.publication import write_json
from research.vectorized_backtest.v66.distributed_assets import sha
from research.vectorized_backtest.v66.identity import digest
from research.vectorized_backtest.v66.review_cohort_results import combine


def test_complete_union_and_child_differences(tmp_path):
    job=tmp_path/'job.json';root=tmp_path/'agent-reviews/service/epoch-0035';folder=root/'shard-0'
    write_json(job,dict(training_days=['2026-01-05','2026-02-02'],execution={'x':1}))
    plan=dict(scope='training',validation_used=False,job_sha256=sha(job),members=[{},{}],records=[dict(serial=1,parent_index=0,child_index=1,parent_sha256='parent',child_sha256='child',decision='control')],source_generation=36,review_epoch=35)
    write_json(root/'plan.json',plan)
    spec=dict(days=['2026-01-05','2026-02-02'],plan_sha256=sha(root/'plan.json'),job_sha256=sha(job),code_sha256='frozen')
    write_json(folder/'spec.json',spec)
    for day in spec['days']:
        metrics=dict(net_pnl=[2.,1.],closed_positions=[1,1],winning_positions=[1,1],ticker_pnl=[{'A':2},{'A':1}],execution_orders=[dict(orders=1,sha256='a'),dict(orders=1,sha256='b')])
        for key in ('episode_profits','episode_listings','episode_entry_clocks','episode_exit_clocks','episode_branch'):metrics[key]=[[1],[1]]
        receipt=folder/(day+'-receipt.json')
        write_json(receipt,dict(day=day,split='training',validation_opened=False,execution={'x':1},population_sha256=digest(plan['members']),listing_ids=['A','B'],metrics=metrics))
        write_json(folder/'days'/f'{day}.json',dict(spec_sha256=sha(folder/'spec.json'),parent_parity=1,receipt=str(receipt),receipt_sha256=sha(receipt),seconds=1,endpoints={}))
    write_json(folder/'state.json',dict(status='running',completed=spec['days'],parent_parity=2))
    with pytest.raises(ValueError,match='Incomplete'):combine(root,job)
    write_json(folder/'state.json',dict(status='complete',completed=spec['days'],parent_parity=2))
    result=combine(root,job)
    assert result['sessions']==2
    assert result['members'][0]['net']==4
    assert result['members'][0]['breadth']['all_listing_ids']==2
    assert len(result['comparisons']['1'])==2
    assert 'order_journal' in result['comparisons']['1'][0]['differences']
    from research.vectorized_backtest.v66.investigation_results import history
    carried=history(tmp_path)
    assert len(carried)==1
    assert [r['label'] for r in carried[0]['comparisons']]==['parent:1','control:1']
    assert carried[0]['comparisons'][1]['metrics']['net_pnl']==2
    write_json(folder/'state.json',dict(status='complete',completed=spec['days'][:1],parent_parity=1))
    with pytest.raises(ValueError,match='Incomplete'):combine(root,job)
