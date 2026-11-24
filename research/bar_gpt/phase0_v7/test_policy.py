"""Guard the domain boundary, information path and changed supervision."""
import copy
import torch
from .eligibility import reasons, model_validity
from .model import Autoencoder
from .objectives import objective
from .test_model import example, parameters


def test_price_policy_inclusive_and_source_invalid_not_rejected():
    raw = torch.ones(5, 18)
    valid = torch.ones_like(raw, dtype=torch.bool)
    raw[:, 0] = torch.tensor([0.05, 100000., 0.049, 100001., 0.00001])
    valid[4, 0] = False
    low, high, missing = reasons(raw, valid)
    assert (~(low | high | missing)).tolist() == [True, True, False, False, True]
    logs = raw.log()
    torch.testing.assert_close(model_validity(logs, valid).any(-1), ~(low | high | missing))


def test_rejected_values_cannot_reach_encoder_and_have_zero_loss():
    model = Autoencoder(parameters())
    _, values = example(torch.float32)
    values = values.repeat(2, 1)
    values[0, 0] = 20  # exp(20) exceeds the maximum price.
    values[1, 0] = -20
    values[1, 4:] += 10  # Activity and time must also be hidden for this bar.
    valid = torch.ones_like(values, dtype=torch.bool)
    out = model(values, valid)
    torch.testing.assert_close(out['embedding'][0], out['embedding'][1])
    loss, _ = objective(model, out, values, model_validity(values, valid))
    assert loss.item() == 0
    loss.backward()
    assert all(torch.count_nonzero(p.grad) == 0 for p in model.parameters())


def test_derived_trade_count_is_not_a_loss_target():
    model = Autoencoder(parameters())
    _, values = example(torch.float32)
    valid = torch.ones_like(values, dtype=torch.bool)
    out = model(values, valid)
    altered = dict(out, logs=out['logs'].clone())
    altered['logs'][..., 5] += 10000
    torch.testing.assert_close(objective(model, altered, values, valid)[0], objective(model, out, values, valid)[0])
    changed_q = dict(out, code=out['code'].clone())
    changed_q['code'][..., 5] += 10000
    assert objective(model, changed_q, values, valid)[0] > objective(model, out, values, valid)[0]


def test_encoder_compresses_geometry_without_changing_decoder_coordinates():
    model = Autoencoder(parameters())
    _, values = example(torch.float32)
    valid = torch.ones_like(values, dtype=torch.bool)
    observed = []
    handle = model.stem[0].register_forward_pre_hook(lambda module, args: observed.append(args[0].detach()))
    out = model(values, valid)
    handle.remove()
    from .transforms import coordinates
    code, _ = coordinates(values[..., :18], valid[..., :18])
    torch.testing.assert_close(observed[0][:, 9], torch.asinh(code[:, 3]))
    torch.testing.assert_close(model.decode(out['embedding'])['code'], out['code'])
