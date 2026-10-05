"""Admission of audited development sessions without opening sealed targets."""
import json
from pathlib import Path
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.split import TRAIN, DEVELOPMENT, CONTEXT_ONLY
from research.rl_trading.v6.validation_split import read_split

VERSION = 'rl-v6-development-admission-v1'

def inventory(data, runtime, mapper=Path):
    binding=data.get('development_admission')
    if binding is None:
        return [(str(d),'train') for d in TRAIN]+[(str(d),'development') for d in DEVELOPMENT]
    root=Path(runtime).resolve()
    path=mapper(binding['path']).resolve()
    if not path.is_relative_to(root) or file_hash(path)!=binding['sha256']:
        raise ValueError('Development admission receipt changed')
    receipt=json.loads(path.read_bytes())
    split_path=mapper(receipt['split']['path']).resolve()
    if not split_path.is_relative_to(root) or file_hash(split_path)!=receipt['split']['sha256']:
        raise ValueError('Frozen development split changed')
    split=read_split(split_path)
    expected=[(str(d),'train') for d in TRAIN]+[(str(d),'development') for d in DEVELOPMENT]
    expected += [(d,'development') for d in split['days'] if split['roles'][d]=='development']
    if (receipt.get('version')!=VERSION or receipt.get('hash')!=digest({k:v for k,v in receipt.items() if k!='hash'})
        or receipt.get('sealed_days')!=[d for d in split['days'] if split['roles'][d]=='sealed_test']
        or receipt.get('admitted_days')!=[d for d,r in expected]
        or [(e['day'],e['role']) for e in data['days']]!=expected
        or data['context']['day']!=str(CONTEXT_ONLY[0]) or data.get('validation_split') is not None):
        raise ValueError('Dataset admission includes an unauthorized date or role')
    return expected

def verify_market(data, runtime, mapper=Path):
    """Bind the training target publication to every admitted 1a certificate."""
    binding=data['market_teacher_dataset'];path=mapper(binding['path']).resolve()
    if not path.is_relative_to(Path(runtime).resolve()) or file_hash(path)!=binding['sha256']:
        raise ValueError('Training 1b publication changed')
    market=json.loads(path.read_bytes())
    audit_path=mapper(market['publication_audit']).resolve()
    if not audit_path.is_relative_to(Path(runtime).resolve()) or file_hash(audit_path)!=market['publication_audit_sha256']:
        raise ValueError('Training 1b audit changed')
    audit=json.loads(audit_path.read_bytes())
    entries=[data['context']]+data['days']
    if (market['hash']!=digest({k:v for k,v in market.items() if k!='hash'}) or market['status']!='audited_1b_labels'
        or market['sealed_test_accessed'] is not False or audit['status']!='passed'
        or audit['binding']!=market['binding'] or audit['rows']!=market['rows']
        or [(e['day'],e['role']) for e in market['days']]!=[(e['day'],e['role']) for e in entries]
        or audit['day_certificates']!={e['day']:e['sha256'] for e in market['days']}):
        raise ValueError('Training 1b inventory or audit mismatch')
    if (market['binding'].get('source_binding_kind')!='day_certificates' or
        market['binding']['source_sha256']!=digest({e['day']:e['teacher_sha256'] for e in entries})):
        raise ValueError('Merged 1b source identity mismatch')
    for entry,prior in zip(market['days'],entries):
        root=mapper(entry['root']).resolve()
        if not root.is_relative_to(Path(runtime).resolve()) or file_hash(root/'complete.json')!=entry['sha256']:
            raise ValueError('Training 1b day changed')
        proof=json.loads((root/'complete.json').read_bytes())
        if (proof['binding']!=entry.get('producer_binding',market['binding']) or proof['day']!=entry['day']
            or proof['role']!=entry['role'] or proof['source_teacher_sha256']!=prior['teacher_sha256']):
            raise ValueError('Copied 1b target provenance mismatch')
    return market
