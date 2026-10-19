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
        if p.name=='genome.py':
            # Decode once then validate once. Every validation rule and every
            # genetic operation must remain the original definition.
            before=ast.parse(expected);after=ast.parse((root/'v32'/p.name).read_text())
            for tree in (before,after):
                for cls in tree.body:
                    if isinstance(cls,ast.ClassDef) and cls.name in ('Network','Individual'):
                        cls.body=[n for n in cls.body if not (isinstance(n,ast.FunctionDef) and n.name in ('restore','_decode'))]
            assert ast.dump(before)==ast.dump(after)
            continue
        if p.name=='io_pipeline.py':
            actual=(root/'v32'/p.name).read_bytes().replace(b'\r\n',b'\n')
            assert actual.startswith(expected.replace(b'\r\n',b'\n'))
            # Compact atomic publication is covered by failure/roundtrip tests;
            # feature preparation and transfer staging still use shared code.
            continue
        if p.name in ('session_lanes.py','phase2.py','run_search.py','cpu_work.py'):
            # Shared scheduling changes orchestration only; retain the legacy
            # evaluator and all non-orchestration definitions exactly.
            before=ast.parse(expected);after=ast.parse((root/'v32'/p.name).read_text())
            excluded={'session_lanes.py':{'SessionLanes'},'phase2.py':{'run_global'},
                      'run_search.py':{'run'},'cpu_work.py':{'descriptors'}}[p.name]
            actual={n.name:n for n in after.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
            for n in before.body:
                if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name not in excluded:
                    assert ast.dump(actual[n.name])==ast.dump(n),n.name
            if p.name=='cpu_work.py':
                original=next(n for n in before.body if isinstance(n,ast.FunctionDef) and n.name=='descriptors')
                changed=actual['_descriptors'];changed.name='descriptors'
                assert ast.dump(changed)==ast.dump(original)
            continue
        if p.name=='network.py':
            before=ast.parse(expected);after=ast.parse((root/'v32'/p.name).read_text())
            for tree in (before,after):
                plan=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='NetworkPlan')
                plan.body=[n for n in plan.body if not (isinstance(n,ast.FunctionDef) and n.name=='__init__')]
            assert ast.dump(before)==ast.dump(after)
            continue
        if p.name=='evaluator.py':
            actual=(root/'v32'/p.name).read_bytes()
            # Only scheduling/preparation/export plumbing changed. The financial
            # functions and exported expressions remain exactly the same source.
            before=ast.parse(expected);after=ast.parse(actual)
            originals={n.name:n for n in before.body if isinstance(n,(ast.ClassDef,ast.FunctionDef))}
            for node in after.body:
                if isinstance(node,(ast.ClassDef,ast.FunctionDef)) and node.name not in ('PopulationPrograms','PreparedEvaluation','replay_cohort','_export_result'):
                    assert ast.dump(node)==ast.dump(originals[node.name])
            assert actual[actual.index(b'    episode_results='):].replace(b'\r\n',b'\n')==expected[expected.index(b'    episode_results='):].replace(b'\r\n',b'\n')
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

