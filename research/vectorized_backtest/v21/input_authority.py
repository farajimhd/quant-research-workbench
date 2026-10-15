"""Explicit display-only validation authorization, separate from legacy seals."""
import json
from pathlib import Path
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def authorize_day(sessions,day,authorization):
    spec=json.loads(Path(sessions).read_text(encoding='utf-8-sig'))
    if authorization is None:raise ValueError('Display-only authorization receipt required')
    receipt=json.loads(Path(authorization).read_text(encoding='utf-8-sig'))
    if (spec.get('validation_mode')!='display_only' or receipt.get('version')!='v8-validation-display-authorization-v1'
            or receipt.get('sessions_sha256')!=file_hash(sessions) or receipt.get('selection_feedback') is not False):
        raise ValueError('Validation authorization identity mismatch')
    training={s['day'] for s in spec['training']};validation={s['day'] for s in spec['validation']}
    if training&validation or max(training)>=min(validation):raise ValueError('Chronological split overlap')
    requested=str(day)
    if requested not in validation:raise ValueError('Only explicitly listed validation dates allowed')
    return next(s for s in spec['validation'] if s['day']==requested)
