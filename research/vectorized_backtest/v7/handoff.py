"""Explicit, qualified successor checkpoint; never mutate a stopped campaign."""
import copy,json,shutil
from pathlib import Path
from .run_search import source_hash,digest,VERSION
from .diversity import VERSION as DIVERSITY_VERSION
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash


def successor(old,new,qualification):
    old=Path(old);new=Path(new);qualification=Path(qualification)
    if new.exists():raise ValueError('Successor destination must be new')
    if (old/'owner.lock').exists() or (old/'controller.lock').exists():raise ValueError('Old campaign still owned')
    saved=json.loads((old/'checkpoint.json').read_text());q=json.loads(qualification.read_text())
    if json.loads((old/'exit.json').read_text())['exit_code']!=130:raise ValueError('Expected generation-boundary stop')
    if not saved['completed_generations']:raise ValueError('No sealed generation')
    if q.get('status')!='passed' or not q.get('exact_metrics') or not q.get('full_session') or q.get('validation_opened') is not False:
        raise ValueError('Full-session qualification required')
    if q['source_sha256']!=source_hash() or q['old_source_sha256']!=saved['contract']['source_sha256']:
        raise ValueError('Source qualification mismatch')
    if q['checkpoint_sha256']!=file_hash(old/'checkpoint.json') or q['population_sha256']!=saved['population_sha256']:
        raise ValueError('Checkpoint qualification mismatch')
    if digest(saved['population'])!=saved['population_sha256']:raise ValueError('Population corrupted')
    for key in ('batch_size','session_workers','execution'):
        if q[key]!=saved['contract'][key]:raise ValueError('Qualification configuration mismatch')
    if q['candidates']!=saved['contract']['population']:raise ValueError('Qualification population mismatch')
    if q.get('diversity_version') and (q['diversity_version']!=DIVERSITY_VERSION or not q.get('no_slowdown')):
        raise ValueError('Diversity performance qualification required')
    verified={}
    for generation in range(1,saved['completed_generations']+1):
        root=old/f'generation-{generation:04d}';seal=json.loads((root/'complete.json').read_text())
        if seal.get('validation_opened') is not False or seal['training_days']!=list(saved['contract']['input_receipts']):
            raise ValueError('Generation training identity changed')
        for day,checksum in seal['session_receipts'].items():
            if file_hash(root/day/'receipt.json')!=checksum:raise ValueError('Session seal mismatch')
        verified[root.name]=file_hash(root/'complete.json')
    if verified[f"generation-{saved['completed_generations']:04d}"]!=saved['last_generation_sha256']:
        raise ValueError('Generation checkpoint mismatch')
    require_runtime(new)
    for name in verified:
        shutil.copytree(old/name,new/name)
        for path in (old/name).rglob('*'):
            if path.is_file() and file_hash(path)!=file_hash(new/path.relative_to(old)):raise ValueError('Successor copy mismatch')
    migrated=copy.deepcopy(saved)
    migrated['contract']['source_sha256']=q['source_sha256']
    migrated['contract']['session_execution']='independent'
    changes=['contract.source_sha256','contract.session_execution']
    if q.get('diversity_version'):
        migrated['contract']['version']=VERSION
        migrated['contract']['diversity']=DIVERSITY_VERSION
        changes.extend(['contract.version','contract.diversity'])
    if (old/'status.json').exists():
        snapshot=json.loads((old/'status.json').read_text())
        if snapshot.get('completed_generations')!=saved['completed_generations'] or snapshot.get('validation_opened') is not False:
            raise ValueError('Resume panel does not match completed generation')
        write_json(new/'resume-panel.json',snapshot)
    write_json(new/'handoff.json',dict(old_campaign=str(old),old_checkpoint_sha256=q['checkpoint_sha256'],
        qualification_sha256=file_hash(qualification),qualification=str(qualification),generation_seals=verified,
        changes=changes,population_sha256=saved['population_sha256'],
        completed_generations=saved['completed_generations'],validation_opened=False))
    write_json(new/'checkpoint.json',migrated)
    return migrated
