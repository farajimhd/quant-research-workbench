"""Exact V21 objective parity and isolated V31-derived source contract."""
from pathlib import Path
import ast
import numpy as np
from research.vectorized_backtest.v21 import objectives as reference
from research.vectorized_backtest.v32 import objectives as candidate


def test_source_is_exact_v21_and_other_engine_files_only_rename_version():
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    assert (root/'v32/objectives.py').read_bytes()==(root/'v21/objectives.py').read_bytes()
    for p in (root/'v31').iterdir():
        if not p.is_file() or p.name in ('objectives.py','README.md','local_runner.py','run_train.py'):continue
        expected=p.read_bytes().replace(b'v31',b'v32').replace(b'V31',b'V32')
        if p.name=='evaluator.py':
            actual=(root/'v32'/p.name).read_bytes()
            # Only scheduling/preparation/export plumbing changed. The financial
            # functions and exported expressions remain exactly the same source.
            assert actual.split(b'@dataclass\r\nclass PreparedEvaluation')[0].split(b'@dataclass\nclass PreparedEvaluation')[0]==expected.split(b'def replay_cohort')[0]
            assert actual[actual.index(b'    episode_results='):]==expected[expected.index(b'    episode_results='):]
            continue
        if p.name=='replay_graph.py':
            actual=ast.parse((root/'v32'/p.name).read_text())
            original=next(n for n in ast.parse(expected).body if isinstance(n,ast.FunctionDef) and n.name=='replay')
            changed=next(n for n in actual.body if isinstance(n,ast.FunctionDef) and n.name=='replay')
            assert changed.args.args[-1].arg=='capture_only'
            assert isinstance(changed.args.defaults[-1],ast.Constant) and changed.args.defaults[-1].value is False
            changed.args.args.pop();changed.args.defaults.pop()
            changed.body=[n for n in changed.body if not (isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='capture_only')]
            assert ast.dump(changed)==ast.dump(original)
            continue
        if p.name=='network_cuda.py':
            # State-channel fusion has dedicated exact CUDA regression tests.
            # Every other kernel remains byte-identical, except its launch grid.
            expected=expected[expected.index(b'def workspace_kernel'):]
            expected=expected.replace(b'state_kernel[(b,len(STATES),tr.cdiv(u,256))]',b'state_kernel[(b,tr.cdiv(u,256))]')
            actual=(root/'v32'/p.name).read_bytes()
            assert actual[actual.index(b'def workspace_kernel'):]==expected
            continue
        assert (root/'v32'/p.name).read_bytes()==expected,p.name


def test_local_and_global_exact_parity_with_fractional_trim_and_losses():
    rng=np.random.default_rng(2020);ordered=[]
    for day in range(24):
        tickers=[{str(k):float(rng.normal(30,100)) for k in range(n)} for n in (0,1,3,10,17)]
        m=dict(ticker_pnl=tickers,net_pnl=[sum(r.values()) for r in tickers],
               closed_positions=[len(r) for r in tickers],drawdown=rng.uniform(0,1000,5).tolist(),
               maximum_position_risk=[100.]*5,maximum_stop_distance=[.1]*5,
               terminal_valid=[True]*5,stop_risk_dollar_seconds=[0.]*5,capital_dollar_seconds=[0.]*5)
        ordered.append(m)
    def exact(a,b):
        assert a.keys()==b.keys()
        for k in a:
            if isinstance(a[k],dict):exact(a[k],b[k])
            else:np.testing.assert_array_equal(a[k],b[k],err_msg=k)
    exact(candidate.local_panel(ordered),reference.local_panel(ordered))
    exact(candidate.score(ordered,[1]*5),reference.score(ordered,[1]*5))
    totals,removed,_=candidate.ticker_summary([{'a':100.,'b':-50.,'c':20.}])
    np.testing.assert_array_equal(totals,[70.])
    np.testing.assert_allclose(removed,[30.],rtol=0,atol=1e-14)

