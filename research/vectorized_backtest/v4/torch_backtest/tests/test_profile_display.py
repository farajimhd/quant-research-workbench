from io import StringIO
from rich.console import Console
from research.vectorized_backtest.v4.torch_backtest.dashboard import render


def test_profile_has_one_session_and_no_campaign_generation_bar():
    status=dict(mode='profile',status='profiling',stage='Backtest',
                config=dict(population=128,generations=32,training_sessions=30),
                completed_sessions=0,prepared_sessions=1,
                progress=dict(completed_seconds=100,total_seconds=19800))
    for width,height in ((80,24),(128,42)):
        for view in ('financial','performance','objective'):
            stream=StringIO()
            Console(file=stream,width=width,height=height,force_terminal=False).print(
                render(status,width=width,height=height,view=view))
            output=stream.getvalue()
            assert 'Generations' not in output and 'Profile session' in output
            assert '0/1' in output and '0/30' not in output
            assert len(output.splitlines())<=height
            assert all(len(line)<=width for line in output.splitlines())
