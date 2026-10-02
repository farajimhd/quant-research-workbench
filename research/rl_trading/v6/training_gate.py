"""Fail-closed full train/development audit gate; sealed test stays absent."""
import json
from pathlib import Path
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.prepare_training import VERSION
from research.rl_trading.v6.split import TRAIN, DEVELOPMENT
from research.rl_trading.v6.teacher_data import VERSION as TEACHER_VERSION


def require_dataset(certificate, *, runtime_root):
    from research.rl_trading.v6.opportunity_dataset import require_dataset as current
    return current(certificate, runtime_root=runtime_root)


def _require_legacy_dataset_for_historical_audit(certificate, *, runtime_root):
    runtime = Path(runtime_root).resolve()
    certificate = Path(certificate).resolve()
    if not runtime.is_dir() or not certificate.is_relative_to(runtime):
        raise ValueError('Training certificate must be under configured runtime')
    data = json.loads(certificate.read_text())
    if (data.get('version')!=VERSION or data.get('status')!='audited_ready_for_training' or
        data.get('sealed_test_accessed') is not False or
        digest({k:v for k,v in data.items() if k!='hash'})!=data.get('hash') or
        data.get('rank_exclusion_policy')!='ignore_entire_entry_and_recompile_account'):
        raise ValueError('Full V6 dataset audit has not passed')
    days = data.get('days',[])
    if [entry['day'] for entry in days]!=[str(d) for d in TRAIN+DEVELOPMENT]:
        raise ValueError('Training requires all 16 training and two development days exactly')
    selected = str(data['ranking']['top_r'])
    selection=data.get('rank_selection',{'policy':'minimum_per_day_coverage'})
    if selection.get('policy')=='explicit_fixed_rank':
        if (selection.get('rank')!=data['ranking']['top_r'] or selection.get('rank',0)<1 or
                selection.get('coverage_requirement')!='report_only_user_accepted_exclusions'):
            raise ValueError('Explicit universe selection differs from audited configuration')
    elif selection.get('policy')!='minimum_per_day_coverage':
        raise ValueError('Unknown audited universe selection policy')
    for entry in days:
        for key in ('bank_root','previous_root','teacher_root','original_teacher_root','coverage_certificate'):
            if not Path(entry[key]).resolve().is_relative_to(runtime):
                raise ValueError('Dataset input escaped runtime')
        checks = ((Path(entry['bank_root'])/'complete.json',entry['bank_certificate_sha256']),
                  (Path(entry['teacher_root'])/'complete.json',entry['teacher_sha256']),
                  (Path(entry['original_teacher_root'])/'complete.json',entry['original_teacher_sha256']),
                  (Path(entry['coverage_certificate']),entry['coverage_sha256']))
        if any(file_hash(p)!=expected for p,expected in checks):
            raise ValueError('Audited dataset input certificate changed')
        report = json.loads(Path(entry['coverage_certificate']).read_text())
        if report['binding']['bank_certificate']!=entry['bank_certificate_sha256'] or report['binding']['teacher_certificate']!=entry['original_teacher_sha256']:
            raise ValueError('Coverage audit/source binding differs')
        if selected not in report['report']['coverage']:
            raise ValueError('Selected universe was not audited')
        if (selection['policy']=='minimum_per_day_coverage' and entry['role']=='train' and
                report['report']['coverage'][selected]['fraction']<data['minimum_entry_coverage']):
            raise ValueError('Chosen universe fails required per-day coverage')
        cert = json.loads((Path(entry['teacher_root'])/'complete.json').read_text())
        original = json.loads((Path(entry['original_teacher_root'])/'complete.json').read_text())
        for name in ('decisions','outcomes','positions'):
            if file_hash(Path(entry['original_teacher_root'])/f'{name}.parquet')!=original[f'{name}_sha256']:
                raise ValueError('Original audited teacher file changed')
        if (cert.get('version')!=TEACHER_VERSION or original.get('version')!=TEACHER_VERSION or
                cert.get('status')!='audited_price_action_teacher' or
                cert.get('ranking')!=data['ranking'] or cert.get('execution_evidence')!='none' or
                cert.get('bank_certificate_sha256')!=entry['bank_certificate_sha256'] or
                cert.get('coverage_certificate_sha256')!=entry['coverage_sha256']):
            raise ValueError('Prepared teacher does not follow audited ranking/price-action contract')
        for name in ('decisions','outcomes','positions'):
            if file_hash(Path(entry['teacher_root'])/f'{name}.parquet')!=cert[f'{name}_sha256']:
                raise ValueError('Prepared teacher file changed')
        expected_role = 'train' if entry['day'] in set(map(str,TRAIN)) else 'development'
        if entry['role']!=expected_role:
            raise ValueError('Forward training/development role changed')
    return data
