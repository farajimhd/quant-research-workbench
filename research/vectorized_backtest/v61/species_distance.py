"""Transient representative packing; identical row-wise NumPy distance math."""
import numpy as np


class DistanceIndex:
    def __init__(self, representatives):
        self.ids = []
        self.rows = []
        self.features = []
        self.dirty = True
        for sid, row in representatives.items():
            self.add(sid, row)

    def add(self, sid, row):
        self.ids.append(sid)
        self.rows.append(row)
        self.features.append(set(row['features']))
        self.dirty = True

    def distances(self, a):
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
