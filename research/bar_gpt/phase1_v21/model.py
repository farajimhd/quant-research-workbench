import torch
from ..phase1_v20.encoders import restore


def initialize(teacher,saved,calibration,arm=None):
    model=restore(saved)
    model.repair_arm=arm or 'both_symmetric'
    if model.repair_arm.startswith('joint_'):
        model.requires_grad_(True)
        return model
    model.requires_grad_(False)
    model.decoder.requires_grad_(True)
    active=torch.tensor([j%6 in ((2,3) if model.repair_arm=='range' else (2,3,4,5)) for j in range(1152)])
    model.register_buffer('active_rows',active,persistent=False)
    model.decoder.weight.register_hook(lambda g:g*model.active_rows[:,None])
    model.decoder.bias.register_hook(lambda g:g*model.active_rows)
    return model


def verify_protected(model,saved):
    if model.repair_arm.startswith('joint_'):return
    old=saved['model']
    active=torch.tensor([j%6 in ((2,3) if model.repair_arm=='range' else (2,3,4,5)) for j in range(1152)])
    for name,value in model.state_dict().items():
        actual=value.detach().cpu()
        if name.startswith('decoder.'):
            torch.testing.assert_close(actual[~active],old[name][~active],rtol=0,atol=0)
        else:torch.testing.assert_close(actual,old[name],rtol=0,atol=0)
