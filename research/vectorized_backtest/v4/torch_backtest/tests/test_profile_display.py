from io import StringIO
from rich.console import Console
from research.vectorized_backtest.v4.torch_backtest.dashboard import render
from dataclasses import asdict
from research.vectorized_backtest.v4.torch_backtest.stability import Objective


def test_profile_has_one_session_and_no_campaign_generation_bar():
    status=dict(mode='profile',status='profiling',stage='Backtest',
                config=dict(population=128,generations=32,training_sessions=1),
                completed_sessions=0,prepared_sessions=1,
                progress=dict(completed_seconds=100,total_seconds=19800))
    status['active_session']=dict(pnl_median=12.5,drawdown_max=99.,open_positions_max=15,fills_max=99)
    status['objective']=asdict(Objective())
    for width,height in ((80,24),(128,42)):
        for view in ('financial','positions','performance','objective'):
            stream=StringIO()
            Console(file=stream,width=width,height=height,force_terminal=False).print(
                render(status,width=width,height=height,view=view))
            output=stream.getvalue()
            assert 'Generations' not in output and 'Profile session' in output
            assert '0/1' in output and '0/30' not in output
            assert len(output.splitlines())<=height
            assert all(len(line)<=width for line in output.splitlines())


def test_two_session_profile_displays_actual_durable_counts():
    status=dict(mode='profile',status='profiling',stage='Backtest',
                config=dict(population=128,generations=0,training_sessions=2),
                completed_sessions=1,prepared_sessions=2)
    for width,height in ((80,24),(128,42)):
        stream=StringIO()
        Console(file=stream,width=width,height=height,force_terminal=False).print(
            render(status,width=width,height=height))
        output=stream.getvalue()
        assert '1/2' in output and '2/2' in output and '1/1' not in output
        assert len(output.splitlines())<=height
        assert all(len(line)<=width for line in output.splitlines())


def test_real_observer_uses_profile_identity_count(tmp_path,capsys,monkeypatch):
    import json
    from research.vectorized_backtest.v4.torch_backtest.observe import main
    monkeypatch.setattr('research.vectorized_backtest.v4.torch_backtest.observe.Console',
                        lambda **kwargs: Console(width=128,height=42,**kwargs))
    (tmp_path/'identity.json').write_text(json.dumps(dict(arguments=dict(profile=True),profile_sessions=2)))
    (tmp_path/'status.json').write_text(json.dumps(dict(status='profile_complete',completed_sessions=2,prepared_sessions=2)))
    assert main(['--output',str(tmp_path),'--once'])==0
    output=capsys.readouterr().out
    assert '2/2' in output and '2 training-session profile' in output


def test_live_training_rates_and_holding_are_visible_before_any_leader():
    status=dict(status='training',stage='Backtest',config=dict(population=128,generations=32,training_sessions=30),
                active_session=dict(pnl_median=123.45,position_win_rate=.375,profit_factor=1.25,closed_positions=80,
                                    winning_positions=30,losing_positions=50,closed_hold_mean_seconds=42.5,
                                    closed_hold_p90_seconds=90,financial_error_candidates=0,overflow_candidates=0))
    for width,height in ((80,24),(128,42)):
        stream=StringIO()
        Console(file=stream,width=width,height=height,force_terminal=False).print(render(status,width=width,height=height))
        output=stream.getvalue()
        assert '123.45' in output and '37.50' in output and '1.25' in output and '42.50' in output
        assert len(output.splitlines())<=height
        assert all(len(line)<=width for line in output.splitlines())
    stream=StringIO()
    Console(file=stream,width=128,height=42,force_terminal=False).print(render(status,width=128,height=42,view='positions'))
    assert 'LIVE POPULATION POSITION TIMING' in stream.getvalue() and '42.50' in stream.getvalue()


def test_preparing_next_session_labels_previous_completion_and_clears_stale_eta():
    status=dict(status='training',stage='Transfer certified inputs',replay_eta=0,
                config=dict(generations=32,training_sessions=30),
                progress=dict(completed_seconds=19800,total_seconds=19800))
    stream=StringIO()
    Console(file=stream,width=128,height=42,force_terminal=False).print(render(status,width=128,height=42))
    output=stream.getvalue()
    assert 'Last backtest s' in output and 'replay ETA —' in output
    assert 'replay ETA 0:00:00' not in output


def test_objective_rows_show_configured_weights_pending_and_signed_arithmetic():
    from research.vectorized_backtest.v4.torch_backtest.dashboard import components_table
    stream=StringIO();console=Console(file=stream,width=128,force_terminal=False)
    console.print(components_table({},asdict(Objective()),profiling=True))
    output=stream.getvalue()
    assert 'Median reward' in output and 'Capital time' in output and 'Total score' in output
    assert '-0.002' in output and 'full 30-day objective is not computed' in output
    values=dict(median_reward=.02,ex_best_reward=.01,tail_penalty=.003,drawdown_penalty=.002,
                stop_risk_penalty=.001,capital_time_penalty=.0002,complexity_penalty=.0001)
    stream.seek(0);stream.truncate()
    console.print(components_table(dict(objective_components=values),asdict(Objective())))
    assert '0.023700' in stream.getvalue() and '-0.003000' in stream.getvalue()


def test_short_fixed_viewport_objective_pages_retain_all_terms_and_scope():
    status=dict(mode='profile',status='profiling',objective=asdict(Objective()),
                config=dict(population=128,generations=0,training_sessions=1))
    for width in (80,128):
        output=''
        for page in range(3):
            stream=StringIO()
            Console(file=stream,width=width,height=30,force_terminal=False).print(
                render(dict(status,_objective_page=page),width=width,height=30,view='objective'))
            frame=stream.getvalue();output+=frame
            assert len(frame.splitlines())==30
            assert 'full 30-day objective is not computed' in frame
        assert all(term in output for term in ('Median reward','Ex-best reward','Tail loss',
                    'Drawdown','Stop-risk time','Capital time','Complexity','Total score'))
