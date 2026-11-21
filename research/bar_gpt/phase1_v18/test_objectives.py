import torch
from .objectives import penalties,calibrate,ACTIVITY,FRAMES


def test_hidden_scale_and_visible_direction_are_separate():
    raw=torch.ones(2,64,18,dtype=torch.float64);valid=torch.ones_like(raw,dtype=torch.bool)
    known=torch.zeros(2,64,dtype=torch.bool);known[:,:16]=True
    frames=torch.full((2,),FRAMES[0]);scale=torch.ones(6,6)
    code=raw.log1p().float();changed=raw.clone();changed[:,16:,list(ACTIVITY)]=10
    original=penalties(code,raw,valid,known,frames,scale,False)
    other=penalties(code,changed,valid,known,frames,scale,False)
    assert original[0]==0 and other[0]>0 and other[2]>0
    assert original[1]==other[1]==0


def test_negative_activity_coordinate_keeps_gradient():
    raw=torch.ones(2,64,18,dtype=torch.float64);valid=torch.ones_like(raw,dtype=torch.bool)
    code=torch.full((2,64,18),-1.,requires_grad=True)
    known=torch.ones(2,64,dtype=torch.bool);frames=torch.full((2,),FRAMES[0])
    level,delta,tail=penalties(code,raw,valid,known,frames,torch.ones(6,6),True)
    (level+delta+.01*tail).backward()
    assert (code.grad[...,list(ACTIVITY)]<0).all()
    assert torch.isfinite(code.grad).all()


def test_training_rms_calibration_and_valid_mask():
    raw=torch.ones(6,64,18,dtype=torch.float64);valid=torch.ones_like(raw,dtype=torch.bool)
    raw[:,:,4]=torch.arange(64,dtype=torch.float64)[None,:]
    scale,record=calibrate(raw,valid,torch.tensor(FRAMES))
    expected=raw[0,:,4].log1p().diff().square().mean()
    torch.testing.assert_close(scale[:,0],expected.float().expand(6))
    assert record['constant_groups']==30
    assert (scale[:,1:]==1).all()
