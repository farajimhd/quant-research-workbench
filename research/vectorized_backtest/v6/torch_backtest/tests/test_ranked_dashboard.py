"""Rank pagination must stay visible and bounded in fixed terminal regions."""
import pytest
from rich.console import Console
from research.vectorized_backtest.v6.torch_backtest.staged_dashboard import render,ranked_rows,navigate


def snapshot(count=100):
    return dict(top_strategies=[dict(rank=i,score=-i,metrics=dict(total_pnl=-i,positions=7))
                               for i in range(count,0,-1)],progress=None,evaluation_basis='3-day search ranking')


def test_sorted_top100_and_navigation():
    status=snapshot(110)
    assert [row['rank'] for row in ranked_rows(status)]==list(range(1,111))
    assert navigate(status,'n',height=38)==(9,1,0)
    assert navigate(status,'b',height=38)==(105,13,0)
    assert navigate(status,'k',height=38)==(110,13,0)
    assert navigate(status,'j',rank=110,rank_page=13,height=38)==(1,0,0)
    assert navigate(snapshot(0),'n',height=24)==(1,0,0)


@pytest.mark.parametrize('width,height',[(160,45),(110,38),(80,24)])
@pytest.mark.parametrize('view',['financial','objective','positions','performance'])
def test_fixed_screen_and_last_page(width,height,view):
    console=Console(width=width,height=height,color_system=None)
    with console.capture() as captured:
        console.print(render({**snapshot(),'_rank_page':999,'_rank':100},width=width,height=height,view=view))
    lines=captured.get().splitlines()
    assert len(lines)==height
    assert max(map(len,lines))<=width
    assert 'Q close' in lines[-1]
    assert any('100 *' in line for line in lines)
    assert any('3-day search ranking' in line for line in lines)
