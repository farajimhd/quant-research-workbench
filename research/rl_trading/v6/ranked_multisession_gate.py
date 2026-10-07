"""Immutable complete-head admission for the six-session ranked teacher."""
import json
from pathlib import Path
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.run_ranked_teacher_underfit import passes
from research.rl_trading.v6.run_ranked_teacher_generalization import exact_metrics
from research.rl_trading.v6.ranked_multisession_metrics import pool_gate_metrics
from research.rl_trading.v6.ranked_normalization import VERSION
from research.rl_trading.v6.execution_features import CANDLE_NORMALIZATION_VERSION


def admit_multisession(root, source_dir=None):
    plan = json.loads((root/'manifest.json').read_text())
    complete = json.loads((root/'complete.json').read_text())
    result = json.loads((root/'result.json').read_text())
    width = plan.get('width', 128)
    if type(width) is not int or width not in (128, 512) or ('width' in plan and plan.get('arguments', {}).get('width') != width):
        raise ValueError('Bounded model width must match admitted underfit arguments')
    checkpointing=plan.get('activation_checkpointing',False)
    if type(checkpointing) is not bool or ('activation_checkpointing' in plan and
            plan.get('arguments',{}).get('activation_checkpointing') is not checkpointing):
        raise ValueError('Activation checkpointing must match admitted underfit arguments')
    if (plan.get('version') != 'rl-v6-ranked-six-session-underfit-v1' or
            plan.get('hash') != digest({k:v for k,v in plan.items() if k != 'hash'}) or
            len(plan.get('sessions', [])) != 6 or
            plan.get('input_population_preserved') is not True or
            plan.get('sealed_labels_read') is not False or
            plan.get('development_labels_read') is not False or
            plan.get('workstation_gpu_used') is not False or
            plan.get('generalization_evaluated') is not False or
            complete.get('status') != 'completed' or complete.get('passed') is not True or
            complete.get('reload_exact') is not True or
            complete.get('generalization_evaluated') is not False or
            plan.get('teacher_loss') != 'branch-balanced-v3' or plan.get('regression_weights') != [0.,0.] or
            plan.get('auxiliary_weights') != dict(ratio=1., forecast=1., quality=1., future_quality=1.) or
            not passes(complete['metrics']) or result.get('passed') is not True or
            complete['epoch'] != result['epoch'] or
            not exact_metrics(complete['metrics'], result['metrics']) or
            len(result.get('sessions', [])) != 6 or
            not exact_metrics(pool_gate_metrics(result['sessions']), complete['metrics'])):
        raise ValueError('Exact same-checkpoint six-session underfit pass required')
    if any(complete['metrics']['action_class_counts'][n] != 32 for n in ('wait','enter_long','hold','exit_long')):
        raise ValueError('Exact 128-row balanced underfit population required')
    if file_hash(root/'last.pt') != complete['checkpoint_sha256']:
        raise ValueError('Six-session checkpoint changed')
    normalization = json.loads((root/'normalization.json').read_text())
    if (file_hash(root/'normalization.json') != plan['normalization_sha256'] or
            normalization.get('version') != VERSION or normalization.get('scope') != 'train_only' or
            plan.get('feature_contract') != CANDLE_NORMALIZATION_VERSION):
        raise ValueError('Six-session normalization binding changed')
    source_dir = Path(__file__).parent if source_dir is None else source_dir
    if not plan.get('source_files_sha256') or any(file_hash(source_dir/n) != h for n,h in plan['source_files_sha256'].items()):
        raise ValueError('Six-session model/objective source changed')
    if len({s['day'] for s in plan['sessions']}) != 6:
        raise ValueError('Six unique TRAIN sessions required')
    return plan, complete, normalization
