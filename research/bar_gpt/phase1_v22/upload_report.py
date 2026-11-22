"""Upload scalar comparison/audit results only; verify no checkpoint artifacts."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,math,time
from pathlib import Path
import wandb
from .compare import summarize
from research.mlops.env import discover_env_files,load_env_files
from ..v5.shards.phase1_contract import write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--profile',type=Path);a=p.parse_args()
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    rows=json.loads((a.output/'comparison.json').read_text());baseline=json.loads((a.output/'baseline-metrics.json').read_text())
    audit=json.loads((a.output/'development-transform-audit.json').read_text());api=wandb.Api();receipts=[]
    profile=json.loads(a.profile.read_text()) if a.profile else None
    for row in rows:
        path='mehdifaraji/bargpt-phase01-v22-capacity/'+row['wandb_url'].rsplit('/',1)[-1];run=api.run(path)
        if run.state!='finished':raise ValueError('Run not finished: '+path)
        values={'report/'+k:v for k,v in summarize(row['name'],row['metrics']).items() if k!='name'}
        values.update({'audit/'+k:v for k,v in audit.items() if isinstance(v,(int,float))})
        for name,metrics in baseline.items():values.update({'baseline/'+name+'/'+k:v for k,v in metrics.items()})
        diag=a.output/row['name']/'diagnostics-v3.json'
        if diag.exists():values.update({'diagnostic/'+k:v for k,v in json.loads(diag.read_text()).items() if isinstance(v,(int,float))})
        if profile and profile['checkpoint_sha']==row['checkpoint_sha']:
            for entry in profile['results']:
                values.update({f"profile/batch_{entry['batch']}/"+k:v for k,v in entry.items() if isinstance(v,(int,float))})
        run.summary.update(values)
        artifacts=list(run.logged_artifacts());files=[f.name for f in run.files()]
        if artifacts or any(name.endswith(('.pt','.pth','.ckpt','.safetensors')) for name in files):raise ValueError('Unexpected checkpoint/artifact upload')
        for attempt in range(8):
            refreshed=wandb.Api().run(path);actual=refreshed.summary.get('report/close_1s')
            if actual is not None and math.isclose(actual,values['report/close_1s'],rel_tol=1e-12):break
            time.sleep(2)
        else:raise ValueError('Remote summary readback mismatch')
        receipts.append(dict(id=run.id,state=refreshed.state,last_step=refreshed.summary.get('_step'),checkpoint_artifacts=0,checkpoint_files=0,report_close_1s=values['report/close_1s']))
        print(receipts[-1],flush=True)
    write_json(a.output/'wandb-readback.json',receipts)


if __name__=='__main__':main()
