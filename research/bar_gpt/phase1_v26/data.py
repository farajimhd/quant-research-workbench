import json
from pathlib import Path
import torch
from ..phase1_v25.data import load as load_data
from ..phase1_v12.data import load_teacher
from ..v5.shards.phase1_contract import file_hash


def parents(root):
    pp=root/'phase1-v24-xl-20261122/e05-w384-d6-s559/checkpoint_last.pt'
    ap=root/'phase1-v25-family-mask-20261122/e05-activity-light-s559/checkpoint_last.pt'
    packs={};identity={}
    for key,path,fields in [('price',pp,('model','architecture','parent_template')),('activity',ap,('model','calibration','kind','seed'))]:
        result=json.loads((path.parent/'result.json').read_text());sha=file_hash(path)
        if sha!=result['checkpoint_sha']:raise ValueError('Changed '+key+' parent')
        s=torch.load(path,map_location='cpu',weights_only=False);packs[key]={k:s[k] for k in fields}
        identity[key]=dict(path=str(path),sha=sha,data_hash=s['contract']['data_hash'])
    if identity['price']['data_hash']!=identity['activity']['data_hash']:raise ValueError('Different parent data')
    return packs,identity


def load(root):
    train,dev,cal,provenance=load_data(root);packs,identity=parents(root)
    if identity['price']['data_hash']!=provenance['data_hash']:raise ValueError('Parent data differs')
    teacher,proof=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',provenance['split_hash'],'cuda')
    with torch.no_grad():
        def anchors(data):
            return (*data,torch.cat([teacher.encode(data[0][i:i+512],data[1][i:i+512]) for i in range(0,len(data[0]),512)]))
        train=anchors(train);dev=anchors(dev)
    del teacher
    return train,dev,packs,dict(provenance,parents=identity,anchor_teacher=proof['checkpoint_sha256'])
