"""Fresh byte audit of consumed inputs after the full training receipt audit.

No SQL, tensor loading, process changes or validation access before completion.
This supplemental proof does not replace audit.json or authorize producers.
"""
import argparse
import json
import time
from pathlib import Path

from .runtime import file_hash, write_json, code_hash


BINDINGS = ('day', 'execution', 'feature_certificate', 'prior_certificate',
            'identity_map_sha256', 'split_certificate_sha256',
            'previous_split_certificate_sha256')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class FreshHashes:
    def __init__(self):
        self.files = {}

    def verify(self, path, expected=None):
        path = Path(path).resolve()
        key = str(path)
        before = path.stat()
        stamp = (before.st_size, before.st_mtime_ns)
        if key in self.files:
            item = self.files[key]
            if tuple(item['stamp']) != stamp:
                raise ValueError('Input changed during audit: ' + key)
            actual = item['sha256']
        else:
            actual = file_hash(path)
            after = path.stat()
            if stamp != (after.st_size, after.st_mtime_ns):
                raise ValueError('Input changed while hashing: ' + key)
            self.files[key] = dict(path=key, sha256=actual, bytes=before.st_size,
                                   stamp=stamp)
        if expected is not None and actual != expected:
            raise ValueError('Input byte hash mismatch: ' + key)
        return actual


def verify_bank(root, certificate_hash, hashes, *, day=None, before_day=None):
    from research.rl_trading.v1.common import digest
    from .feature_bank import SCALARS, LEVELS

    root = Path(root)
    hashes.verify(root / 'complete.json', certificate_hash)
    certificate = read(root / 'complete.json')
    hashes.verify(root / 'plan.json')
    plan = read(root / 'plan.json')
    if (certificate.get('status') != 'complete'
            or plan.get('hash') != digest({k: v for k, v in plan.items() if k != 'hash'})
            or certificate['plan_hash'] != plan['hash']):
        raise ValueError('Feature plan/certificate mismatch')
    if day is not None and plan['day'] != day:
        raise ValueError('Feature day mismatch')
    if before_day is not None and plan['day'] >= before_day:
        raise ValueError('Prior bank reaches current/future session')
    hashes.verify(root / 'bank' / 'complete.json')
    manifest = read(root / 'bank' / 'complete.json')
    if (manifest['source_hash'] != plan['hash']
            or tuple(manifest['scalar_names']) != SCALARS
            or tuple(manifest['level_names']) != LEVELS
            or manifest['files_sha256'] != certificate['bank_file_hashes']):
        raise ValueError('Feature bank manifest/certificate mismatch')
    for name in ('close_us.npy', 'scalar.npy', 'levels.npy'):
        hashes.verify(root / 'bank' / name, certificate['bank_file_hashes'][name])


def verify_session(spec, binding, hashes):
    if binding['day'] != spec['day']:
        raise ValueError('Session input day mismatch')
    execution = Path(spec['execution_root'])
    expected = binding['execution']
    hashes.verify(execution / 'receipt.json', expected['receipt_sha256'])
    receipt = read(execution / 'receipt.json')
    if (receipt['identity'] != expected['creator_identity']
            or receipt['source_fingerprint'] != expected['source_fingerprint']
            or receipt['sha256'] != expected['tape_sha256']):
        raise ValueError('Execution source identity mismatch')
    hashes.verify(execution / 'tape.pt', expected['tape_sha256'])
    verify_bank(spec['feature_root'], binding['feature_certificate'], hashes, day=spec['day'])
    hashes.verify(spec['identity_map'], binding['identity_map_sha256'])
    if read(spec['identity_map'])['bank_certificate_sha256'] != binding['feature_certificate']:
        raise ValueError('Identity mapping bank binding mismatch')
    hashes.verify(spec['split_certificate'], binding['split_certificate_sha256'])
    if spec.get('previous_feature_root'):
        verify_bank(spec['previous_feature_root'], binding['prior_certificate'], hashes,
                    before_day=spec['day'])
        hashes.verify(spec['previous_split_certificate'], binding['previous_split_certificate_sha256'])
    elif binding['prior_certificate'] is not None or binding['previous_split_certificate_sha256'] is not None:
        raise ValueError('Unexpected prior input binding')


def audit_inputs(root, *, include_validation=False):
    root = Path(root).resolve()
    started = time.time()
    if (root / 'owner.lock').exists():
        raise ValueError('Full byte audit requires released financial worker ownership')
    hashes = FreshHashes()
    identity_hash = hashes.verify(root / 'identity.json')
    identity = read(root / 'identity.json')
    prior_audit_hash = hashes.verify(root / 'audit.json')
    audit = read(root / 'audit.json')
    budget = identity['arguments']['generations']
    if (identity['arguments'].get('profile') or audit.get('status') != 'passed'
            or not audit.get('full_budget_verified')
            or audit['identity_sha256'] != identity_hash
            or audit['completed_generations_verified'] != budget
            or len(audit['generation_bindings']) != budget):
        raise ValueError('Fresh inputs require the bound full-budget training audit')
    checkpoint_hash = hashes.verify(root / 'checkpoint.json', audit['checkpoint_sha256'])
    if read(root / 'checkpoint.json')['next_generation'] != budget:
        raise ValueError('Full budget checkpoint mismatch')
    freeze_hash = None
    if (root / 'frozen_winner.json').exists():
        freeze_hash = hashes.verify(root / 'frozen_winner.json', audit['freeze_sha256'])
        if read(root / 'frozen_winner.json')['identity_sha256'] != identity_hash:
            raise ValueError('Frozen identity mismatch')
    if include_validation:
        if (freeze_hash is None or read(root / 'report.json').get('status') != 'completed'
                or audit.get('validation_sessions_verified') != 6):
            raise ValueError('Final input audit requires completed frozen evaluation and six-receipt financial audit')
        if len(audit.get('validation_bindings', [])) != 6:
            raise ValueError('Final financial audit must bind all six receipt hashes')
    spec = identity['sessions']
    if len(spec['training']) != 30 or len(spec['validation']) != 6:
        raise ValueError('Expected original all30/six session split')
    bindings = {}
    for index, item in enumerate(audit['generation_bindings']):
        expected = root / f'generation_{index:03d}' / 'generation.json'
        if Path(item['path']).resolve() != expected:
            raise ValueError('Training generation order/path mismatch')
        hashes.verify(expected, item['sha256'])
        generation = read(expected)
        if len(generation['receipts']) != 30:
            raise ValueError('Incomplete training generation receipts')
        for day_index, (session, receipt_item) in enumerate(zip(spec['training'], generation['receipts'])):
            path = expected.parent / f'session_{day_index:03d}' / 'receipt.json'
            if Path(receipt_item['path']).resolve() != path:
                raise ValueError('Training receipt order/path mismatch')
            hashes.verify(path, receipt_item['sha256'])
            receipt = read(path)
            binding = {key: receipt[key] for key in BINDINGS}
            if receipt['population_sha256'] != generation['population_sha256']:
                raise ValueError('Training population binding mismatch')
            if binding['day'] != session['day']:
                raise ValueError('Training day binding mismatch')
            if session['day'] in bindings and bindings[session['day']] != binding:
                raise ValueError('Training input binding changed across generations')
            bindings[session['day']] = binding
    for session in spec['training']:
        verify_session(session, bindings[session['day']], hashes)
    validated = 0
    if include_validation:
        for index, session in enumerate(spec['validation']):
            path = root / f'validation_{index:03d}' / 'receipt.json'
            item = audit['validation_bindings'][index]
            if Path(item['path']).resolve() != path:
                raise ValueError('Final receipt order/path mismatch')
            hashes.verify(path, item['sha256'])
            receipt = read(path)
            if receipt['freeze_sha256'] != freeze_hash:
                raise ValueError('Final input receipt freeze mismatch')
            verify_session(session, {key: receipt[key] for key in BINDINGS}, hashes)
            validated += 1
    report = dict(status='passed',kind='supplemental-consumed-input-byte-audit',
                  identity_sha256=identity_hash,training_audit_sha256=prior_audit_hash,
                  auditor_code_hash=code_hash(),simulation_code_hash=identity['code_hash'],
                  checkpoint_sha256=checkpoint_hash,freeze_sha256=freeze_hash,
                  training_sessions_verified=30,validation_sessions_verified=validated,
                  validation_opened=include_validation,files=list(hashes.files.values()),
                  started_epoch=started,finished_epoch=time.time(),
                  bytes_hashed=sum(item['bytes'] for item in hashes.files.values()))
    write_json(root / ('final_input_integrity.json' if include_validation else 'training_input_integrity.json'), report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--include-validation',action='store_true')
    args = parser.parse_args(argv)
    report = audit_inputs(args.output,include_validation=args.include_validation)
    print(json.dumps({key:value for key,value in report.items() if key != 'files'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
