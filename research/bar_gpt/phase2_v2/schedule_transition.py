"""Explicit audited correction of historical cycle labels, never implicit resume."""
import copy
from .schedule import ValidationCosine,FullPassCosine
from ..v5.shards.contract import digest


def prepare_full_pass_state(saved,contract,fraction):
    old=saved['scheduler']
    if (old.get('version')!=ValidationCosine.VERSION or old['updates']!=saved['step']
            or type(saved.get('data_pass')) is not int or saved['data_pass']<1):
        raise ValueError('Expected intact validation-cycle checkpoint ledgers')
    policy=contract['schedule']
    if policy['version']!=FullPassCosine.VERSION or policy['epoch_kind']!='full dataset pass':
        raise ValueError('Correction requires explicit full-pass contract')
    scheduler=FullPassCosine(policy['initial_peak'],policy['initial_floor'],
        policy['validation_every_updates'],policy['warmup_updates'],policy['restart_peak_decay'])
    scheduler.updates=saved['step'];scheduler.epoch=saved['data_pass']-1
    scheduler.validation(fraction)
    prepared=dict(saved)
    prepared.update(contract=copy.deepcopy(contract),epoch=scheduler.epoch,scheduler=scheduler.state_dict(),
        pass_exhausted=False,schedule_transition=dict(version='explicit-full-pass-correction-v1',
            reason='validation intervals were incorrectly counted as dataset epochs',
            original_contract=digest(saved['contract']),original_scheduler=copy.deepcopy(old),
            original_epoch=saved['epoch'],legacy_cycle_history_entries=len(saved['history']),
            preserved_step=saved['step'],preserved_cursor=list(saved['cursor'])))
    return prepared
