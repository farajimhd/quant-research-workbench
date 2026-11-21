import numpy as np
from .training_pool import sample_rows


def test_training_sampling_deterministic_and_retains_cheap_band():
    dtype=[('values','f4',(64,18)),('valid','u1',(64,18))]
    rows=np.ones(80,dtype=dtype)
    rows['values'][:10,:,[3,9,15]]=.03
    rows['values'][10:20,:,[3,9,15]]=.003
    before=rows.copy()
    first=sample_rows(rows,19)
    assert first==sample_rows(rows,19)
    assert sum(i<10 for i in first)>=8
    assert sum(10<=i<20 for i in first)>=8
    np.testing.assert_array_equal(rows,before)
