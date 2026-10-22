"""Durable shared-runtime work queue. Claims never expire merely by elapsed time."""
import json,os,time,uuid,hashlib
from pathlib import Path
from .publication import write_json
from .distributed_assets import sha,copy_verified,remote

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def read(path):
    # SMB can briefly deny an open during atomic replacement. Never retry
    # malformed data or hide a persistent access failure.
    for attempt in range(12):
        try:return json.loads(Path(path).read_bytes())
        except PermissionError:
            if attempt==11:raise
            time.sleep(.01*(attempt+1))

class Bus:
    def __init__(self,job,node='laptop'):
        self.job=job;self.node=node
        canonical=str(job['transport']).replace('\\','/')
        if not canonical.startswith('D:/TradingML/runtimes/'):raise ValueError('Transport must be an owned runtime')
        self.root=Path(job['transport']) if node=='workstation' else remote(job['transport'],job['workstation_share'])
        for name in ('requests','history','claims','done','failed','heartbeats','progress','payloads','attempts'):(self.root/name).mkdir(parents=True,exist_ok=True)
    def write(self,path,value):
        from .distributed_storage import publish
        publish(self.root,path,value)
    def submit(self,request):
        key=digest(request);path=self.root/'requests'/(key+'.json')
        if path.exists():
            if read(path)!=request:raise ValueError('Immutable work request changed')
        else:self.write(path,request)
        return key
    def claim(self,owner):
        for path in sorted((self.root/'requests').glob('*.json'),key=lambda p:p.stat().st_mtime_ns):
            key=path.stem
            if (self.root/'done'/(key+'.json')).exists() or (self.root/'failed'/(key+'.json')).exists():continue
            request=read(path)
            allowed=self.job.get('machines',{}).get(self.node,{}).get('days')
            if allowed is not None and request.get('kind') in ('local','replay') and request['day'] not in allowed[request.get('role','training')]:continue
            if request.get('node') not in (None,self.node):continue
            if request.get('blocked',False):continue
            affinity=self.root/'attempts'/(key+'-affinity.json')
            if affinity.exists() and read(affinity)['node']!=self.node:continue
            claim=self.root/'claims'/(key+'.json')
            try:claim.mkdir()
            except FileExistsError:continue
            self.write(claim/'owner.json',dict(owner,node=self.node,request_sha256=key,epoch=time.time()))
            if digest(request)!=key:raise ValueError('Work request hash mismatch')
            return key,request
        return None
    def recover_own_claims(self,identity_live):
        for path in (self.root/'claims').glob('*.json'):
            if not (path/'owner.json').exists():raise RuntimeError('Incomplete claim requires ownership audit: '+str(path))
            owner=read(path/'owner.json')
            if owner['node']!=self.node or (self.root/'done'/path.name).exists():continue
            if identity_live(owner):raise RuntimeError('Prior exact worker still owns '+path.stem)
            # Recovery stays on the same host: its original checkpoint/cache are
            # authoritative. A different host cannot silently restart this unit.
            request=read(self.root/'requests'/path.name)
            request_node=request.get('node')
            if request_node is None:
                # Store host affinity separately; immutable request stays intact.
                self.write(self.root/'attempts'/(path.stem+'-affinity.json'),dict(node=self.node))
            path.replace(self.root/'attempts'/(path.stem+'-'+uuid.uuid4().hex+'.json'))
    def retire(self,key):
        path=self.root/'requests'/(key+'.json')
        if path.exists():path.replace(self.root/'history'/path.name)
    def result(self,key):
        failure=self.root/'failed'/(key+'.json')
        if failure.exists():raise RuntimeError('Distributed unit failed: '+str(failure)+' '+str(read(failure)))
        path=self.root/'done'/(key+'.json')
        if not path.exists():return None
        value=read(path)
        if value['request_sha256']!=key:raise ValueError('Completion identity changed')
        return value
    def payload(self,payload):
        key=digest(payload);path=self.root/'payloads'/(key+'.json')
        if not path.exists():self.write(path,payload)
        return key
    def mirror(self,node,path,expected):
        if node=='laptop':
            if sha(path)!=expected:raise ValueError('Local result changed')
        else:copy_verified(remote(path,self.job['workstation_share']),path,expected)
