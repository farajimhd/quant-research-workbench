"""Fail-closed V19 completion gate; reading this module never starts work."""
import json
from pathlib import Path
import psutil
from .run_search import digest
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def read(path):return json.loads(Path(path).read_bytes())


def alive(owner):
    try:return abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01
    except psutil.NoSuchProcess:return False


def process_identity(pointer):
    """Legacy pointers locate campaigns; process authority stays in active.json."""
    if 'pid' in pointer and 'creation_time' in pointer:return pointer
    if not pointer.get('campaign'):raise ValueError('Optimizer pointer lacks campaign/process authority')
    root=Path(pointer['campaign']);path=root/'active.json'
    if not path.exists():raise ValueError('Legacy optimizer process authority missing')
    identity=read(path)
    if 'pid' not in identity or 'creation_time' not in identity:raise ValueError('Legacy optimizer process identity missing')
    if (root/'owner.lock').exists():raise RuntimeError('Legacy optimizer still owns its campaign')
    return identity


def completion(job_path):
    """Follow the current job after migrations, never an obsolete campaign."""
    job=read(job_path);root=Path(job['campaign']);checkpoint=root/'checkpoint.json'
    waiting=dict(status='waiting',campaign=str(root),reason='Waiting for V19 successful completion of all 50 generations')
    if not checkpoint.exists():return waiting
    saved=read(checkpoint);target=saved['contract']['generations'];done=saved['state']['completed']
    if not saved['contract'].get('version','').startswith('v19-'):raise ValueError('Current V19 campaign required')
    waiting.update(completed_generations=done,total_generations=target)
    if target!=50:raise ValueError('V20 requires the approved 50-generation V19 campaign')
    if done!=target:return waiting
    if digest(saved['state'])!=saved['state_sha256']:raise ValueError('V19 checkpoint integrity changed')
    if (root/'owner.lock').exists() or not (root/'exit.json').exists():return waiting
    if read(root/'exit.json')['exit_code']!=0:return dict(waiting,reason='V19 did not exit successfully')
    active=read(root/'active.json')
    if alive(active):return dict(waiting,reason='Waiting for V19 controller to exit')
    status=read(root/'status.json')
    if status.get('status')!='complete' or status.get('completed_generations')!=50:return waiting
    if saved['contract']['source_sha256']!=job['source_sha256'] or active['source_sha256']!=job['source_sha256']:
        raise ValueError('V19 completed source does not match current job')
    seals={}
    for role in ('training','validation'):
        folder=root/role/'generation-0050';path=folder/'complete.json'
        if not path.exists():return waiting
        seal=read(path);days=saved['contract'][role]['days']
        if seal.get('validation_feedback') is not False or set(seal['session_receipts'])!=set(days):raise ValueError('V19 final split coverage/feedback changed')
        for day,h in seal['session_receipts'].items():
            if file_hash(folder/day/'receipt.json')!=h:raise ValueError('V19 final session receipt changed')
        if file_hash(folder/'session-panel.npz')!=seal['session_panel_sha256']:raise ValueError('V19 final panel changed')
        seals[role]=file_hash(path)
    # Worker pools join before lock release and exit publication. Also reject
    # independently advertised active optimizers.
    for version in range(7,20):
        pointer=root.parents[2]/f'torch_backtest_v{version}'/'active-campaign.json'
        if pointer.exists():
            owner=process_identity(read(pointer))
            if owner.get('pid')!=psutil.Process().pid and alive(owner):return dict(waiting,reason=f'Waiting for active optimizer V{version} to exit')
    return dict(status='ready',campaign=str(root),completed_generations=50,job_sha256=file_hash(job_path),
                checkpoint_sha256=file_hash(checkpoint),source_sha256=job['source_sha256'],seals=seals)


def require_complete(job_path,proof=None):
    if job_path is None:raise ValueError('Explicit current V19 job required; no early V20 execution')
    if proof is not None:
        job=read(job_path);root=Path(job['campaign'])
        if proof.get('status')!='ready' or file_hash(job_path)!=proof['job_sha256'] or str(root)!=proof['campaign']:raise ValueError('Pinned V19 completion changed')
        if (root/'owner.lock').exists() or alive(read(root/'active.json')) or read(root/'exit.json')['exit_code']!=0:raise RuntimeError('V19 completion ownership changed')
        if file_hash(root/'checkpoint.json')!=proof['checkpoint_sha256']:raise ValueError('V19 final checkpoint changed')
        for role,h in proof['seals'].items():
            if file_hash(root/role/'generation-0050'/'complete.json')!=h:raise ValueError('V19 final seal changed')
        return proof
    proof=completion(job_path)
    if proof['status']!='ready':raise RuntimeError(proof['reason'])
    return proof
