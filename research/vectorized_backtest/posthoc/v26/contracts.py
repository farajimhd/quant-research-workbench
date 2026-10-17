"""Fail-closed completion and artifact bindings; no engine/GPU imports."""
import hashlib,json,os
from pathlib import Path
import psutil
from contextlib import contextmanager


def read(path):return json.loads(Path(path).read_bytes())
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):h.update(block)
    return h.hexdigest()
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False),encoding='utf-8');temporary.replace(path)
def alive(owner):
    try:return abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01
    except psutil.NoSuchProcess:return False


def completed(pointer):
    pointer=Path(pointer)
    if not pointer.exists():return None
    owner=read(pointer);root=Path(owner['campaign'])
    if alive(owner) or (root/'owner.lock').exists() or (root/'global/owner.lock').exists():return None
    if not (root/'exit.json').exists() or read(root/'exit.json').get('exit_code')!=0:return None
    cp=root/'global/checkpoint.json'
    if not cp.exists():return None
    saved=read(cp);contract=saved['contract'];state=saved['state']
    if state['completed']!=50 or contract['generations']!=50:return None
    if not contract['version'].startswith('v26-') or contract['validation_feedback'] is not False:
        raise ValueError('Completed V26 with display-only validation required')
    if contract['source_sha256']!=owner['source_sha256'] or digest(state)!=saved['state_sha256']:
        raise ValueError('Final source/checkpoint identity changed')
    roles={};proof={}
    for role,count in [('training',24),('validation',12)]:
        folder=root/'global'/role/'generation-0050';seal=read(folder/'complete.json');days=contract[role]['days']
        if len(days)!=count or set(days)!=set(seal['session_receipts']) or seal['validation_feedback'] is not False:
            raise ValueError('Final split coverage changed')
        if file_hash(folder/'session-panel.npz')!=seal['session_panel_sha256']:raise ValueError('Final panel changed')
        for day,h in seal['session_receipts'].items():
            if file_hash(folder/day/'receipt.json')!=h:raise ValueError('Final receipt changed')
        roles[role]=dict(folder=str(folder),seal=seal,days=days)
        proof[role]=file_hash(folder/'complete.json')
    if roles['validation']['seal'].get('display_only') is not True:raise ValueError('Validation display contract changed')
    return dict(owner=owner,checkpoint=str(cp),checkpoint_sha256=file_hash(cp),saved=saved,roles=roles,seals=proof)


def ensure_idle(pointer):
    p=Path(pointer)
    if p.exists():
        owner=read(p);root=Path(owner['campaign'])
        if alive(owner) or (root/'owner.lock').exists() or (root/'global/owner.lock').exists():
            raise InterruptedError('Optimizer is active; analysis yields')


def memory_admission():
    if psutil.virtual_memory().available<160*1024**3:raise InterruptedError('Waiting for 128 GiB reserve +32 GiB allowance')


@contextmanager
def analysis_owner(output):
    import uuid
    output=Path(output);output.mkdir(parents=True,exist_ok=True);lock=output/'analysis.lock'
    if lock.exists():
        old=read(lock)
        if alive(old):raise InterruptedError('Another occupancy analyzer owns the output')
        lock.rename(output/f"stale-lock-{old['pid']}-{uuid.uuid4().hex}.json")
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise InterruptedError('Another occupancy analyzer acquired the output') from None
    owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time())
    with os.fdopen(fd,'w') as stream:json.dump(owner,stream)
    try:yield
    finally:
        if lock.exists() and read(lock)==owner:lock.rename(output/f"released-lock-{owner['pid']}-{uuid.uuid4().hex}.json")
