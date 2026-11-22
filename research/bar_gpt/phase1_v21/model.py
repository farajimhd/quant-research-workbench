import torch
from ..phase1_v20.encoders import restore


def selected_rows(arm):
    coordinates=(1,) if arm=='body_direction' else (2,3) if arm=='range' else (2,3,4,5)
    return torch.tensor([j%6 in coordinates for j in range(1152)])


def initialize(teacher,saved,calibration,arm=None):
    model=restore(saved)
    model.activity_change_prefloor=arm in ('joint_prefloor','joint_basis_prefloor','joint_basis_prefloor_slow_temporal','joint_basis_prefloor_range_balanced','joint_basis_prefloor_nonlinear32','joint_basis_prefloor_temporal_extra','joint_basis_prefloor_temporal_extra_fast')
    model.temporal_lr_multiplier=.1 if arm in ('joint_basis_prefloor_slow_temporal','joint_basis_prefloor_range_balanced','joint_basis_prefloor_nonlinear32','joint_basis_prefloor_temporal_extra','joint_basis_prefloor_temporal_extra_fast') else 1.
    model.repair_arm='joint_repaired' if model.activity_change_prefloor else arm or 'both_symmetric'
    if arm in ('joint_basis_prefloor','joint_basis_prefloor_slow_temporal','joint_basis_prefloor_range_balanced','joint_basis_prefloor_nonlinear32','joint_basis_prefloor_temporal_extra','joint_basis_prefloor_temporal_extra_fast'):
        from .temporal_basis import ActivityBasisLinear
        model.decoder=ActivityBasisLinear(model.decoder)
        if arm=='joint_basis_prefloor_nonlinear32':
            from .temporal_basis import ActivityBasisNonlinear
            native=restore(saved).decoder
            model.decoder=ActivityBasisNonlinear(native)
    model.new_block_lr_multiplier=10. if arm=='joint_basis_prefloor_temporal_extra_fast' else None
    if arm in ('joint_basis_prefloor_temporal_extra','joint_basis_prefloor_temporal_extra_fast'):
        from .temporal_depth import attach
        attach(model)
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
