from types import SimpleNamespace
import numpy as np
import pytest
import torch
from .loader_census import label_pool,LoaderCensus,TAG
from .class_census import count_block


def test_labels_match_census_and_transactional_resume():
    rng=np.random.default_rng(17);values=(100+rng.normal(0,.1,(130,18))).astype(np.float32)
    valid=np.ones_like(values,dtype=bool);valid[3,9]=False
    tags=label_pool(values,valid,2024,1)
    expected,_=count_block(values[:,[3,9,15]],valid[:,[3,9,15]],np.arange(129),np.array([1]))
    c=LoaderCensus(17,0,(0,0,0));c.commit(tags[:50],18,0,(0,0,50))
    # Prefetch may produce arbitrary tags; no mutation occurs before commit.
    before=c.state();discarded=tags[50:].copy();assert c.state()==before
    c=LoaderCensus(18,0,(0,0,50),before);c.commit(discarded,19,0,(0,0,130))
    np.testing.assert_array_equal(c.counts.sum((0,1))[:,:11],expected[0,0])
    assert c.origins==130  # Last pool row explicitly invalid, not silently dropped.
    with pytest.raises(ValueError):c.commit(tags,19,0,(0,0,130))
    with pytest.raises(ValueError):LoaderCensus(20,0,(0,0,130),c.state())
    from ..v5.checkpoint_transition_v1 import state_hash
    assert state_hash(c.state())


def test_batch_packing_keeps_cpu_tags_aligned(monkeypatch):
    from . import data
    def source(*args,**kwargs):
        for start,n in ((0,3),(3,4)):
            x=torch.arange(start,start+n).reshape(-1,1)
            tags=np.tile(np.arange(start,start+n,dtype=np.int8)[:,None],(1,5))
            yield x,x,dict(logs=x,valid=x.bool(),**{TAG:tags}),(0,start,n),np.arange(n),1
    monkeypatch.setattr(data,'source_batches',source)
    a=SimpleNamespace(pack_batches=True,batch_size=4,loader_census=True)
    rows=list(data.batches(None,0,None,None,a))
    assert [len(r[0]) for r in rows]==[4,3]
    for x,_,target,*_ in rows:np.testing.assert_array_equal(target[TAG][:,0],x[:,0].numpy())


def test_real_checkpoint_stop_resume_and_pass_end(tmp_path):
    from .full_pass import train_full_pass
    from .metrics import Metrics
    from ..phase2_v2.train import load_checkpoint
    class Model(torch.nn.Module):
        def __init__(self):super().__init__();self.w=torch.nn.Parameter(torch.tensor(.1))
        def training_prediction(self,x,ages):return self.w.expand(len(x),64,18),None
        def training_loss(self,p,t,ids=None):return (p-t['logs']).square().mean()
    def batches(*args,cursor=(0,0,0),**kwargs):
        for i in range(cursor[2],4):
            target=dict(logs=torch.zeros(2,64,18),valid=torch.ones(2,64,18,dtype=torch.bool),
                        **{TAG:np.array([[0,1,6,5,1]]*2,dtype=np.int8)})
            yield torch.zeros(2,16,256),torch.zeros(2,16,2),target,(0,0,i+1),np.arange(2),np.ones(2)
    class Reporter:
        stopping=True
        def update(self,**kw):pass
        def log(self,m,step):
            if self.stopping and step==2:(tmp_path/'STOP').write_text('test stop')
    a=SimpleNamespace(device='cpu',precision='fp32',learning_rate=3e-5,min_learning_rate=3e-6,
        validation_every=100,warmup_updates=0,output=tmp_path,resume=None,epochs=1,
        batch_size=2,log_every=1,checkpoint_every=100,patience=100,loader_census=True)
    components=SimpleNamespace(batches=batches,Metrics=Metrics,target_size=lambda t:len(t['logs']),evaluate=lambda *args:dict(loss=.1))
    units=[SimpleNamespace(blocks=[(0,0,1,0)])];contract={'test':'loader-census'};reporter=Reporter()
    assert train_full_pass(Model(),None,'',units,contract,a,reporter,components=components)=='stopped'
    saved=load_checkpoint(tmp_path/'checkpoint_last.pt',contract)
    assert saved['step']==2 and saved['loader_census']['origins']==4
    (tmp_path/'STOP').unlink();a.resume=tmp_path/'checkpoint_last.pt';reporter.stopping=False
    assert train_full_pass(Model(),None,'',units,contract,a,reporter,components=components)=='complete'
    saved=load_checkpoint(a.resume,contract)
    assert saved['step']==4 and saved['loader_census']['origins']==8
    c=LoaderCensus(saved['step'],saved['epoch'],saved['cursor'],saved['loader_census'])
    assert c.origins==8
