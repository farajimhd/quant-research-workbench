import torch
from ..phase1_v20.encoders import restore


def selected_rows(arm):
    coordinates=(1,) if arm=='body_direction' else (2,3) if arm=='range' else (2,3,4,5)
    return torch.tensor([j%6 in coordinates for j in range(1152)])


def initialize(teacher,saved,calibration,arm=None):
    model=restore(saved)
    model.activity_change_prefloor=arm=='joint_prefloor'
    model.repair_arm='joint_repaired' if model.activity_change_prefloor else arm or 'both_symmetric'
    if model.repair_arm.startswith('joint_'):
        model.requires_grad_(True)
        return model
    model.requires_grad_(False)
    model.decoder.requires_grad_(True)
    active=selected_rows(model.repair_arm)
    model.register_buffer('active_rows',active,persistent=False)
    model.decoder.weight.register_hook(lambda g:g*model.active_rows[:,None])
    model.decoder.bias.register_hook(lambda g:g*model.active_rows)
    return model


def verify_protected(model,saved):
    if model.repair_arm.startswith('joint_'):return
    old=saved['model']
    active=selected_rows(model.repair_arm)
    for name,value in model.state_dict().items():
        actual=value.detach().cpu()
        if name.startswith('decoder.'):
            torch.testing.assert_close(actual[~active],old[name][~active],rtol=0,atol=0)
        else:torch.testing.assert_close(actual,old[name],rtol=0,atol=0)
