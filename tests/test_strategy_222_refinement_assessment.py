from copy import deepcopy
from scripts.assess_strategy_222_refinement import assess_cases


def test_assessment_deduplicates_moves_and_does_not_hide_late_losing_entry():
    base=dict(name='corrected-baseline',run_id='baseline',status='entered_before_peak',
              fraction_of_price_move_before_entry=.6,episode_net=1000)
    candidate=dict(name='candidate',run_id='candidate',status='entered_before_peak',
                   fraction_of_price_move_before_entry=.7,episode_net=-50)
    first=dict(position=1,symbol='X',major_move=True,opportunity_id='X:1',variants=[base,candidate])
    second=deepcopy(first);second['position']=2
    third=dict(position=3,symbol='Y',major_move=False,variants=[base])
    results=assess_cases([first,second,third],[dict(run_id='baseline',end='10:00:00'),dict(run_id='candidate',end='10:00:00')])
    result=next(r for r in results if r['variant']=='candidate')
    assert result['coverage']==2 and not result['all_positions_covered']
    assert result['major_opportunities_covered']==1
    assert result['flagged_major_opportunities']==1
    move=result['major_opportunities'][0]
    assert move['positions']==[1,2]
    assert 'first_overlapping_entry_did_not_profit' in move['issues']
    assert move['large_winner_retention_ratio']==-.05


def test_portfolio_and_isolated_results_are_never_mixed():
    variants=[dict(name='candidate',run_id=run,status='entered_before_peak',
                   fraction_of_price_move_before_entry=.2,episode_net=net)
              for run,net in [('isolated',1000),('portfolio',-20)]]
    cases=[dict(position=1,symbol='X',major_move=True,opportunity_id='X:1',variants=variants)]
    trials=[dict(run_id='isolated',symbol='X',end='10:00:00'),dict(run_id='portfolio',symbol='PORTFOLIO',end='10:00:00')]
    result=assess_cases(cases,trials)
    assert len(result)==2
    assert next(r for r in result if r['scope']=='isolated')['flagged_major_opportunities']==0
    assert next(r for r in result if r['scope']=='portfolio')['flagged_major_opportunities']==1
