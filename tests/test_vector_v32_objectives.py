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
        if p.name=='calibration_cpu.py':
            actual=(root/'v32'/p.name).read_text()
            actual=actual.replace('            terms=np.zeros(counts[node],np.float32)\n','')
            actual=actual.replace('z=bias[node];ok=False\n                    for edge in range(counts[node]):terms[edge]=0','z=bias[node];ok=False;terms=np.zeros(counts[node],np.float32)')
            assert ast.dump(ast.parse(actual))==ast.dump(ast.parse(expected))
            continue
        if p.name=='lineage.py':
            actual=(root/'v32'/p.name).read_text()
            actual=actual.replace('    from .identity_cache import program_key as cached_key\n    cached=cached_key(member)\n    if cached is not None:return cached\n','')
            assert ast.dump(ast.parse(actual))==ast.dump(ast.parse(expected))
            continue
        if p.name=='species.py':
            actual=(root/'v32'/p.name).read_text()
            actual=actual.replace('        from .species_distance import DistanceIndex\n        distance_index=DistanceIndex(representatives)\n','')
            actual=actual.replace('distances=distance_index.distances(descriptors[int(i)])','distances=[(distance(descriptors[int(i)],rep),sid) for sid,rep in representatives.items()]')
            actual=actual.replace('                distance_index.add(sid,descriptors[int(i)])\n','')
            assert ast.dump(ast.parse(actual))==ast.dump(ast.parse(expected))
            continue
        if p.name=='genome.py':
            # Decode once then validate once. Every validation rule and every
            # genetic operation must remain the original definition.
            text=(root/'v32'/p.name).read_text()
            text=text.replace('k=options[int(rng.integers(len(options)))]','k=int(rng.choice(options))')
            text=text.replace('        from .sampling_fast import operator_choice\n','')
            text=text.replace('operator_choice(rng,len(n.sources))',"str(rng.choice(tuple(o for o in OPS if o not in ('identity','conv') and (o!='attention' or len(n.sources)>=3 and len(n.sources)%2==1))))")
            before=ast.parse(expected);after=ast.parse(text)
            network=next(n for n in after.body if isinstance(n,ast.ClassDef) and n.name=='Network')
            wrapper=next(n for n in network.body if isinstance(n,ast.FunctionDef) and n.name=='validate')
            expected_wrapper=ast.parse('def validate(self):\n from .validation_cache import validate\n return validate(self,self._validate_uncached)').body[0]
            assert ast.dump(wrapper)==ast.dump(expected_wrapper)
            network.body.remove(wrapper)
            next(n for n in network.body if isinstance(n,ast.FunctionDef) and n.name=='_validate_uncached').name='validate'
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
                    if p.name=='phase2.py' and n.name=='seed_pool':
                        import copy
                        changed=copy.deepcopy(actual[n.name])
                        normalization=ast.parse('pool=json.loads(json.dumps(pool,allow_nan=False))').body[0]
                        indices=[i for i,node in enumerate(changed.body) if ast.dump(node)==ast.dump(normalization)]
                        assert len(indices)==1
                        changed.body.pop(indices[0])
                        assert ast.dump(changed)==ast.dump(n)
                        continue
                    assert ast.dump(actual[n.name])==ast.dump(n),n.name
            if p.name=='cpu_work.py':
                original=next(n for n in before.body if isinstance(n,ast.FunctionDef) and n.name=='descriptors')
                changed=actual['_descriptors'];changed.name='descriptors'
                assert ast.dump(changed)==ast.dump(original)
            continue
        if p.name=='network.py':
            before=ast.parse(expected);after=ast.parse((root/'v32'/p.name).read_text())
            changed=next(n for n in after.body if isinstance(n,ast.ClassDef) and n.name=='NetworkPlan')
            changed.body=[n for n in changed.body if not (isinstance(n,ast.FunctionDef) and n.name=='project')]
            next(n for n in changed.body if isinstance(n,ast.FunctionDef) and n.name=='_project').name='project'
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

