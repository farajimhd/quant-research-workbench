import numpy as np
import pytest
from types import SimpleNamespace
from research.vectorized_backtest.v65.calibration import activation_threshold,entry_boundary
from research.vectorized_backtest.v65.price_program import Prices,Expression,entry_guard


def test_discrete_quantile_cannot_silently_change_activation_target():
    values=np.array([.7260595]*20+[.82288384]*80,np.float32)
    with pytest.raises(ValueError,match='tolerance'):activation_threshold(values,.29)
    threshold,rate=activation_threshold(values,.8)
    assert values.min()<threshold<values.max()
    assert rate==.8


@pytest.mark.parametrize('target',[.001,.01,.15,.5,.9])
def test_continuous_activation_preserves_requested_rate(target):
    values=np.linspace(-.9,.9,10000,dtype=np.float32)
    _,rate=activation_threshold(values,target)
    assert abs(rate-target)<=.0001


def test_constant_output_is_replaced_instead_of_accepted():
    with pytest.raises(ValueError):activation_threshold(np.ones(100),.2)


def test_management_calibration_uses_its_own_market_price_boundary():
    from research.vectorized_backtest.v65.calibration import action_boundaries
    member=SimpleNamespace(policy=SimpleNamespace(price_program=Prices(update=Expression('rolling.low.5')).payload()))
    ask=np.array([10.,250.,10.,10.])
    probe=dict(eligible=np.ones(4,bool),quote_valid=np.ones(4,bool),ask=ask,ask_size=np.ones(4),
               price_references={'ask':ask,'bid':ask-.01,'close':ask,'rolling.low.5':np.array([9.,200.,11.,np.nan])})
    bounds=action_boundaries(member,probe)
    assert bounds['add'].tolist()==[True,False,True,True]
    assert bounds['trail'].tolist()==[True,True,False,False]


def test_joint_boundary_requires_actual_prices_and_whole_share_opportunity():
    member=SimpleNamespace(policy=SimpleNamespace(price_program=Prices().payload()))
    probe=dict(eligible=np.ones(4,bool),quote_valid=np.ones(4,bool),ask=np.array([10.,10.,2000.,10.]),ask_size=np.array([1.,1.,10.,.5]))
    with pytest.raises(ValueError):entry_boundary(member,probe)
    probe['price_references']={'ask':probe['ask'],'bid':probe['ask']-.01,'close':probe['ask']}
    assert entry_boundary(member,probe).tolist()==[True,True,False,False]
    member.policy.price_program=Prices(stop=Expression('rolling.low.5')).payload()
    probe['price_references']['rolling.low.5']=np.array([9.,11.,100.,np.nan])
    assert entry_boundary(member,probe).tolist()==[True,False,False,False]


def test_selected_references_match_full_causal_projection():
    from research.vectorized_backtest.v65.price_references import References
    from types import SimpleNamespace
    slots=np.tile(np.arange(5),(100,1))
    source=np.arange(500,dtype=float).reshape(100,5)+10
    data=SimpleNamespace(arrays={'clocks':np.arange(100)},v65_price_reference_cache={'_raw_low':source,'_raw_high':source})
    refs=References(data,slots);ticks=np.array([0,5,20,70,99])
    for kind in ('candle','previous','rolling'):
        for side in ('low','high'):
            for seconds in (5,20,60):
                name=f'{kind}.{side}.{seconds}'
                np.testing.assert_array_equal(refs.selected(name,ticks),refs.values(name)[ticks])


def test_block_ranking_retains_opposite_split_values():
    from research.vectorized_backtest.v65.campaign_progress import blocked_ranking_table
    from rich.console import Console
    import io
    output=io.StringIO();console=Console(file=output,width=200,color_system=None)
    rows=[dict(serial=13,score=100.,net_pnl=120.,opposite_score=42.,opposite_net=55.,opposite_coverage=12)]
    console.print(blocked_ranking_table(rows,0,0,10,196,'Val'))
    text=output.getvalue()
    assert 'Val score $' in text and 'Val net $' in text and '42.00' in text and '55.00' in text
