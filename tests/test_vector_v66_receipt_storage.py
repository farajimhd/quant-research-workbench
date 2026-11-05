import json
import numpy as np
import pytest
from research.vectorized_backtest.v66 import receipt_storage as storage
from research.vectorized_backtest.v66.publication_lane import publish_replays
from research.vectorized_backtest.v66.progress_sessions import read_verified,load_live_session
from research.vectorized_backtest.v66.prefetch_pipeline import read_results
from research.vectorized_backtest.v66.distributed_assets import sha
from research.vectorized_backtest.v66.completed_replays import restore
from research.vectorized_backtest.v66.full_common import aggregate
from test_vector_v66_restored_summary import fixture


def test_publication_panel_cache_and_resume_use_same_metrics(tmp_path):
    job,day,legacy,metrics=fixture(tmp_path)
    original=json.loads(legacy.read_bytes());metrics=original['metrics']
    source=tmp_path/'paired/receipt.json';view=tmp_path/'training/generation-0001'/day/'receipt.json'
    cache=tmp_path/'cache.json'
    keys=[str(i) for i in range(12)]
    original.update(program_keys=keys,result_identity={'scope':'training'})
    sliced=dict(original,paired_replay=dict(receipt=str(source),sha256=None,offset=0,count=12))
    hashes=publish_replays([(str(source),original),(str(view),sliced),
        (str(cache),dict(identity=original['result_identity'],results={k:{} for k in keys}))],True)
    assert 'metrics' not in json.loads(source.read_bytes())
    assert json.loads(view.read_bytes())['metrics_storage']['format']==storage.VIEW
    assert read_verified(view,hashes[str(view)])['metrics']==metrics
    results=read_results(cache)[1]['results']
    assert results['0']['episode_profits']==metrics['episode_profits'][0]
    job['founder_readmission']['completed_replays']={f'training/1/{day}':dict(path=str(view),sha256=sha(view))}
    restored,_=restore(job,tmp_path/'training','training',[day],1,'token',12)
    assert restored[0]==metrics
    panel,_=load_live_session(job,tmp_path,1,'training',day,list(range(12)),'token')
    for key,value in aggregate([metrics]).items():np.testing.assert_array_equal(panel['panel'][key],value)
    source.write_text('{}')
    with pytest.raises(ValueError,match='source integrity'):read_verified(view)
    with pytest.raises(ValueError,match='integrity'):read_results(cache)


def test_legacy_and_invalid_views(tmp_path):
    legacy={'metrics':{'net_pnl':[1.,-0.,1.e-20]}}
    assert storage.decode('legacy.json',legacy)==legacy
    source=tmp_path/'source/receipt.json';source.parent.mkdir()
    source.write_text(json.dumps(storage.encode(source,legacy)))
    view=tmp_path/'view/receipt.json'
    ref=dict(metrics_storage=dict(format=storage.VIEW,path='../source/receipt.json',sha256=sha(source),offset=1,count=2))
    assert storage.decode(view,ref)['metrics']['net_pnl']==[-0.,1.e-20]
    ref['metrics_storage']['count']=9
    with pytest.raises(ValueError,match='outside'):storage.decode(view,ref)
    encoded=storage.encode(source,legacy);encoded['metrics_storage']['sha256']='0'*64
    with pytest.raises(ValueError,match='payload integrity'):storage.decode(source,encoded)


def test_retired_source_manifest_requires_original_hash(tmp_path):
    from research.vectorized_backtest.v66.global_contract import source_job_path
    old=tmp_path/'retired/job.json';old.parent.mkdir();old.write_text('{"execution":{}}')
    job=dict(source_job=str(old),source_job_sha256=sha(old),campaign=str(tmp_path/'current'))
    archived=tmp_path/'current/source-authority'/(sha(old)+'.json')
    archived.parent.mkdir(parents=True);archived.write_bytes(old.read_bytes())
    old.unlink()
    assert source_job_path(job)==archived
    archived.write_text('{}')
    with pytest.raises(ValueError,match='source_job changed'):source_job_path(job)
