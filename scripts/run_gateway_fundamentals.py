"""One bounded source-commit-driven publication pass, scheduled on workstation."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,platform,subprocess,hashlib
from datetime import datetime,timezone,timedelta
from scripts.run_fundamentals import lock,save
from pipelines.certified_research.contracts import digest


def command(request, root):
    population=Path(request['population'])
    if hashlib.sha256(population.read_bytes()).hexdigest()!=request['population_sha256']:
        raise ValueError('Reviewed forward population changed; explicit request migration required')
    result=[sys.executable,'-B',str(Path(__file__).with_name('run_fundamentals.py')),'forward-fill',
        '--selected-v1','--execute','--population',str(population),'--run-dir',str(root/'publication'),
        '--endpoint',request['endpoint'],'--workers',str(request.get('workers',4))]
    if not (root/'publication'/'watermark.json').exists():
        result+=['--since-recorded',request['since_recorded']]
    return result


def run(root):
    root=Path(root).resolve()
    if platform.node().upper()!='DESKTOP-SAAI85T':
        raise ValueError('Gateway fundamentals publication is workstation-owned')
    if not root.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError('Controller must use the operational runtime root')
    with lock(root/'controller.lock'):
        if (root/'STOP').exists():raise ValueError('Controller STOP present')
        request=json.loads((root/'controller-request.json').read_text(encoding='utf-8-sig'))
        signals={s:json.loads((root/(s+'-commit.json')).read_text()) for s in ('sec','reference')
                 if (root/(s+'-commit.json')).exists()}
        state_file=root/'controller-state.json'
        prior=json.loads(state_file.read_text()) if state_file.exists() else {}
        from research.mlops.env import load_env_files
        from research.mlops.clickhouse import discover_clickhouse_env_files
        from scripts.run_fundamentals import Client
        from pipelines.fundamentals.adapters import ADAPTERS
        load_env_files(discover_clickhouse_env_files(),verbose=False)
        client=Client(request['endpoint'])
        try:
            # Cheap metadata backstop: a failed notification or process crash
            # cannot hide a committed source. No historical fact fetch.
            tables=','.join("'"+a.table+"'" for a in ADAPTERS.values())
            source_state=client.query('SELECT database,table,sum(rows) rows,max(modification_time) changed '
                'FROM system.parts WHERE active AND concat(database,\'.\',table) IN ('+tables+') '
                'GROUP BY database,table ORDER BY database,table')
        finally:client.close()
        fdic=request.get('fdic')
        day=datetime.now(timezone.utc).date().isoformat()
        source_signature=digest((signals,request,source_state))
        signature=digest((source_signature,day if fdic else None))
        if prior.get('state')=='complete' and prior.get('signature')==signature:return 0
        if prior.get('state')=='failed' and datetime.now(timezone.utc)<datetime.fromisoformat(prior['retry_after']):return 1
        state=dict(state='running',signature=signature,pid=os.getpid(),started_at=datetime.now(timezone.utc).isoformat(),
                   fdic_completed_day=prior.get('fdic_completed_day'),
                   publication_signature=prior.get('publication_signature'),publication_phase=prior.get('publication_phase'))
        save(state_file,state)
        try:
            with (root/'controller.log').open('a',encoding='utf-8') as output:
                if prior.get('publication_signature')!=source_signature:
                    result=subprocess.run(command(request,root),stdout=output,stderr=subprocess.STDOUT,
                        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8'),check=False)
                    if result.returncode:raise ValueError('Incremental publisher failed; see publication/status.json')
                    state['publication_signature']=source_signature
                    state['publication_phase']='complete'
                    save(state_file,state)
                if fdic and prior.get('fdic_completed_day')!=day:
                    from services.reference_gateway.fdic_maintenance import refresh
                    state['fdic']=refresh(request,root,day,output)
                    state['fdic_completed_day']=day
            state.update(state='complete',finished_at=datetime.now(timezone.utc).isoformat())
            state.setdefault('fdic_completed_day',prior.get('fdic_completed_day'))
        except Exception as error:
            state.update(state='failed',error=str(error),retry_after=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat())
            save(state_file,state);raise
        save(state_file,state)
        return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-dir',required=True)
    raise SystemExit(run(parser.parse_args().run_dir))
