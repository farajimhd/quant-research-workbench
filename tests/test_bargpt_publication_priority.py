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
