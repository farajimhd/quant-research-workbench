"""Deterministic allocation; isolated, addressable exploration proposals."""
import hashlib,json,math
import numpy as np

SETTINGS=dict(mutation_fraction=.525,edit_counts=[1,2,3,4,5],
    edit_weights=[.2]*5,action_counts=[1,2,3,4,5],action_weights=[.2]*5,
    mating_weights=[.8,.2],action_modes=['uniform','union','intersection','xor'],
    action_mode_weights=[.25,.25,.25,.25],parameter_fraction=.7,
    parameter_fields=['weight','bias','threshold','policy','size'],
    parameter_weights=[.3,.15,.15,.15,.05,.2],
    structural_fields=['input','connect','disconnect','grow','remove','operator'],
    rank_bias='deterministic quantiles of size-three rank tournament',
    new_id='persistent SHA256 address; inherited identities unchanged',
    stream_key='saved root RNG state + generation + offspring slot + purpose + attempt',
    archive='profit-band rank quantiles; no archive RNG')

def balanced(n,weights,rotation=0):
    """Largest remainders, then interleaved equal-spacing; exact finite quotas."""
    if n==0:return np.empty(0,dtype=np.int64)
    w=np.asarray(weights,dtype=np.float64)
    if (w<0).any() or not np.isfinite(w).all() or w.sum()<=0:raise ValueError('Invalid allocation weights')
    exact=n*w/w.sum();counts=np.floor(exact).astype(int)
    ties=np.roll(np.arange(len(w)),rotation%len(w))
    order=ties[np.argsort(-(exact-counts)[ties],kind='stable')]
    counts[order[:n-int(counts.sum())]]+=1
    labels=np.repeat(np.arange(len(w)),counts)
    positions=np.concatenate([(np.arange(c)+.5)/c for c in counts if c])
    result=labels[np.argsort(positions,kind='stable')]
    return np.roll(result,rotation%n)

def rank_index(n,slot,generation,base=2):
    """Coverage of the same rank bias as minimum of three uniform draws."""
    k=int(slot+1+generation*997);u=0.;factor=1./base
    while k:
        k,digit=divmod(k,base);u+=digit*factor;factor/=base
    return min(n-1,int(n*(1-(1-u)**(1/3))))

class Proposal:
    def __init__(self,root,address=(),plan=None):
        self.root=root;self.address=tuple(address);self.plan=plan or {};self.counters={}
        self.key=hashlib.sha256(root+json.dumps(self.address,separators=(',',':')).encode()).digest()
        self.generator=np.random.default_rng(np.random.SeedSequence(np.frombuffer(self.key,dtype='<u4').tolist()))
    def fork(self,*parts,plan=None):return Proposal(self.root,self.address+tuple(parts),plan)
    def count(self,purpose):
        value=self.counters.get(purpose,0);self.counters[purpose]=value+1;return value
    def pick(self,n,purpose='target'):
        if n<1:raise ValueError('No legal choice')
        # No exploration seed or random draw determines allocation/locations.
        ordinal=sum((i+1)*v for i,v in enumerate(self.address) if isinstance(v,int))
        offset=sum(map(ord,purpose));stride=max(1,int(math.sqrt(n))+1)
        while math.gcd(stride,n)!=1:stride+=1
        return ((ordinal+self.count(purpose))*stride+offset)%n
    def uid(self):
        counter=self.count('uid')
        return int.from_bytes(hashlib.sha256(self.key+b'uid'+counter.to_bytes(8,'little')).digest()[:8],'little')%(2**63-1)+1
    def random(self,*a,**k):return self.generator.random(*a,**k)
    def integers(self,*a,**k):return self.generator.integers(*a,**k)
    def uniform(self,*a,**k):return self.generator.uniform(*a,**k)
    def normal(self,*a,**k):return self.generator.normal(*a,**k)
    def choice(self,*a,**k):return self.generator.choice(*a,**k)
    def permutation(self,*a,**k):return self.generator.permutation(*a,**k)

def generation_root(rng):
    # No shared draws, including for identity allocation or retries.
    return hashlib.sha256(json.dumps(rng.bit_generator.state,sort_keys=True,separators=(',',':')).encode()).digest()

def new_uid(rng,tag):
    if isinstance(rng,Proposal):return rng.uid()
    # Initialization helpers outside the matched-pool experiment remain usable.
    key=json.dumps([rng.bit_generator.state,tag],sort_keys=True,separators=(',',':')).encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8],'little')%(2**63-1)+1
