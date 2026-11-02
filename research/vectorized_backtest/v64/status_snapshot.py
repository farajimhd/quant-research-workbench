"""Small live snapshots reference immutable ranking payloads; readers restore the API."""
import json
from hashlib import sha256
from functools import lru_cache
from pathlib import Path
from .publication import write_json, _write_text

class StatusWriter:
    def __init__(self, root):
        self.root=Path(root);self.saved={}
    def compact(self, state):
        result=dict(state);refs={}
        for key in ('training_ranking','validation_ranking'):
            if key not in result:continue
            value=result.pop(key);prior=self.saved.get(key)
            if prior is None or prior[0] is not value:
                raw=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
                digest=sha256(raw.encode()).hexdigest();name='status-data/'+digest+'.json'
                path=self.root/name
                if not path.exists():_write_text(path,raw)
                prior=(value,dict(path=name,sha256=digest));self.saved[key]=prior
            refs[key]=prior[1]
        if refs:result['status_payloads']=refs
        return result

@lru_cache(maxsize=4)
def _payload(path,digest):
    raw=Path(path).read_bytes()
    if sha256(raw).hexdigest()!=digest:raise ValueError('Status payload integrity mismatch')
    return json.loads(raw)

def expand(path,state):
    if not isinstance(state,dict) or 'status_payloads' not in state:return state
    result=dict(state)
    for key,ref in result.pop('status_payloads').items():
        if key not in ('training_ranking','validation_ranking'):raise ValueError('Unknown status payload')
        name='status-data/'+ref['sha256']+'.json'
        if ref['path']!=name or len(ref['sha256'])!=64 or any(c not in '0123456789abcdef' for c in ref['sha256']):
            raise ValueError('Invalid status payload path')
        result[key]=_payload(str(Path(path).parent/name),ref['sha256'])
    return result
