import numpy as np
from .scale_augmentation import augment


def test_exact_scale_roundtrip_counts_validity_and_direction():
    dtype=[('values','f4',(64,18)),('valid','u1',(64,18)),('end_us','i8',(64,)),('timeframe_us','i8'),('split','u1')]
    rows=np.ones(3,dtype=dtype);rng=np.random.default_rng(51)
    rows['values']=rng.uniform(.001,1e6,(3,64,18)).astype('f4')
    rows['valid'][:,0,4]=0;rows['values'][:,0,4]=np.nan
    before=rows.copy();powers=np.array([-8,1,8]);changed=augment(rows,powers);restored=augment(changed,-powers)
    for key in rows.dtype.names:
        np.testing.assert_array_equal(restored[key],rows[key])
        np.testing.assert_array_equal(before[key],rows[key])
        if key!='values':np.testing.assert_array_equal(changed[key],rows[key])
    np.testing.assert_array_equal(changed['values'][...,[5,11,17]],rows['values'][...,[5,11,17]])
    np.testing.assert_array_equal(np.sign(np.diff(changed['values'][:,:,10],axis=1)),np.sign(np.diff(rows['values'][:,:,10],axis=1)))
    np.testing.assert_array_equal(np.sign(changed['values'][:,:,0]-changed['values'][:,:,3]),np.sign(rows['values'][:,:,0]-rows['values'][:,:,3]))
