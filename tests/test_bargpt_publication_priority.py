from research.bar_gpt.v5.publication_priority_v1 import prefer_normal


def test_priority_hysteresis_and_live_process_requirement():
    status=dict(updated=100,state='running',stage='training',metrics=dict(loader_wait_pct=.5))
    assert prefer_normal(status,101,120,live=True)
    assert not prefer_normal(status,101,120,live=False)
    status['metrics']['loader_wait_pct']=1.5
    assert not prefer_normal(status,101,120,live=True)
    assert prefer_normal(status,101,120,live=True,was_normal=True)
    status['metrics']['loader_wait_pct']=2
    assert not prefer_normal(status,101,120,live=True,was_normal=True)


def test_priority_uncertainty_validation_and_resource_pressure_back_off():
    base=dict(updated=100,state='running',stage='training',metrics=dict(loader_wait_pct=.2))
    assert not prefer_normal(base,101,95,live=True)
    assert not prefer_normal(base,116,120,live=True)
    assert not prefer_normal(base,98,120,live=True)
    for stage in ['validation','probe','saving epoch checkpoint']:
        assert not prefer_normal(dict(base,stage=stage),101,120,live=True)
    for wait in [float('nan'),float('inf'),-1]:
        assert not prefer_normal(dict(base,metrics=dict(loader_wait_pct=wait)),101,120,live=True)
    assert not prefer_normal({},101,120,live=True)


def test_exiting_worker_access_denial_does_not_fail_data_controller():
    import psutil
    from research.bar_gpt.v5.publication_priority_v1 import worker_low_priority
    class Worker:
        pid=10
        def ionice(self,value):raise psutil.AccessDenied(self.pid)
        def wait(self,timeout):return 0
    assert worker_low_priority(Worker()) is None


def test_live_worker_priority_denial_is_visible_for_conservative_backoff():
    import psutil
    from research.bar_gpt.v5.publication_priority_v1 import worker_low_priority
    class Worker:
        pid=11
        def ionice(self,value):raise psutil.AccessDenied(self.pid)
        def wait(self,timeout):raise psutil.TimeoutExpired(timeout,self.pid)
    assert worker_low_priority(Worker())==dict(pid=11,reason='live worker priority access denied')
