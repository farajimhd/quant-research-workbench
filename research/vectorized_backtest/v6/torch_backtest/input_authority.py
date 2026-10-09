"""Authorize producers from the owned session plan before any market read."""
import json
from pathlib import Path
from .runtime import file_hash


def authorize_day(sessions, day, frozen_winner=None):
    spec=json.loads(Path(sessions).read_text(encoding='utf-8'))
    matches=[(role,item) for role in ('training','validation') for item in spec[role] if item['day']==str(day)]
    if len(matches)!=1:raise ValueError('Day absent or ambiguous in owned session plan')
    role,item=matches[0]
    if role=='validation':
        if frozen_winner is None:raise ValueError('Final validation remains sealed until verified winner freeze')
        freeze_path=Path(frozen_winner);root=freeze_path.parent
        identity_path=root/'identity.json';audit_path=root/'audit.json'
        freeze=json.loads(freeze_path.read_text(encoding='utf-8'))
        identity=json.loads(identity_path.read_text(encoding='utf-8'))
        if identity.get('version')=='v5-staged-v1':
            from .staged_audit import require_frozen
            verified,_=require_frozen(root)
            if verified['sessions']!=spec or verified['sessions_sha256']!=file_hash(sessions):
                raise ValueError('Final input plan differs from audited staged split')
            return item
        audit=json.loads(audit_path.read_text(encoding='utf-8'))
        if (not freeze.get('winner') or identity['sessions']!=spec
                or identity['arguments'].get('profile') or not audit.get('full_budget_verified')
                or audit.get('status')!='passed' or freeze['identity_sha256']!=file_hash(identity_path)
                or audit['identity_sha256']!=file_hash(identity_path)
                or audit.get('freeze_sha256')!=file_hash(freeze_path)
                or audit.get('checkpoint_sha256')!=file_hash(root/'checkpoint.json')):
            raise ValueError('Final input authorization requires exact audited full-budget freeze')
        for binding in audit['generation_bindings']:
            if file_hash(Path(binding['path']))!=binding['sha256']:
                raise ValueError('Audited training generation changed')
    return item
