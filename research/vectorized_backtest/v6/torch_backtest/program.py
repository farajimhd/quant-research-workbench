"""Typed variable-length ruleset DAGs, evaluated across candidates in Torch.

The program axis is bounded; candidate/listing/candle arithmetic is vectorized.
Temporal operations count observed candles. Financial time never uses this axis.
"""
from dataclasses import dataclass, asdict
from enum import IntEnum
import math
import torch
from torch.nn import functional as F

class Op(IntEnum):
    FEATURE=0; CONSTANT=1; ADD=2; SUBTRACT=3; MULTIPLY=4; DIVIDE=5
    GREATER=6; GREATER_EQUAL=7; LESS=8; LESS_EQUAL=9
    AND=10; OR=11; NOT=12; LAG=13; MEAN=14; MINIMUM=15; MAXIMUM=16
    DIFFERENCE=17; CROSS_ABOVE=18; CROSS_BELOW=19; ABS=20

WINDOWS=(1,2,3,5,10,20,30,60,120)
MAX_NODES=32

@dataclass(frozen=True)
class Node:
    op:int
    a:int=-1
    b:int=-1
    feature:int=-1
    window:int=1
    value:float=0.
    unit:str='ratio'

@dataclass(frozen=True)
class Program:
    nodes:tuple[Node,...]
    output:int

    def validate(self, catalog, *, output_unit='bool', maximum_nodes=MAX_NODES):
        if not 1<=len(self.nodes)<=maximum_nodes or not 0<=self.output<len(self.nodes):
            raise ValueError('Program length/output exceeds declared envelope')
        units=[]; history=[]
        for i,node in enumerate(self.nodes):
            op=Op(node.op)
            if not math.isfinite(node.value) or node.window not in WINDOWS:
                raise ValueError('Invalid literal/observed-candle window')
            if op==Op.FEATURE:
                if not 0<=node.feature<len(catalog):raise ValueError('Unknown feature')
                unit=catalog[node.feature].unit; extent=0
            elif op==Op.CONSTANT:
                if node.unit not in {f.unit for f in catalog}|{'bool','ratio'}:
                    raise ValueError('Unknown literal unit')
                if node.unit=='bool' and node.value not in (0.,1.):raise ValueError('Boolean literal')
                unit=node.unit;extent=0
            else:
                unary=op in (Op.NOT,Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE,Op.ABS)
                if not 0<=node.a<i or (not unary and not 0<=node.b<i):
                    raise ValueError('Rulesets must refer to earlier operands; cycles forbidden')
                ua=units[node.a];ub=ua if unary else units[node.b]
                extent=max(history[node.a],history[node.a] if unary else history[node.b])
                if op in (Op.AND,Op.OR,Op.NOT):
                    if ua!='bool' or ub!='bool':raise ValueError('Boolean operands required')
                    unit='bool'
                elif op in (Op.GREATER,Op.GREATER_EQUAL,Op.LESS,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW):
                    if ua!=ub:raise ValueError('Comparison operands have incompatible units')
                    unit='bool'
                    if op in (Op.CROSS_ABOVE,Op.CROSS_BELOW):extent+=1
                elif op in (Op.ADD,Op.SUBTRACT):
                    if ua!=ub or ua=='bool':raise ValueError('Arithmetic unit mismatch')
                    unit=ua
                elif op==Op.MULTIPLY:
                    if 'bool' in (ua,ub) or 'ratio' not in (ua,ub):raise ValueError('Multiply requires dimensionless operand')
                    unit=ub if ua=='ratio' else ua
                elif op==Op.DIVIDE:
                    if 'bool' in (ua,ub) or (ua!=ub and ub!='ratio'):raise ValueError('Division unit mismatch')
                    unit='ratio' if ua==ub else ua
                else:
                    if ua=='bool' and op in (Op.MEAN,Op.DIFFERENCE,Op.ABS):raise ValueError('Numeric operand required')
                    unit=ua
                    if op in (Op.LAG,Op.DIFFERENCE):extent+=node.window
                    elif op in (Op.MEAN,Op.MINIMUM,Op.MAXIMUM):extent+=node.window-1
                if extent>119:raise ValueError('Expression exceeds 120 observed-candle context')
            units.append(unit);history.append(extent)
        if units[self.output]!=output_unit:raise ValueError('Lifecycle output has wrong type')
        reachable=set()
        def visit(i):
            if i in reachable:return
            reachable.add(i);n=self.nodes[i];op=Op(n.op)
            if op not in (Op.FEATURE,Op.CONSTANT):
                visit(n.a)
                if op not in (Op.NOT,Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE,Op.ABS):visit(n.b)
        visit(self.output)
        return dict(units=units,history=history,active_nodes=len(reachable))

    def payload(self):return dict(nodes=[asdict(n) for n in self.nodes],output=self.output)

    @classmethod
    def from_payload(cls,value):return cls(tuple(Node(**n) for n in value['nodes']),value['output'])


class TorchPrograms:
    """One packed population; only a bounded instruction loop is on the host.

    Evaluate a contiguous listing chunk including at most 119 preceding rows.
    Masks propagate through NOT/OR: missing evidence is never true evidence.
    """
    def __init__(self,programs,catalog,device='cpu',output_unit='bool',specialize_windows=True):
        for p in programs:p.validate(catalog,output_unit=output_unit)
        self.programs=programs;self.device=torch.device(device);self.width=max(len(p.nodes) for p in programs)
        self.specialize_windows=specialize_windows
        self.rows=[]
        for i in range(self.width):
            nodes=[p.nodes[i] if i<len(p.nodes) else Node(Op.CONSTANT) for p in programs]
            self.rows.append((nodes,torch.tensor([n.feature if n.feature>=0 else 0 for n in nodes],device=device),
                torch.tensor([max(n.a,0) for n in nodes],device=device),torch.tensor([max(n.b,0) for n in nodes],device=device)))
        self.outputs=torch.tensor([p.output for p in programs],device=device)
        self.batch_axis=torch.arange(len(programs),device=device)
        self.dispatch=[]
        for nodes,fi,ai,bi in self.rows:
            operations=[]
            for op in sorted({Op(n.op) for n in nodes}):
                choose=torch.tensor([n.op==op for n in nodes],device=device)[:,None]
                windows=[(k,torch.tensor([n.op==op and n.window==k for n in nodes],device=device)[:,None],
                           torch.tensor([j for j,n in enumerate(nodes) if n.op==op and n.window==k],device=device))
                         for k in sorted({n.window for n in nodes if n.op==op})] if op in (Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE) else []
                constants=torch.tensor([n.value for n in nodes],dtype=torch.float32,device=device)[:,None] if op==Op.CONSTANT else None
                operations.append((op,choose,windows,constants))
            self.dispatch.append(operations)

    def __call__(self,features,valid):
        if features.ndim not in (2,3) or valid.shape!=features.shape:raise ValueError('Expected [candles,features] or [listings,candles,features] and validity')
        b=len(self.programs);c=features.shape[-2]
        shape=(b,*features.shape[:-1]);broadcast=(b,)+(1,)*(features.ndim-1)
        # Every operand refers to an earlier instruction. Keep those results
        # once instead of stacking the complete prefix at each instruction.
        values=features.new_empty((self.width,*shape))
        masks=torch.empty((self.width,*shape),dtype=torch.bool,device=features.device)
        axis=torch.arange(c,device=features.device)[None]
        def lag(x,k):return F.pad(x[...,:max(0,c-k)],(k,0))[...,:c]
        for i,(nodes,fi,ai,bi) in enumerate(self.rows):
            out=features.new_zeros(shape);ok=torch.zeros(shape,dtype=torch.bool,device=features.device)
            if i:
                a=values[ai,self.batch_axis];d=values[bi,self.batch_axis]
                av=masks[ai,self.batch_axis];dv=masks[bi,self.batch_axis]
            for op,choose,windows,constants in self.dispatch[i]:
                choose=choose.reshape(broadcast)
                if op==Op.FEATURE:v=features[...,fi].movedim(-1,0);m=valid[...,fi].movedim(-1,0)
                elif op==Op.CONSTANT:
                    v=constants.to(features.dtype).reshape(broadcast).expand(shape);m=torch.ones_like(ok)
                elif op in (Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE):
                    v=torch.zeros_like(out);m=torch.zeros_like(ok)
                    for k,selected,indices in windows:
                        selected=selected.reshape(broadcast)
                        source=a.index_select(0,indices) if self.specialize_windows else a
                        known=av.index_select(0,indices) if self.specialize_windows else av
                        if op in (Op.LAG,Op.DIFFERENCE):
                            z=lag(source,k);mv=lag(known,k)&(axis>=k)
                            if op==Op.DIFFERENCE:z=source-z;mv=mv&known
                        else:
                            safe=torch.where(known,source,0.)
                            sums=F.pad(safe.cumsum(-1),(1,0));counts=F.pad(known.to(features.dtype).cumsum(-1),(1,0))
                            starts=(torch.arange(c,device=features.device)+1-k).clamp_min(0)
                            mv=((counts[...,1:]-counts[...,starts])==k)&(axis>=k-1)
                            if op==Op.MEAN:z=source if k==1 else (sums[...,1:]-sums[...,starts])/k
                            elif op==Op.MAXIMUM:z=F.max_pool1d(F.pad(safe.reshape(-1,1,c),(k-1,0),value=-float('inf')),k,1).reshape(source.shape)
                            else:z=-F.max_pool1d(F.pad(-safe.reshape(-1,1,c),(k-1,0),value=-float('inf')),k,1).reshape(source.shape)
                        if self.specialize_windows:
                            v.index_copy_(0,indices,z);m.index_copy_(0,indices,mv)
                        else:v=torch.where(selected,z,v);m=torch.where(selected,mv,m)
                else:
                    m=av if op in (Op.NOT,Op.ABS) else av&dv
                    if op==Op.ADD:v=a+d
                    elif op==Op.SUBTRACT:v=a-d
                    elif op==Op.MULTIPLY:v=a*d
                    elif op==Op.DIVIDE:m=m&(d.abs()>1e-12);v=a/torch.where(d.abs()>1e-12,d,1.)
                    elif op==Op.GREATER:v=a>d
                    elif op==Op.GREATER_EQUAL:v=a>=d
                    elif op==Op.LESS:v=a<d
                    elif op==Op.LESS_EQUAL:v=a<=d
                    elif op==Op.AND:v=(a!=0)&(d!=0)
                    elif op==Op.OR:v=(a!=0)|(d!=0)
                    elif op==Op.NOT:v=a==0
                    elif op==Op.ABS:v=a.abs()
                    elif op==Op.CROSS_ABOVE:v=(a>d)&(lag(a,1)<=lag(d,1));m=m&lag(av&dv,1)&(axis>=1)
                    elif op==Op.CROSS_BELOW:v=(a<d)&(lag(a,1)>=lag(d,1));m=m&lag(av&dv,1)&(axis>=1)
                m=m&torch.isfinite(v);out=torch.where(choose,torch.where(m,v,0.),out);ok=torch.where(choose,m,ok)
            values[i].copy_(out);masks[i].copy_(ok)
        return values[self.outputs,self.batch_axis],masks[self.outputs,self.batch_axis]
