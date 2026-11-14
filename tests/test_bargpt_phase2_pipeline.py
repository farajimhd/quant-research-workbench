"""Lazy coverage, ordered prefetch and packed-batch exact cursor recovery."""
from types import SimpleNamespace
import random
import numpy as np
import polars as pl
import pytest
import torch
from research.bar_gpt.phase2_v1.release import ReleasedUnits,certify,load_released_units
from research.bar_gpt.phase2_v1.data import Unit,checked_unit,unit_readers
from research.bar_gpt.phase2_v1.train import batches,train,load_checkpoint
from research.bar_gpt.phase2_v1.model import Forecaster
from research.bar_gpt.phase2_v1.config import Config
from tests.test_bargpt_phase2_latent_v1 import session,Teacher,Reporter,options
from tests.test_bargpt_phase2_all_release import dataset


@pytest.fixture(autouse=True)
def one_thread():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def test_lazy_index_no_payload_resolution_until_use_and_same_coverage(tmp_path,monkeypatch):
    from research.bar_gpt.phase2_v1 import release
    unit=dataset(tmp_path/'source');out=tmp_path/'release';certify([unit.root],out,workers=1)
    original=release.beneath
    def metadata_only(root,path):
        assert not path.replace('\\','/').startswith('shards/')
        return original(root,path)
    monkeypatch.setattr(release,'beneath',metadata_only)
    units,proof=load_released_units(out/'certificate.json')
    assert isinstance(units,ReleasedUnits) and len(units)==1 and proof['blocks']==1
    assert units[0].path==unit.path and units[-1].blocks==unit.blocks
    assert [i for i,u in units.iter_from(0)]==[0]
    assert list(units.iter_from(1))==[]
    checked_unit(units[0])


def test_compact_index_preserves_global_indices_split_and_resume(tmp_path):
    def table(paths,splits):
        return pl.DataFrame(dict(path=paths,ordinal=[[0]]*len(paths),split=[[s] for s in splits],
              liquid=[[1]]*len(paths),selection=[[i] for i in range(len(paths))],
              receipt_hash=['seal']*len(paths),unit_split=splits))
    units=ReleasedUnits([(tmp_path/'one','binding',table(['shards/a','shards/b'],[0,1])),
                         (tmp_path/'two','binding',table(['shards/c','shards/d'],[0,1]))])
    assert len(units)==4 and units[-1].path.name=='d'
    assert [i for i,u in units.iter_from(0)]==[0,2]
    assert [i for i,u in units.iter_from(1,2)]==[3]
    assert [u.path.name for u in units[:2]]==['a','b']
    with pytest.raises(IndexError):units[4]


def test_read_time_path_containment_and_prefetch_budget_fail_closed(tmp_path):
    a=session(tmp_path/'a');b=session(tmp_path/'b')
    with pytest.raises(ValueError,match='escapes'):
        checked_unit(Unit(a.root,b.path,b.binding,b.blocks))
    with pytest.raises(MemoryError,match='prefetch budget'):
        list(unit_readers([a],0,workers=4,prefetch_mib=1))


def test_packing_and_prefetch_keep_every_input_target_and_evidence(tmp_path):
    units=[session(tmp_path/'a'),session(tmp_path/'b','2023-01-04')]
    args=options(tmp_path);args.batch_size=256;args.pack_batches=False;args.io_workers=0
    raw=list(batches(units,0,Teacher(),'pinned',args,reconstruction=True))
    args.pack_batches=True;args.io_workers=2
    packed=list(batches(units,0,Teacher(),'pinned',args,reconstruction=True))
    assert [len(b[2]) for b in raw]==[193,193]
    assert [len(b[2]) for b in packed]==[256,130]
    assert packed[0][3]==(1,0,63) and packed[-1][3]==(1,0,193)
    for i in range(3):assert torch.equal(torch.cat([r[i] for r in raw]),torch.cat([r[i] for r in packed]))
    for key in raw[0][6]:
        assert torch.equal(torch.cat([r[6][key] for r in raw]),torch.cat([r[6][key] for r in packed]))
    resumed=list(batches(units,0,Teacher(),'pinned',args,cursor=packed[0][3]))
    assert torch.equal(resumed[0][2],packed[1][2])


def test_cross_unit_packed_stop_resume_fullstate_exact(tmp_path):
    units=[session(tmp_path/'a'),session(tmp_path/'b','2023-01-04'),session(tmp_path/'v','2026-01-05')]
    contract=dict(version='packed-resume-fixture')
    def fresh():
        random.seed(7);np.random.seed(7);torch.manual_seed(7)
        return Forecaster(Config(layers=1,feedforward=64)),Teacher()
    def args(path,resume=None):
        a=options(path,resume);a.epochs=1;a.batch_size=256;a.io_workers=2;return a
    full=tmp_path/'full';full.mkdir();m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(full),Reporter())=='complete'
    expected=load_checkpoint(full/'checkpoint_last.pt',contract)
    stopped=tmp_path/'stopped';stopped.mkdir();m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(stopped),Reporter(stopped,1))=='stopped'
    partial=load_checkpoint(stopped/'checkpoint_last.pt',contract)
    assert partial['cursor']==(1,0,63)
    (stopped/'STOP').unlink()  # exact fixture-owned marker only
    m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(stopped,stopped/'checkpoint_last.pt'),Reporter())=='complete'
    actual=load_checkpoint(stopped/'checkpoint_last.pt',contract)
    assert expected['step']==actual['step']==2 and expected['history']==actual['history']
    assert expected['scheduler']==actual['scheduler']
    assert torch.equal(expected['rng']['torch'],actual['rng']['torch'])
    for k in expected['model']:assert torch.equal(expected['model'][k],actual['model'][k])
    for k,row in expected['optimizer']['state'].items():
        for name,v in row.items():assert torch.equal(v,actual['optimizer']['state'][k][name])
