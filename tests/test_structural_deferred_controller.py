from contextlib import nullcontext
import json

from scripts import run_structural_after_bars as controller


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('COMPUTERNAME','DESKTOP-SAAI85T')
    monkeypatch.setattr(controller,'RUNTIME',tmp_path)
    monkeypatch.setattr(controller,'build_lock',lambda path:nullcontext())
    bars=tmp_path/'bars';bars.mkdir()
    run=tmp_path/'structural';run.mkdir()
    return bars,run,['--bars-run-dir',str(bars),'--run-dir',str(run)]


def test_stop_while_waiting_never_launches_a_child(tmp_path,monkeypatch):
    bars,run,args=setup(tmp_path,monkeypatch)
    (run/'STOP').touch()
    monkeypatch.setattr(controller.subprocess,'Popen',lambda *a,**k:(_ for _ in ()).throw(AssertionError('launched')))
    assert controller.main(args)==130
    assert json.loads((run/'controller-state.json').read_text())['phase']=='interrupted'


def test_failed_plan_never_runs_profile_or_writer(tmp_path,monkeypatch):
    bars,run,args=setup(tmp_path,monkeypatch)
    (bars/'status.json').write_text(json.dumps(dict(status='complete',failed=0)))
    calls=[]
    class Child:
        pid=123
        def __init__(self,cmd,**kwargs):calls.append(cmd)
        def wait(self):return 1
    monkeypatch.setattr(controller.subprocess,'Popen',Child)
    assert controller.main(args)==1
    assert len(calls)==1 and calls[0][-1]=='--plan-only'
    assert json.loads((run/'controller-state.json').read_text())['failed_phase']=='plan'


def test_controller_requires_plan_and_profile_before_full_fill(tmp_path,monkeypatch):
    bars,run,args=setup(tmp_path,monkeypatch)
    (bars/'status.json').write_text(json.dumps(dict(status='complete',failed=0)))
    calls=[]
    class Child:
        pid=123
        def __init__(self,cmd,**kwargs):calls.append(cmd)
        def wait(self):
            if len(calls)==3:
                (run/'structural-completion.json').write_text(json.dumps(dict(completed=2,reused=1,failed=0)))
                (run/'status.json').write_text(json.dumps(dict(status='complete',total=3)))
            return 0
    monkeypatch.setattr(controller.subprocess,'Popen',Child)
    assert controller.main(args)==0
    assert [c[-1] for c in calls]==['--plan-only','--profile-only','--migrate-schema']
    assert json.loads((run/'controller-state.json').read_text())['phase']=='complete'


def test_spawned_workers_refuse_a_reused_parent_pid(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    import os
    import psutil
    import pytest
    from scripts.backfill_structural_sessions import _ticker_worker
    spec=tmp_path/'spec.json'
    spec.write_text(json.dumps(dict(owner=dict(pid=os.getpid(),created=psutil.Process().create_time()+1))))
    with ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn')) as pool:
        futures=[pool.submit(_ticker_worker,str(spec),'TEST') for _ in range(2)]
        for future in futures:
            with pytest.raises(RuntimeError,match='owner exited'):future.result(timeout=30)
    assert not list(tmp_path.glob('lease-*.json'))


def test_new_pool_waits_for_previous_worker_identity(tmp_path):
    from types import SimpleNamespace
    import os
    import psutil
    import pytest
    from scripts.backfill_structural_sessions import run_pool
    workers=tmp_path/'workers';workers.mkdir()
    (workers/'lease-old.json').write_text(json.dumps(dict(pid=os.getpid(),created=psutil.Process().create_time())))
    with pytest.raises(ValueError,match='still draining'):
        run_pool(SimpleNamespace(run_dir=tmp_path),{},'',{}, {}, [], [], {}, None,None,'','',0)


def test_worker_preserves_unpickleable_clickhouse_failure(tmp_path,monkeypatch):
    import pickle
    from contextlib import nullcontext
    from scripts import backfill_structural_sessions as runner
    from research.mlops.clickhouse import ClickHouseHttpStatusError
    import pytest
    (tmp_path/'workers').mkdir()
    spec=dict(owner={'token':'test'},arguments=dict(run_dir=str(tmp_path),
        bars_run_dir=str(tmp_path),env_file=str(tmp_path/'env'),endpoint='http://localhost'))
    monkeypatch.setattr(runner,'_SPEC_CACHE',spec)
    monkeypatch.setattr(runner,'require_parent',lambda owner:None)
    monkeypatch.setattr(runner.builder,'build_lock',lambda path:nullcontext())
    def fail(*args):raise ClickHouseHttpStatusError(500,'Server Error','Query memory limit exceeded')
    monkeypatch.setattr(runner,'_process_ticker',fail)
    with pytest.raises(RuntimeError,match='Query memory limit exceeded') as caught:
        runner._ticker_worker('unused','TEST')
    assert 'ClickHouseHttpStatusError' in str(pickle.loads(pickle.dumps(caught.value)))
    assert not list((tmp_path/'workers').glob('lease-*.json'))
