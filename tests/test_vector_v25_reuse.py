"""Raw-evaluation reuse must bind data, payloads and sealed evidence."""
import copy
import json
from dataclasses import replace
from pathlib import Path
import pytest
from test_vector_v25 import metrics
from test_vector_v25 import local_config,FakeWorker
from research.vectorized_backtest.v25.initial_reuse import VERSION,SOURCES,import_receipt
from research.vectorized_backtest.v25.objectives import Objective,local_panel
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,file_hash


@pytest.fixture
def reuse(tmp_path):
    day='2026-07-30';token='exact-ordered-payload-hash';identity={'input_sha256':'certified'}
    contract=dict(day=day,seed=2020,population=1024,initial_multiplier=6,dataset={'inputs':'pinned'},execution={'fees':'pinned'},
                  feature_semantics='v21-magnitude-ratio-v1',backend='compile',device='cuda',batch_size=512)
    prior=dict(contract,source_sha256=sorted(SOURCES)[0],version='v21-local-session-v1')
    receipt=tmp_path/'prior/receipt.json';seal=tmp_path/'prior/complete.json'
    write_json(receipt,dict(day=day,population_sha256=token,identity=identity,execution=contract['execution'],split='training',
                           validation_opened=False,full_session=True,metrics=metrics([[10.,-2.]]*6144)))
    write_json(seal,dict(receipt_sha256=file_hash(receipt),population_sha256=token))
    entry=dict(contract=prior,receipt=str(receipt),receipt_sha256=file_hash(receipt),seal=str(seal),seal_sha256=file_hash(seal),population_sha256=token)
    manifest=dict(version=VERSION,validation_opened=False,sessions={day:entry})
    return manifest,day,contract,token,6144,tmp_path/'new',identity


def test_import_only_raw_results_and_rescore_as_profit(reuse):
    import_receipt(*reuse)
    record=json.loads((reuse[5]/'generation-0001'/reuse[1]/'receipt.json').read_bytes())
    assert record['reuse_provenance']['old_scores_imported'] is False
    assert (local_panel([record['metrics']])['values']==8.).all()
    with pytest.raises(ValueError,match='forbidden'):replace(Objective(),drawdown_weight=.05).validate()


@pytest.mark.parametrize('change',['token','dataset','fees','source','role','bytes','identity','count'])
def test_import_rejects_changed_authorities(reuse,change):
    args=list(copy.deepcopy(reuse));entry=args[0]['sessions'][args[1]]
    if change=='token':args[3]='different'
    elif change=='dataset':args[2]['dataset']={'inputs':'different'}
    elif change=='fees':args[2]['execution']={'fees':'different'}
    elif change=='source':entry['contract']['source_sha256']='different'
    elif change=='identity':args[6]={'input_sha256':'different'}
    elif change=='count':args[4]=1024
    elif change=='bytes':Path(entry['receipt']).write_text('{}')
    else:
        p=Path(entry['receipt']);record=json.loads(p.read_bytes());record['validation_opened']=True;write_json(p,record)
        entry['receipt_sha256']=file_hash(p)
        p=Path(entry['seal']);seal=json.loads(p.read_bytes());seal['receipt_sha256']=entry['receipt_sha256'];write_json(p,seal);entry['seal_sha256']=file_hash(p)
    with pytest.raises(ValueError):import_receipt(*args)


def test_evaluation_and_sampling_remain_v21():
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    excluded={'objectives.py','local_runner.py','run_train.py','initial_reuse.py'}
    for old in (root/'v21').glob('*.py'):
        if old.name in excluded:continue
        expected=old.read_text(encoding='utf-8').replace('v21','v25').replace('V21','V25').replace('v25-magnitude-ratio-v1','v21-magnitude-ratio-v1')
        assert (root/'v25'/old.name).read_text(encoding='utf-8')==expected,old.name


def test_local_dispatch_reuses_once_then_breeds_and_resumes(tmp_path,monkeypatch,local_config):
    from research.vectorized_backtest.v25 import initial_reuse,local_runner
    from research.vectorized_backtest.v25.run_search import digest
    day='2026-08-01';manifest=tmp_path/'manifest.json';write_json(manifest,{'test':'dispatch'})
    config=dict(local_config,reuse_initial_manifest=str(manifest))
    imported=[];evaluated=[];stop=[False]
    def importer(manifest,day,contract,token,size,output,identity):
        imported.append((size,token))
        write_json(output/'generation-0001'/day/'receipt.json',dict(population_sha256=token,metrics=metrics([[100.,-10.]]*size),
                   full_session=True,execution=contract['execution'],validation_opened=False))
        if stop[0]:(output/'STOP').touch()
    class Worker(FakeWorker):
        def run(self,day,next_day,payload,generation,token,width):
            evaluated.append((generation,len(payload)))
            return super().run(day,next_day,payload,generation,token,width)
    monkeypatch.setattr(initial_reuse,'import_receipt',importer)
    options=dict(generations=3,population_size=16,seed=20,archive_capacity=8,archive_shortlist=16,worker_factory=Worker)
    full=tmp_path/'full';resumed=tmp_path/'resumed'
    local_runner.run_session(config,day,full,**options)
    stop[0]=True
    assert local_runner.run_session(config,day,resumed,**options)['completed']==1
    assert len(imported)==2 and imported[0]==imported[1] and imported[0][0]==96
    stop[0]=False;(resumed/'STOP').unlink()
    local_runner.run_session(config,day,resumed,**options)
    assert len(imported)==2 and evaluated==[(2,16),(3,16)]*2
    a=json.loads((full/'checkpoint.json').read_bytes())['state'];b=json.loads((resumed/'checkpoint.json').read_bytes())['state']
    for key in ('population','ids','pedigree','next_serial','rng','archive','archive_pool'):assert a[key]==b[key]
    scores=json.loads((full/'generation-0001/scores.json').read_bytes())
    assert scores['scores']==[90.]*96
    assert scores['diversity']['crossovers']>0 and scores['diversity']['mutations']>0
