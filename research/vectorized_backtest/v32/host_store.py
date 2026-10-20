"""Process-owned, bounded, disk-backed copies of verified CPU preparation.

The first read still executes every producer certificate/hash/causality check.
Only that process may reuse the resulting template. Source and derived files
are stat-checked before every borrow, just like resident SessionData.activate.
There is no persisted pickle, cross-process trust, or financial-result cache.
"""
from collections import OrderedDict
from copy import copy
from pathlib import Path
import shutil
import uuid
import numpy as np
import torch


def stamp(path):
    stat=Path(path).stat()
    return stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns


class HostStore:
    def __init__(self,root,maximum_bytes=128*2**30):
        self.root=Path(root)/uuid.uuid4().hex
        self.maximum_bytes=maximum_bytes;self.bytes=0;self.entries=OrderedDict()
        self.hits=0;self.misses=0

    def metadata(self,day):
        if day not in self.entries:return None
        template,files,folder,size=self.entries[day]
        for name,expected in files.items():
            if stamp(name)!=expected:raise ValueError('Verified host preparation input/cache changed: '+name)
        return template

    def get(self,day):
        template=self.metadata(day)
        if template is None:return None
        self.hits+=1;self.entries.move_to_end(day)
        item=self._borrow(template)
        # Fault each pageable tensor page on the CPU loader, while another
        # session replays. Mappings alone would defer all I/O to GPU staging.
        for group in (item.host_tensors,item.resident_features.host):
            for value in group.values():
                value.numpy().view(np.uint8).reshape(-1)[::4096].sum(dtype=np.uint64)
        return item

    @staticmethod
    def _borrow(template):
        def reopen(group):
            return {k:torch.from_numpy(np.load(path,mmap_mode='c',allow_pickle=False)) if tensor else np.load(path,mmap_mode='c',allow_pickle=False)
                    for k,(path,tensor) in group.items()}
        item=copy(template);item.host_tensors=reopen(template._prepared_paths['thin']);item.tensors=item.host_tensors
        item.market=dict(template.market);item.market.update(reopen(template._prepared_paths['market']))
        # Reopen authoritative maps for this borrow only. Retaining mmap objects
        # in metadata would keep visited source pages in the process working set.
        for group in ('arrays','bank'):
            if group in template._source_paths:
                setattr(item,group,{k:np.load(path,mmap_mode='r',allow_pickle=False)
                                    for k,path in template._source_paths[group].items()})
        for name,path in template._source_paths.get('optional',{}).items():
            setattr(item,name,np.load(path,mmap_mode='r',allow_pickle=False))
        bank=copy(template.resident_features);bank.host=reopen(template._prepared_paths['resident'])
        bank.tensors=bank.host;bank.column_plans={};item.resident_features=bank
        if item.feature_cache is not None:
            item.feature_cache=copy(item.feature_cache)
            item.feature_cache.cache=OrderedDict();item.feature_cache.bytes=0
            item.feature_cache.verified=set(item.feature_cache.verified)
        return item

    def put(self,day,item):
        if day in self.entries:raise ValueError('Duplicate prepared session')
        if item.device.type!='cpu':raise ValueError('Only CPU preparation can be cached')
        self.misses+=1
        # Originals already mapped from certified files require no extra copy.
        size=sum(v.numel()*v.element_size() for group in (item.host_tensors,item.resident_features.host) for v in group.values())
        size+=sum(v.nbytes for v in item.market.values() if isinstance(v,np.ndarray) and not isinstance(v,np.memmap))
        # Never unlink a mapping that an overlapping replay/prefetch may own.
        # Once full, later sessions keep the original verified loading path.
        if self.bytes+size>self.maximum_bytes:return False
        self.root.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(self.root).free<size+32*2**30:return False
        folder=self.root/day;folder.mkdir(exist_ok=False)
        files={name:stamp(name) for name in item.file_stats}
        counter=0
        paths={}
        def mapped(value,group,key):
            nonlocal counter
            array=value.detach().numpy() if isinstance(value,torch.Tensor) else value
            path=folder/(str(counter)+'.npy');counter+=1
            np.save(path,array,allow_pickle=False);files[str(path)]=stamp(path)
            paths.setdefault(group,{})[key]=(str(path),isinstance(value,torch.Tensor))
            # Open private mappings only on borrow. The template itself must
            # not pin file handles/pages or keep a second materialized copy.
            return None
        try:
            template=copy(item)
            template.host_tensors={k:mapped(v,'thin',k) for k,v in item.host_tensors.items()};template.tensors=template.host_tensors
            template.market={k:mapped(v,'market',k) if isinstance(v,np.ndarray) and not isinstance(v,np.memmap) else v for k,v in item.market.items()}
            bank=copy(item.resident_features)
            bank.host={k:mapped(v,'resident',k) for k,v in bank.host.items()};bank.tensors=bank.host;bank.column_plans={}
            template.resident_features=bank
            paths.setdefault('market',{});template._prepared_paths=paths
            template._source_paths={'optional':{}}
            for group in ('arrays','bank'):
                if not hasattr(item,group):continue
                values=getattr(item,group)
                if any(not isinstance(v,np.memmap) for v in values.values()):
                    raise ValueError('Authoritative session arrays must remain mapped')
                template._source_paths[group]={k:str(v.filename) for k,v in values.items()}
                setattr(template,group,{})
            for name in ('indicator_values','enriched_values'):
                value=getattr(item,name,None)
                if value is not None:
                    if not isinstance(value,np.memmap):raise ValueError('Authoritative indicator arrays must remain mapped')
                    template._source_paths['optional'][name]=str(value.filename)
                    setattr(template,name,None)
            # Catch source replacement during the preparation/copy itself.
            for name,expected in item.file_stats.items():
                if stamp(name)[:2]!=expected:raise ValueError('Certified input changed during host preparation')
            self.entries[day]=(template,files,folder,size);self.bytes+=size
        except BaseException:
            # Preserve failed derived artifacts for diagnosis; never reuse them.
            raise
        return True

    def close(self):
        self.entries.clear();self.bytes=0
