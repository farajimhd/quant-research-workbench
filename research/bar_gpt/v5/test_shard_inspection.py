"""Integrity, timeframe and padding contracts for the read-only shard inspector."""
import json
import numpy as np
import pytest
from research.bar_gpt.v5 import shard_inspection as view
from research.bar_gpt.v5.shards.phase1_contract import SAMPLE_DTYPE, VERSION, digest, file_hash


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(view, 'ROOT', tmp_path)
    for cache in (view._plan, view._available, view._shards, view._verify): cache.cache_clear()
    contract=dict(version=VERSION,years=[2025])
    (tmp_path/'contract.json').write_text(json.dumps(dict(contract=contract,binding=digest(contract))))
    (tmp_path/'plan.json').write_text(json.dumps([dict(ticker='TEST',year=2025)]))
    (tmp_path/'progress.json').write_text(json.dumps(dict(state='running',samples=2,bytes=15000)))
    job='2025-'+digest('TEST')[:20]
    folder=tmp_path/'jobs'/job/'2025-01-02';folder.mkdir(parents=True)
    a=np.zeros(2,dtype=SAMPLE_DTYPE)
    a['values'][0,:3]=10.;a['valid'][0,:3]=1
    a['values'][1]=20.;a['valid'][1]=1
    a['end_us'][0,:3]=1000000+np.arange(3)*100000
    a['end_us'][1]=1000000+np.arange(64)*60000000
    a['timeframe_us']=[100000,60000000]
    path=folder/'samples-0000.npy';np.save(path,a,allow_pickle=False)
    r=dict(binding=digest(contract),frames={'100000':1,'60000000':1},samples=2,files=[dict(name=path.name,bytes=path.stat().st_size,sha256=file_hash(path))])
    r['receipt_hash']=digest(r);(folder/'receipt.json').write_text(json.dumps(r))
    yield job,folder
    for cache in (view._plan, view._available, view._shards, view._verify): cache.cache_clear()


def test_raw_evidence_padding_and_frame_selection(corpus):
    job,folder=corpus
    assert view.catalog('TEST',2025)['total']==1
    assert len(view.shards(job)['shards'])==1
    s=view.sample(job,'2025-01-02','samples-0000.npy',timeframe=100000)
    assert len(s['bars'])==64 and s['observed']==3
    assert s['bars'][3]['end_us'] is None and s['bars'][3]['raw']==[0.]*18
    assert s['bars'][3]['values']==[None]*18 and not any(s['bars'][3]['valid'])
    minute=view.sample(job,'2025-01-02','samples-0000.npy',timeframe=60000000,randomize=True)
    assert minute['row']==1 and minute['observed']==64


def test_unpublished_paths_and_wrong_timeframe_fail_closed(corpus):
    job,_=corpus
    with pytest.raises(ValueError): view.sample('../outside','2025-01-02','samples-0000.npy')
    with pytest.raises(ValueError): view.sample(job,'../outside','samples-0000.npy')
    with pytest.raises(ValueError): view.sample(job,'2025-01-02','samples-9999.npy')
    with pytest.raises(ValueError): view.sample(job,'2025-01-02','samples-0000.npy',timeframe=60000000)


def test_mutated_payload_rejected(corpus):
    job,folder=corpus
    p=folder/'samples-0000.npy'
    with p.open('r+b') as f: f.seek(-1,2);f.write(b'x')
    with pytest.raises(ValueError,match='SHA-256'): view.sample(job,'2025-01-02',p.name)


def test_modified_receipt_rejected(corpus):
    job,folder=corpus
    p=folder/'receipt.json';r=json.loads(p.read_text());r['samples']=999;p.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='receipt'): view.sample(job,'2025-01-02','samples-0000.npy')


def test_component_namespaces_and_waiting_supplement_are_visible(corpus):
    """Two v6 bindings share one inspector without ambiguous job IDs or path fallbacks."""
    from research.bar_gpt.v5.cohort_v1 import COHORT_VERSION
    import shutil
    _, folder = corpus
    base = view.ROOT
    supplement = base / "extra"
    contract = dict(version=VERSION, years=[2025], component="fixture-extra")
    supplement.mkdir()
    (supplement / "contract.json").write_text(json.dumps(dict(contract=contract, binding=digest(contract))))
    (supplement / "plan.json").write_text(json.dumps([dict(ticker="NVDA", year=2025)]))
    (supplement / "queue.json").write_text(json.dumps(dict(state="waiting_base")))
    raw_job = "2025-" + digest("NVDA")[:20]
    target = supplement / "jobs" / raw_job / "2025-01-02"
    target.mkdir(parents=True)
    shutil.copyfile(folder / "samples-0000.npy", target / "samples-0000.npy")
    receipt = json.loads((folder / "receipt.json").read_text())
    receipt["binding"] = digest(contract)
    receipt["receipt_hash"] = digest({k: v for k, v in receipt.items() if k != "receipt_hash"})
    (target / "receipt.json").write_text(json.dumps(receipt))
    body = dict(version=COHORT_VERSION, schema=VERSION,
                members=[dict(name="base", path="."), dict(name="liquid500", path="extra")])
    (base / "cohort.json").write_text(json.dumps(dict(cohort=body, hash=digest(body))))
    view._plan.cache_clear()
    view._available.cache_clear()
    catalog = view.catalog("NVDA")
    assert catalog["total"] == 1 and catalog["components"][1]["state"] == "waiting_base"
    job = catalog["jobs"][0]["job"]
    assert job == "liquid500~" + raw_job
    sample = view.sample(job, "2025-01-02", "samples-0000.npy", row=1)
    assert sample["component"] == "liquid500" and sample["ticker"] == "NVDA"
