"""Lossless receipt metrics and hash-bound views; legacy JSON remains readable."""
import base64
import hashlib
import json
import os
import zlib
from pathlib import Path

ENCODING='v66-zlib-json-v1'
VIEW='v66-metrics-view-v1'


def encode(path, value):
    if not isinstance(value,dict) or 'metrics' not in value or Path(path).name!='receipt.json':
        return value
    result=dict(value);metrics=result.pop('metrics')
    paired=result.get('paired_replay')
    if paired and Path(paired['receipt'])!=Path(path):
        if not paired.get('sha256'):raise ValueError('Unbound paired metrics')
        result['metrics_storage']=dict(format=VIEW,path=os.path.relpath(paired['receipt'],Path(path).parent),
            sha256=paired['sha256'],offset=paired['offset'],count=paired['count'])
    else:
        raw=json.dumps(metrics,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        result['metrics_storage']=dict(format=ENCODING,sha256=hashlib.sha256(raw).hexdigest(),
            data=base64.b64encode(zlib.compress(raw,level=3)).decode('ascii'))
    return result


def decode(path, value, depth=0):
    if not isinstance(value,dict) or 'metrics_storage' not in value:return value
    if depth>=8:raise ValueError('Receipt reference cycle or excessive depth')
    result=dict(value);ref=result.pop('metrics_storage')
    if 'metrics' in result:raise ValueError('Ambiguous metrics authority')
    if ref['format']==ENCODING:
        raw=zlib.decompress(base64.b64decode(ref['data'],validate=True))
        if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('Metrics payload integrity mismatch')
        metrics=json.loads(raw)
    elif ref['format']==VIEW:
        name=Path(ref['path'])
        if name.is_absolute():raise ValueError('Metrics view must be relative')
        source=Path(path).parent/name;raw=source.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('Metrics source integrity mismatch')
        original=decode(source,json.loads(raw),depth+1)
        start=ref['offset'];count=ref['count']
        if type(start)!=int or type(count)!=int or start<0 or count<0:raise ValueError('Invalid metrics slice')
        metrics={}
        for key,values in original['metrics'].items():
            if start+count>len(values):raise ValueError('Metrics slice outside source')
            metrics[key]=values[start:start+count]
    else:raise ValueError('Unknown metrics storage format')
    result['metrics']=metrics
    return result


def read(path):
    return decode(path,json.loads(Path(path).read_bytes()))
