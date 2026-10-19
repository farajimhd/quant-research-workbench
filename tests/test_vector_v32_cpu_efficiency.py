from dataclasses import replace
from hashlib import sha256
import json
import numpy as np
from research.vectorized_backtest.v32 import calibration_cpu
from research.vectorized_backtest.v31.calibration_cpu import scan as reference_scan
from research.vectorized_backtest.v32.genome import sample,OPS
from research.vectorized_backtest.v32.sampling_fast import operator_choice
from research.vectorized_backtest.v32.lineage import program_key
from research.vectorized_backtest.v32.semantics import identity
from research.vectorized_backtest.v32.validation_cache import validation_scope


def test_population_preparation_preserves_order_keys_and_payloads():
    from research.vectorized_backtest.v32.preparation import _restore_population
    members=sample(np.random.default_rng(2020),10)
    payload=[m.payload() for m in members]
    restored,keys=_restore_population(payload)
    assert [m.payload() for m in restored]==payload
    assert keys==[program_key(m) for m in members]


def test_scalar_choices_preserve_values_and_rng():
    a=np.random.default_rng(2020);b=np.random.default_rng(2020)
    for _ in range(30):
        for fan in range(1,17):
            values=tuple(o for o in OPS if o not in ('identity','conv') and (o!='attention' or fan>=3 and fan%2==1))
            assert operator_choice(a,fan)==str(b.choice(values))
            options=list(range(0,fan*3,3))
            assert options[int(a.integers(len(options)))]==int(b.choice(options))
            assert a.bit_generator.state==b.bit_generator.state


def test_reused_calibration_buffer_exact_all_operators():
    rng=np.random.default_rng(2020)
    for entry in (False,True):
        for op in range(len(OPS)):
            x=rng.uniform(-1,1,(2,8,7,14)).astype(np.float32)
            known=rng.random((2,8,7,5))>.3
            held=rng.uniform(-1,1,(2,8,7,4)).astype(np.float32)
            counts=np.array([1,3,5,3,5,1],np.int32) if op!=6 else np.full(6,5,np.int32)
            src=np.array([rng.integers(-6,14+i,5) for i in range(6)],np.int32)
            weights=rng.uniform(-2,2,(6,5)).astype(np.float32);weights[rng.random(weights.shape)<.3]=0
            args=(x,known,held,src,weights,rng.uniform(-1,1,6).astype(np.float32),counts,np.full(6,op,np.int32),10,5,entry)
            for before,after in zip(reference_scan(*args),calibration_cpu.scan(*args)):
                np.testing.assert_array_equal(before,after)


def test_scoped_identity_fragments_preserve_mutable_fields_and_uid_semantics():
    def reference(m):return sha256(json.dumps(identity(m),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    members=sample(np.random.default_rng(2020),12)
    with validation_scope():
        for member in members:
            assert program_key(member)==reference(member)
            assert program_key(member)==reference(member)
            member.minimum_age['exit']=17
            assert program_key(member)==reference(member)
            net=member.networks['entry']
            member.networks['entry']=replace(net,threshold=-0.0)
            assert program_key(member)==reference(member)
            mutable=replace(net,nodes=list(net.nodes))
            member.networks['entry']=mutable
            assert program_key(member)==reference(member)
            mutable.nodes[0]=replace(mutable.nodes[0],bias=.123)
            assert program_key(member)==reference(member)

