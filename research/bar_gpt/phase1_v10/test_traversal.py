import math
import pytest
from .train import verify_traversal,training_learning_rate
from .model import learning_rate


def test_shuffled_worker_tails_preserve_population_with_variable_steps():
    # Same shard lengths, two epoch permutations, two disjoint worker lanes.
    shards=[1,1,3,3]
    counts=[]
    for order in ([0,1,2,3],[0,2,1,3]):
        lanes=[sum(shards[i] for i in order[lane::2]) for lane in range(2)]
        batches=sum(math.ceil(n/4) for n in lanes)
        verify_traversal(sum(lanes),8,batches,batches)
        counts.append(batches)
    assert counts==[2,3]
    with pytest.raises(ValueError):verify_traversal(7,8,3,3)
    with pytest.raises(ValueError):verify_traversal(8,8,2,3)


def test_resume_keeps_cosine_and_variable_epoch_final_endpoint():
    assert training_learning_rate(45056,2278000,1,22276,22779,100,.001,.00001)==learning_rate(45056,2278000,.001,.00001)
    assert training_learning_rate(2277998,2278000,99,22778,22779,100,.001,.00001)==.00001
    assert training_learning_rate(0,2278000,0,0,22780,100,.001,.00001)==.001
