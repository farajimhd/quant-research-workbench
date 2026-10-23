"""Wait for the current campaign to drain, then retry a sealed blocked-only set."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,subprocess,time,shutil
from datetime import datetime,timezone,date
import psutil
from scripts.run_fundamentals import lock,save
from pipelines.fundamentals.retry import select,refresh
from pipelines.certified_research.contracts import digest


def owner_alive(owner):
    try:return abs(psutil.Process(owner['pid']).create_time()-owner['created'])<0.01
    except psutil.NoSuchProcess:return False


def require_drained(source,owners):
    if any(owner_alive(owner) for owner in owners):return False
    status=json.loads((source/'status.json').read_text())
    completion=json.loads((source/'completion.json').read_text())
    if status['state'] not in ('complete','blocked') or completion.get('full_scope') is not True:
        raise ValueError('Source did not finish normally; retry requires diagnosis')
    if datetime.fromisoformat(status['updated_at']).timestamp()<max(o['created'] for o in owners):
        raise ValueError('Source completion predates monitored owners')
    if status['counts'].get('failed') or status.get('active') or status.get('queued'):
        raise ValueError('Source has failures or undrained work')
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--population',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--profile-source',type=Path,required=True)
    p.add_argument('--family-verification',type=Path,required=True);p.add_argument('--owners',type=Path,required=True)
    p.add_argument('--refresh-blocked',action='store_true',help='After source drain, seal all remaining blocked census units including newly encountered blockers')
    p.add_argument('--check-only',action='store_true');a=p.parse_args()
    runtime=Path('D:/TradingML/runtimes').resolve();root=a.run_dir.resolve();code=Path(__file__).resolve().parents[1]
    if not root.is_relative_to(runtime) or not runtime.is_dir():raise ValueError('Required runtime unavailable')
    m=json.loads(a.manifest.read_text());source=Path(m['source_run_dir']);owners=json.loads(a.owners.read_text())
    if not owners or any(set(o)!= {'pid','created'} for o in owners):raise ValueError('Bounded owner identities required')
    request=json.loads((source/'request.json').read_text());population=json.loads(a.population.read_text(encoding='utf-8-sig'))
    def selection():return select(m,population_hash=digest(population),start=date.fromisoformat(request['start']),end=date.fromisoformat(request['end']),runtime=runtime)
    documents,preserved=selection()
    if a.check_only:
        print(json.dumps(dict(retry_targets=len(documents),preserved=len(preserved),owners_alive=[owner_alive(o) for o in owners])));return 0
    root.mkdir(parents=True,exist_ok=True)
    with lock(root/'retry-controller.lock'):
        def state(phase,**extra):save(root/'retry-controller-state.json',dict(phase=phase,pid=os.getpid(),updated_at=datetime.now(timezone.utc).isoformat(),**extra))
        try:
            while not require_drained(source,owners):
                state('waiting_for_existing_owner',targets=len(documents),owners=owners)
                if (root/'STOP').exists():raise InterruptedError('Retry stopped before publication')
                time.sleep(30)
            # Check the shared OS lock as well as PID+creation identity. Each
            # child acquires this same lock again and fails closed on a race.
            with lock(runtime/'qualified-fundamentals.lock'):
                if a.refresh_blocked:
                    m=refresh(m);a.manifest=root/'expanded-retry-manifest.json';save(a.manifest,m)
                documents,preserved=selection()
            if (root/'STOP').exists():raise InterruptedError('Retry stopped before publication')
            profile=root/'profile';profile.mkdir(exist_ok=True);(profile/'receipts').mkdir(exist_ok=True)
            if not (profile/'request.json').exists():
                shutil.copyfile(a.profile_source/'request.json',profile/'request.json')
                for f in (a.profile_source/'receipts').glob('*.json'):
                    if len(f.stem)==64:shutil.copyfile(f,profile/'receipts'/f.name)
            base=[sys.executable,'-B',str(code/'scripts/run_qualified_fundamentals.py'),'--population',str(a.population),'--workers','8',
                  '--start',request['start'],'--end',request['end']]
            with (root/'retry-build.log').open('a',encoding='utf-8') as log:
                def run(phase,args):
                    if (root/'STOP').exists():raise InterruptedError('Retry stopped before publication')
                    state(phase,targets=len(documents));result=subprocess.run(base+args,cwd=code,env=os.environ,stdout=log,stderr=subprocess.STDOUT)
                    return result.returncode
                result=run('profile_readback',['--run-dir',str(profile),'--profile','--upgrade-recipe'])
                if result:raise ValueError('Retry recipe profile failed; inspect retry-build.log')
                # Exercise the newly supported HTML path, not only the legacy
                # XBRL profile. This happens only after the original owner drains.
                from pipelines.certified_research.sec_html_reviewed import plan_for
                html_targets=[row for row in documents if plan_for(row['content_sha256'],row['cik'])]
                html_targets.sort(key=lambda row: plan_for(row['content_sha256'],row['cik'])['facts'][0]['locator']['kind']!='cells')
                if html_targets:
                    selected={k:v for k,v in m.items() if k not in ('documents','manifest_hash')}
                    selected['documents']=html_targets[:3];selected['manifest_hash']=digest(selected)
                    pilot_manifest=root/'html-profile-manifest.json';save(pilot_manifest,selected)
                    result=run('html_source_publication_and_reuse',['--run-dir',str(root/'html-profile'),'--profile',
                        '--retry-manifest',str(pilot_manifest)])
                    if result:raise ValueError('HTML source publication/readback profile failed; full retry has not started')
                args=['--run-dir',str(root),'--retry-manifest',str(a.manifest),'--profile-result',str(profile/'completion.json'),
                      '--family-verification',str(a.family_verification)]
                result=run('retry_plan',args+['--plan-only'])
                if result:raise ValueError('Retry plan failed')
                # Reuse the existing visible progress task after its old writer
                # has exited. No additional progress terminals are created.
                script=code/'scripts/monitor_fundamentals.py'
                monitor=f"Stop-ScheduledTask -TaskName 'Fundamentals-Progress-20261022'; $a=New-ScheduledTaskAction -Execute '{sys.executable}' -Argument '-B {script} --run-dir {root}' -WorkingDirectory '{code}'; Set-ScheduledTask -TaskName 'Fundamentals-Progress-20261022' -Action $a | Out-Null; Start-ScheduledTask -TaskName 'Fundamentals-Progress-20261022'"
                subprocess.run(['powershell','-NoProfile','-Command',monitor],check=True,stdout=log,stderr=subprocess.STDOUT)
                result=run('retry_publication',args)
            state('finished',exit_code=result)
            return result
        except BaseException as error:
            state('failed',error=str(error));raise


if __name__=='__main__':raise SystemExit(main())
