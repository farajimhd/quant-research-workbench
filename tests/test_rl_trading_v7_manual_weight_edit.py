import numpy as np
import pytest
import torch
from research.rl_trading.v7.experiments import manual_weight_edit as edit


def test_consistent_attribution_selects_one_neuron():
    names=[str(i) for i in range(120)]
    days=np.repeat(names,10);scores=np.ones((1200,5));known=np.ones_like(scores,bool)
    y=np.ones_like(scores);y[:,::2]=-1
    parts=np.zeros((*scores.shape,64));parts[...,2]=np.where(y<0,2.,.4)
    parts[:600,:,3]=np.where(y[:600]<0,20.,1.)
    parts[600:,:,3]=np.where(y[600:]<0,1.,20.)
    chosen=edit.choose(scores,parts,y,known,days,names)
    assert chosen['index']==2 and chosen['eligible_indices']==[2]
    np.testing.assert_allclose(np.asarray(chosen['contrast_by_half'])[:,2],1.6)
    with pytest.raises(ValueError,match='No justified'):edit.choose(scores,np.zeros_like(parts),y,known,days,names)
    with pytest.raises(ValueError,match='120'):edit.choose(scores,parts,y,known,days,names[:119])


def test_exact_single_scalar_edit_preserves_parent_and_rejects_other_changes():
    original={edit.KEY:torch.arange(64,dtype=torch.float32)[None], 'other':torch.ones(3)}
    result=edit.edited_state(original,12)
    assert result[edit.KEY][0,12]==6 and original[edit.KEY][0,12]==12
    edit.verify_edit(original,result,12)
    assert int((original[edit.KEY]!=result[edit.KEY]).sum())==1
    result['other'][0]=2
    with pytest.raises(ValueError,match='single-weight'):edit.verify_edit(original,result,12)


def test_linear_contribution_predicts_intervention_without_gradient():
    torch.manual_seed(19)
    layer=torch.nn.Linear(64,1);h=torch.randn(50,64)
    with torch.no_grad():
        before=10*layer(h).squeeze(-1)
        contribution=10*h[:,7]*layer.weight[0,7]
        layer.weight[0,7]*=.5
        after=10*layer(h).squeeze(-1)
    torch.testing.assert_close(after,before-.5*contribution,atol=2e-6,rtol=2e-6)
    assert all(p.grad is None for p in layer.parameters())


def test_replay_routes_and_restores(monkeypatch):
    from research.rl_trading.v7.experiments import manual_weight_replay,entry_replay,entry_fast_replay
    original=entry_replay.replay_session
    def run(argv,*,bundle_loader):
        assert argv==['--test'] and entry_replay.replay_session is entry_fast_replay.replay_session
        raise ValueError('intentional')
    monkeypatch.setattr(entry_replay,'main',run)
    with pytest.raises(ValueError,match='intentional'):manual_weight_replay.main(['--test'])
    assert entry_replay.replay_session is original
