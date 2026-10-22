"""Verified single-session V42 launch; immutable runtime job is authoritative."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,hashlib,json,sys,time,subprocess,shutil
from pathlib import Path
import psutil

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_bytes())
def live(owner):
    try:return abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01
    except (psutil.NoSuchProcess,KeyError):return False

def check(job):
    from .run_search import source_hash,digest
    if source_hash()!=job['source_sha256']:raise ValueError('Pinned V42 source changed')
    for k in ('training','initial_pool','baseline_initial_pool','tests_result','qualification'):
        if sha(job[k])!=job[k+'_sha256']:raise ValueError(k+' changed')
    pool=read(job['initial_pool']);old=read(job['baseline_initial_pool'])
    if len(pool['population'])!=6144 or digest([dict(p,version='v34-recurrent-v1') for p in pool['population']])!=old['population_sha256']:raise ValueError('Initial V34 programs changed')
    if pool['rng_before']!=old['rng_before'] or pool['rng_after']!=old['rng_after']:raise ValueError('Initial RNG changed')
    if pool['financial_results_imported'] is not False or pool['source_sha256']!=job['source_sha256']:raise ValueError('Initial provenance changed')
    tests=read(job['tests_result']);q=read(job['qualification'])
    if tests['exit_code'] or tests['source_sha256']!=job['source_sha256'] or sha(tests['output'])!=tests['output_sha256']:raise ValueError('CPU tests unverified')
    if q['source_sha256']!=job['source_sha256'] or not q['ready_for_run'] or sha(q['profile'])!=q['profile_sha256']:raise ValueError('Qualification changed')
    profile=read(q['profile'])
    if not all(profile.get(k) for k in ('exact_v34_captured_financial_parity','exact_batch_parity','exact_cold_warm_parity')):raise ValueError('GPU parity failed')
    if len(profile.get('newborn_fixture_cases',[]))!=8:raise ValueError('Mask operator fixtures missing')
    if Path(job['campaign']).exists():raise ValueError('Fresh campaign already exists; use audited checkpoint resume')
    for pointer in Path(job['root']).parent.glob('torch_backtest_v*/active-campaign.json'):
        if live(read(pointer)):raise RuntimeError('Competing optimizer: '+str(pointer))
    if psutil.virtual_memory().available<20*2**30:raise MemoryError('Laptop 12 GiB reserve + 8 GiB admission required')

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();job=read(a.job);root=Path(job['root'])
    check(job)
    from .windows_process import quiet_compilers
    quiet_compilers()
    probe=root/'initialization-probes/2026-08-04.npz';dest=Path(job['campaign'])/'initialization-probes/2026-08-04.npz'
    dest.parent.mkdir(parents=True);shutil.copy2(probe,dest)
    if sha(probe)!=sha(dest):raise ValueError('Probe copy mismatch')
    os.environ['V42_INITIAL_POOL']=job['initial_pool']
    # New console uses SW_SHOWNOACTIVATE: it remains an ordinary independent
    # window and never makes other applications modal or grabs focus.
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
    panel=subprocess.Popen([sys.executable.replace('pythonw.exe','python.exe'),'-B','-u','-m','research.vectorized_backtest.v42.local_details'],cwd=job['code'],creationflags=subprocess.CREATE_NEW_CONSOLE,startupinfo=startup)
    (root/'observer.json').write_text(json.dumps(dict(pid=panel.pid,creation_time=psutil.Process(panel.pid).create_time())))
    from .run_train import main as train
    code=train(['--training',job['training'],'--output',job['campaign'],'--mode','local','--day','2026-08-04','--authorize-run','--commit',job['commit'],'--population','1024','--initial-multiplier','6','--generations','50','--batch-size','1024','--seed','2020','--host-reserve-gib','12','--host-admission-gib','8'])
    (root/'launcher-exit.json').write_text(json.dumps(dict(exit_code=code,epoch=time.time())))
    return code
if __name__=='__main__':sys.exit(main())
