"""Transient representative packing; identical row-wise NumPy distance math."""
import numpy as np


class DistanceIndex:
    def __init__(self, representatives):
        self.ids = []
        self.rows = []
        self.features = []
        self.dirty = True
        self.population=None;self.cached=[]
        for sid, row in representatives.items():
            self.add(sid, row)

    def add(self, sid, row):
        self.ids.append(sid)
        self.rows.append(row)
        self.features.append(set(row['features']))
        self.dirty = True
        if self.population is not None:self.cached.append(self._population_distance(row))

    def distances(self, a):
        if self.population is not None and id(a) in self.population_index:
            i=self.population_index[id(a)]
            return [(float(values[i]),sid) for sid,values in zip(self.ids,self.cached)]
        if not self.rows:
            return []
        if self.dirty:
            self.shapes = np.asarray([r['shape'] for r in self.rows])
            self.active = [i for i,r in enumerate(self.rows) if r.get('active')]
            self.behaviors = np.asarray([self.rows[i]['behavior'] for i in self.active])
            self.dirty = False
        x = np.asarray(a['shape']); y = self.shapes
        shape = np.mean(abs(x-y)/np.maximum(1,np.maximum(abs(x),abs(y))), axis=1)
        behavior = {}
        if a.get('active') and self.active:
            x = np.asarray(a['behavior']); y = self.behaviors
            behavior = dict(zip(self.active,np.mean(abs(x-y)/np.maximum(1,np.maximum(abs(x),abs(y))), axis=1)))
        aa = set(a['features']); out = []
        for i, (sid, b, bb) in enumerate(zip(self.ids, self.rows, self.features)):
            feature = 1-len(aa&bb)/max(1,len(aa|bb))
            struct = .5*feature+.5*float(shape[i])
            common = sorted(a['parameters'].keys()&b['parameters'].keys())
            parameter = float(np.mean([min(1,abs(a['parameters'][k]-b['parameters'][k])/16) for k in common])) if common else .5
            if a.get('active') and b.get('active'):
                value = .4*struct+.2*parameter+.4*float(behavior[i])
            else:
                value = (.4*struct+.2*parameter)/.6
            out.append((value, sid))
        return out

    def prepare_population(self, rows):
        """Evaluate each representative once against contiguous population rows."""
        self.population=rows;self.population_index={id(row):i for i,row in enumerate(rows)}
        self.population_features=[set(r['features']) for r in rows]
        self.population_shapes=np.asarray([r['shape'] for r in rows])
        self.population_active=np.asarray([bool(r.get('active')) for r in rows])
        self.population_behaviors=np.asarray([r['behavior'] for r in rows])
        self.cached=[self._population_distance(row) for row in self.rows]

    def _population_distance(self,b):
        result=np.empty(len(self.population),np.float64);bb=set(b['features'])
        for first in range(0,len(result),256):
            last=min(first+256,len(result));rows=self.population[first:last]
            x=self.population_shapes[first:last];y=np.asarray(b['shape'])
            shape=np.mean(abs(x-y)/np.maximum(1,np.maximum(abs(x),abs(y))),axis=1)
            feature=np.array([1-len(aa&bb)/max(1,len(aa|bb)) for aa in self.population_features[first:last]])
            parameter=[]
            for a in rows:
                common=sorted(a['parameters'].keys()&b['parameters'].keys())
                parameter.append(float(np.mean([min(1,abs(a['parameters'][k]-b['parameters'][k])/16) for k in common])) if common else .5)
            struct=.5*feature+.5*shape;value=.4*struct+.2*np.asarray(parameter)
            active=self.population_active[first:last]&bool(b.get('active'))
            output=value/.6
            if active.any():
                x=self.population_behaviors[first:last][active];y=np.asarray(b['behavior'])
                behavior=np.mean(abs(x-y)/np.maximum(1,np.maximum(abs(x),abs(y))),axis=1)
                output[active]=value[active]+.4*behavior
            result[first:last]=output
        return result
