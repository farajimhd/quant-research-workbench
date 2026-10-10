"""Reachable lifecycle programs with exact pointwise sparsity and CUDA capture."""
from collections import OrderedDict
from dataclasses import replace
from time import perf_counter
import numpy as np
import torch
from research.vectorized_backtest.v6.torch_backtest.program import Program, Op, TorchPrograms
from .features import CATALOG
from .genome import STAGES, effective_rules
from .resident_features import ResidentFeatures


def prune(program):
    reachable = set()
    def visit(index):
        if index in reachable: return
        reachable.add(index)
        node = program.nodes[index]
        if node.op not in (Op.FEATURE, Op.CONSTANT):
            visit(node.a)
            if node.op not in (Op.NOT,Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE,Op.ABS): visit(node.b)
    visit(program.output)
    order = sorted(reachable)
    mapping = {old: new for new, old in enumerate(order)}
    return Program(tuple(replace(program.nodes[i], a=mapping.get(program.nodes[i].a, -1),
                                 b=mapping.get(program.nodes[i].b, -1)) for i in order), mapping[program.output])


class CapturedGroup:
    def __init__(self, program, shape, device):
        self.values = torch.zeros(shape, device=device)
        self.valid = torch.zeros(shape, device=device, dtype=torch.bool)
        self.program = program
        torch.cuda.synchronize(device)
        stream = torch.cuda.Stream(device=device)
        stream.wait_stream(torch.cuda.current_stream(device))
        with torch.cuda.stream(stream), torch.inference_mode():
            self.evaluate(); self.evaluate()
            self.graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(self.graph, stream=stream): self.output = self.evaluate()
        torch.cuda.current_stream(device).wait_stream(stream)

    def evaluate(self):
        value, known = self.program(self.values, self.valid)
        return (value != 0) & known

    def __call__(self, values, known):
        self.values.copy_(values); self.valid.copy_(known)
        self.graph.replay()
        return self.output


class RulePlan:
    def __init__(self, members, device, maximum_capture_gib=4.):
        self.device = torch.device(device)
        self.groups = []
        rules = [effective_rules(m) for m in members]
        for bit, stage in enumerate(STAGES):
            programs = [prune(r[stage]) for r in rules]
            extents = [p.validate(CATALOG)['history'][p.output] for p in programs]
            # Trail can change a held stop even without an execution observation.
            for sparse in (True, False):
                indices = [i for i, extent in enumerate(extents) if (extent == 0 and stage != 'trail') == sparse]
                if not indices: continue
                selected = [programs[i] for i in indices]
                columns = sorted({n.feature for p in selected for n in p.nodes if n.op == Op.FEATURE}) or [0]
                lookup = {old: new for new, old in enumerate(columns)}
                mapped = [Program(tuple(replace(n, feature=lookup[n.feature]) if n.op == Op.FEATURE else n
                                        for n in p.nodes), p.output) for p in selected]
                packed = TorchPrograms(mapped, [CATALOG[i] for i in columns], device)
                self.groups.append(dict(bit=bit, sparse=sparse, candidates=torch.tensor(indices, device=device),
                                        columns=columns, program=packed, extent=max(extents[i] for i in indices)))
        self.captures = OrderedDict()
        self.capture_bytes = 0
        self.maximum_capture_bytes = int(maximum_capture_gib*1024**3)
        self.builds = 0
        self.hits = 0
        self.workspace_gib = None

    def execute(self, group_id, values, known):
        group = self.groups[group_id]
        if self.device.type != 'cuda':
            value, mask = group['program'](values, known)
            return (value != 0) & mask
        key = (group_id, tuple(values.shape))
        rows=values.shape[0]*values.shape[1]
        estimate=rows*(values.shape[2]*5+len(group['candidates'])*(group['program'].width*20+64))
        limit=(self.workspace_gib*1024**3 if self.workspace_gib is not None
               else torch.cuda.mem_get_info(self.device)[0]*.8)
        if estimate>limit:raise MemoryError('V7 rule workspace exceeds declared budget')
        if key not in self.captures:
            torch.cuda.synchronize(self.device)
            # Reserve headroom before capture; evicted graphs have no queued work.
            while self.captures and self.capture_bytes > self.maximum_capture_bytes//2:
                _, (old, size) = self.captures.popitem(last=False)
                del old
                self.capture_bytes -= size
            before = torch.cuda.memory_allocated(self.device)
            capture = CapturedGroup(group['program'], values.shape, self.device)
            torch.cuda.synchronize(self.device)
            size = max(0, torch.cuda.memory_allocated(self.device)-before)
            if size > self.maximum_capture_bytes: raise MemoryError('Rule capture exceeds declared memory budget')
            while self.captures and self.capture_bytes+size > self.maximum_capture_bytes:
                _, (old, old_size) = self.captures.popitem(last=False)
                del old
                self.capture_bytes -= old_size
            self.captures[key] = (capture, size)
            self.capture_bytes += size
            self.builds += 1
        else:
            self.hits += 1
            self.captures.move_to_end(key)
        return self.captures[key][0](values, known)

    def evaluate(self, data, members, chunk, listing_batch, maximum_gate_gib, progress=None):
        b, t, u = len(members), data.clocks, len(data.listing_ids)
        if b*t*u > maximum_gate_gib*1024**3: raise MemoryError('V7 rule gates exceed declared budget')
        if not hasattr(data, 'resident_features'):
            data.resident_features = ResidentFeatures(data)
            if self.device.type == 'cuda': data.resident_features.pin()
            data.resident_features.activate(self.device)
        resident = data.resident_features
        if resident.device != self.device: resident.activate(self.device)
        gates = torch.zeros((b,t,u), device=self.device, dtype=torch.uint8)
        host = data.host_tensors
        membership = host['membership'].numpy()
        observed = host['observed'].numpy()
        next_observed = np.zeros_like(observed)
        next_observed[:-1] = observed[1:]
        first = np.where(membership.any(axis=0), membership.argmax(axis=0), t)
        # Before first admission no candidate can hold this identity. Group
        # similar first-admission times while preserving original listing IDs.
        order = np.argsort(first, kind='stable')
        started = perf_counter(); completed = 0
        for offset in range(0,u,listing_batch):
            listings = order[offset:offset+listing_batch].tolist()
            first_clock = int(first[listings].min())
            for begin in range(first_clock//chunk*chunk,t,chunk):
                if first_clock >= t: break
                end = min(t,begin+chunk)
                for group_id, group in enumerate(self.groups):
                    bit = group['bit']; candidates = group['candidates']
                    if group['sparse']:
                        eligible = next_observed[begin:end,listings].copy()
                        eligible &= np.arange(begin,end)[:,None] >= first[listings][None]
                        if bit == 0: eligible &= membership[begin:end,listings]
                        row, column = np.nonzero(eligible)
                        if not len(row): continue
                        # Fixed power-of-two query shapes amortize graph capture.
                        count = len(row); size = 1 << (count-1).bit_length()
                        clock = np.pad(row+begin,(0,size-count),mode='edge')
                        ticker = np.pad(np.asarray(listings)[column],(0,size-count),mode='edge')
                        values, known = resident.points(clock,ticker,group['columns'])
                        signal = self.execute(group_id,values,known)[:,0,:count]
                        ti = torch.as_tensor(row+begin,device=self.device)
                        ui = torch.as_tensor(np.asarray(listings)[column],device=self.device)
                        gates[candidates[:,None],ti[None],ui[None]] |= signal.to(torch.uint8)*(1<<bit)
                    else:
                        # Keep the reference chunk's prefix, including its
                        # float32 cumulative-sum rounding for moving means.
                        extent = 119
                        warm = max(0,begin-extent)
                        values, known = resident.block(warm,end,listings,group['columns'])
                        # Pad to a single shape per group, including the first
                        # and last chunks. Missing prefix validity stays false.
                        from torch.nn.functional import pad
                        left = extent-(begin-warm); right = chunk-(end-begin)
                        values = pad(values,(0,0,left,right,0,listing_batch-len(listings)))
                        known = pad(known,(0,0,left,right,0,listing_batch-len(listings)),value=False)
                        signal = self.execute(group_id,values,known)[:,:len(listings),extent:extent+end-begin]
                        eligible = np.arange(begin,end)[:,None] >= first[listings][None]
                        if bit == 0: eligible &= membership[begin:end,listings] & next_observed[begin:end,listings]
                        elif bit != 4: eligible &= next_observed[begin:end,listings]
                        signal = signal & torch.as_tensor(eligible.T,device=self.device)[None]
                        ti = torch.arange(begin,end,device=self.device)
                        ui = torch.as_tensor(listings,device=self.device)
                        gates[candidates[:,None,None],ti[None,None,:],ui[None,:,None]] |= signal.to(torch.uint8)*(1<<bit)
                completed += 1
                if progress and completed % 16 == 0:
                    progress(dict(stage='rule evaluation',tiles=completed,listing_groups_completed=offset//listing_batch,
                                  wall_seconds=perf_counter()-started,capture_builds=self.builds,capture_hits=self.hits))
        return gates

    def close(self):
        if self.device.type == 'cuda': torch.cuda.synchronize(self.device)
        self.captures.clear(); self.capture_bytes = 0


def mask_reference(gates, host):
    """Drop only unreachable decisions from the dense CPU oracle for parity."""
    membership=host['membership'];observed=host['observed']
    admitted=membership.to(torch.int32).cumsum(0)>0
    following=torch.zeros_like(observed);following[:-1]=observed[1:]
    masks=(membership & following,following,following,following,torch.ones_like(observed))
    result=torch.zeros_like(gates)
    for bit, mask in enumerate(masks):
        result |= (gates & (1<<bit))*((admitted & mask)[None].to(gates.dtype))
    return result
