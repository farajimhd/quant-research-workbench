"""Reference-owned four-bank source maintenance, outside SEC filing workers."""
from datetime import date,timedelta
from pathlib import Path
import json,subprocess,sys


def refresh(request,root,day,output):
    scripts=Path(__file__).resolve().parents[2]/'scripts'
    start=(date.fromisoformat(day)-timedelta(days=7)).isoformat()
    inventory=root/'fdic'/day/'inventory.json';inventory.parent.mkdir(parents=True,exist_ok=True)
    acquire=[sys.executable,'-B',str(scripts/'acquire_fdic_reports.py'),
        '--source-root',request['fdic']['source_root'],'--inventory-file',str(inventory),
        '--start',start,'--end',day,'--changed-since',start+'T00:00:00+00:00',
        '--allow-acquisition','--allow-revisions']
    acquired=subprocess.run(acquire,stdout=output,stderr=subprocess.STDOUT,check=False)
    if acquired.returncode:raise ValueError('FDIC maintenance acquisition failed')
    if json.loads(inventory.read_text()):
        publish=[sys.executable,'-B',str(scripts/'run_fdic_fundamentals.py'),
            '--source-root',request['fdic']['source_root'],'--inventory',str(inventory),
            '--run-dir',str(root/'fdic'/day/'publication'),'--endpoint',request['endpoint'],
            '--workers','2','--execute']
        published=subprocess.run(publish,stdout=output,stderr=subprocess.STDOUT,check=False)
        if published.returncode:raise ValueError('FDIC publication needs review; completed receipts retained')
    return dict(day=day,reports=len(json.loads(inventory.read_text())))
