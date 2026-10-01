import numpy as np
import torch

from research.rl_trading.v6.model import INPUT_WIDTH
from research.rl_trading.v6.resnet_baseline import ResNetClassifier, run_epoch


def example():
    return dict(identities=np.array([0, 1]), window_index=0,
        account=np.zeros(7, np.float32), enter=np.array([True, False]),
        held_slots=np.array([1]), held_features=np.zeros((1, 11), np.float32),
        held_masks=np.ones((1, 4), bool), token=1, kind=1, weight=1.)


def test_resnet_masks_independent_windows_and_gradient():
    torch.manual_seed(1)
    model = ResNetClassifier()
    item = example()
    packed = {**item, **{key:torch.as_tensor(item[key]) for key in
        ('account','enter','held_slots','held_features','held_masks')}}
    windows = torch.randn(2, 120, INPUT_WIDTH)
    single = model(windows, [packed])[0]
    doubled = model(torch.cat((windows, windows * 3)), [packed, packed])[0]
    torch.testing.assert_close(single, doubled)
    assert single.shape == (7,)
    assert single[2] == torch.finfo(single.dtype).min
    torch.nn.functional.cross_entropy(single[None], torch.tensor([1])).backward()
    assert model.encoder[0].weight.grad.abs().sum() > 0


def test_actual_baseline_optimizer_and_evaluation_path():
    torch.set_num_threads(2)
    model = ResNetClassifier()
    features = np.random.default_rng(1).normal(size=(121, INPUT_WIDTH)).astype(np.float32)
    features[0] = 0
    data = (features, [np.tile(np.arange(1, 121), (2, 1))], [example()])
    optimizer = torch.optim.Adam(model.parameters(), lr=3e-4)
    args = ('cpu', 1, np.zeros(INPUT_WIDTH, np.float32), np.ones(INPUT_WIDTH, np.float32), 1)
    before = model.encoder[0].weight.detach().clone()
    result = run_epoch(model, data, optimizer, *args)
    assert result['counts']['enter_long'] == 1
    assert torch.isfinite(torch.tensor(result['mean_loss']))
    assert not torch.equal(before, model.encoder[0].weight)
    before = {key:value.clone() for key,value in model.state_dict().items()}
    run_epoch(model, data, None, *args)
    assert all(torch.equal(value, model.state_dict()[key]) for key,value in before.items())
