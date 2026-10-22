"""Atomic transport publication, restricted to the configured shared runtime."""
import json,os,time,uuid
from pathlib import Path

def publish(root,path,value):
    root,path=Path(root).absolute(),Path(path).absolute()
    if not path.is_relative_to(root):raise ValueError('Transport publication escaped its owned root')
    path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False),encoding='utf-8')
        for attempt in range(12):
            try:os.replace(temporary,path);return
            except PermissionError:
                if attempt==11:raise
                time.sleep(.01*(attempt+1))
    finally:
        if temporary.exists():temporary.unlink()
