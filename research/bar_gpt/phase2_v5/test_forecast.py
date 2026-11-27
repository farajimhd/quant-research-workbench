import numpy as np
import torch
from .data import target_indices
from .model import Forecast, SLOTS, objective
from .objectives import LossWeights, forecast_objective, relative_direction
from ..phase1_v29.model import Autoencoder
from ..phase0_v7.test_model import parameters
from ..phase0_v7.objectives import objective as v29_objective


def test_boundary_and_missing_horizon():
    closes = np.array([10,20,40])
    starts = closes - 10
    origins = np.array([0,1,10,11,20,30,31,40])
    expected = [[0,1],[1,2],[1,2],[2,-1],[2,-1],[2,-1],[-1,-1],[-1,-1]]
    np.testing.assert_array_equal(target_indices(closes,starts,origins,2), expected)


def test_forecast_shape_teacher_frozen_and_loss_parity():
    teacher = Autoencoder(parameters()).eval().requires_grad_(False)
    model = Forecast()
    windows = torch.randn(4,5,64,32)
    z = model(windows)
    assert z.shape == (4,SLOTS,32)
    assert model.encode(windows).shape == (4,128)
    out = teacher.decode(z)
    target = torch.randn_like(out["logs"])
    valid = torch.rand_like(target) > .2
    start = 0
    terms = []
    for width in (10,6,1,1,1):
        part = slice(start,start+width)
        terms.append(v29_objective(teacher,{k:v[:,part] for k,v in out.items()},target[:,part],valid[:,part])[0])
        start += width
    loss = objective(teacher,out,target,valid)
    torch.testing.assert_close(loss,torch.stack(terms).mean())
    loss.backward()
    assert model.heads.weight.grad.isfinite().all()
    assert teacher.decoder.weight.grad is None


def test_weighted_forecast_loss_backpropagates_through_frozen_decoder():
    teacher = Autoencoder(parameters()).eval().requires_grad_(False)
    model = Forecast()
    out = teacher.decode(model(torch.randn(4, 5, 64, 32)))
    target = torch.randn_like(out["logs"])
    valid = torch.rand_like(target) > .2
    anchors = torch.randn_like(target), torch.rand_like(target) > .2
    torch.testing.assert_close(forecast_objective(teacher, out, target, valid, LossWeights()),
                               objective(teacher, out, target, valid))
    weights = LossWeights.parse(".65,.35,.1,.05,.05,.05,.5")
    loss = forecast_objective(teacher, out, target, valid, weights, anchors)
    loss.backward()
    assert all(p.grad is not None and p.grad.isfinite().all() for p in model.parameters())
    assert all(p.grad is None for p in teacher.parameters())


def test_causal_direction_penalizes_wrong_way_moves_and_masks_missing_anchors():
    pred = torch.tensor([.99, 1.01, 100.], requires_grad=True)
    value = relative_direction(pred, torch.tensor([1.01, .99, .99]), torch.ones(3),
                               torch.tensor([True, True, False]), .001)
    value.backward()
    assert pred.grad[0] < 0 and pred.grad[1] > 0 and pred.grad[2] == 0


def test_direction_and_zero_padding_preserve_objective_and_gradient():
    from ..phase1_v29.objectives import direction_loss

    rng = torch.Generator().manual_seed(43)
    prediction = torch.randn(4, SLOTS, 32, generator=rng).requires_grad_()
    teacher = Autoencoder(parameters()).eval().requires_grad_(False)
    target = torch.randn(4, SLOTS, 18, generator=rng)
    valid = torch.rand(4, SLOTS, 18, generator=rng) > .2
    anchor = torch.randn(4, SLOTS, 18, generator=rng)
    anchor_valid = torch.rand(4, SLOTS, 18, generator=rng) > .25
    weights = LossWeights(.65, .35, .1, .1, .1, .1, anchored_direction=True)

    def pad(x):
        return torch.cat((x, torch.zeros_like(x[:3])))

    original = forecast_objective(teacher, teacher.decode(prediction), target, valid, weights, (anchor, anchor_valid))
    reported, reference = forecast_objective(teacher, teacher.decode(prediction), target, valid, weights,
                                             (anchor, anchor_valid), return_original=True)
    torch.testing.assert_close(reported, original)
    torch.testing.assert_close(reference, objective(teacher, teacher.decode(prediction), target, valid))
    padded = forecast_objective(teacher, teacher.decode(pad(prediction)), pad(target), pad(valid),
                                weights, (pad(anchor), pad(anchor_valid)))
    torch.testing.assert_close(original, padded)
    torch.testing.assert_close(torch.autograd.grad(original, prediction)[0],
                               torch.autograd.grad(padded, prediction)[0])

    actual = target.clone()
    actual[0] = anchor[0]
    pred = torch.randn(actual.shape, generator=rng).requires_grad_()
    expected = direction_loss(pred-anchor, actual-anchor, valid, .1)
    observed = relative_direction(pred, actual, anchor, valid, .1)
    torch.testing.assert_close(observed, expected)
    torch.testing.assert_close(torch.autograd.grad(observed, pred)[0], torch.autograd.grad(expected, pred)[0])

def test_packed_pools_match_direct_teacher_and_anchor_lookup():
    from .data import DeviceSession, Session
    from ..v5.phase2_ar_shards.contract import BAR, FRAMES
    from .model import HORIZONS

    teacher = Autoencoder(parameters()).eval().requires_grad_(False)
    pools = []
    for i, frame in enumerate(FRAMES):
        pool = np.zeros(130 + i, dtype=BAR)
        pool["end_us"] = 1_700_000_000_000_000 + np.arange(len(pool)) * frame
        pool["values"] = np.arange(len(pool))[:, None] + 1
        pool["valid"] = 1
        pool["valid"][64:67, 3] = 0
        pool["valid"][:, 9] = 0
        pools.append(pool)
    ends = np.tile([[63], [66], [128]], (1, 5))
    targets = [np.tile(ends[:, :1], (1, width)) for width in HORIZONS]
    source = Session(pools, ends, targets, np.arange(3), "test", 0)
    ids = torch.arange(3)
    train = DeviceSession(source, teacher, 2, "cpu", metrics=False, loss_anchors=True)
    evaluation = DeviceSession(source, teacher, 2, "cpu", verify=True)
    for actual, expected in zip(train.batch(ids)[:3], evaluation.batch(ids)[:3]):
        torch.testing.assert_close(actual, expected)
    shuffled = ids[[2, 0, 2]]
    buffer = torch.empty_like(train.batch(ids)[0])
    gathered = train.batch(shuffled, window_buffer=buffer)[0]
    assert gathered.data_ptr() == buffer.data_ptr()
    torch.testing.assert_close(gathered, train.batch(ids)[0][shuffled], atol=0, rtol=0)
    _, _, _, (_, anchors, observed) = evaluation.batch(ids, metrics=True)
    assert anchors[1, 0, 0] == 64
    assert not observed[:, :, 1].any()
    last_logs, last_valid = train.forecast_anchors(ids)
    torch.testing.assert_close(last_logs[1, :10, 0], torch.full((10,), np.log(67)))
    assert not last_valid[1, :10, 3].any()

def test_origin_order_respects_certified_overlap_policy():
    import pytest
    from .data import OVERLAP_POLICY, validate_origin_order

    overlap = {"version": OVERLAP_POLICY}
    validate_origin_order(np.array([[1, 2, 3], [2, 3, 4]]), overlap)
    validate_origin_order(np.array([[1, 2, 3], [4, 5, 6]]), None)
    for clocks, policy in (
        ([[1, 2, 3], [2, 3, 4]], None),
        ([[1, 3, 2], [2, 3, 4]], overlap),
        ([[2, 3, 4], [1, 2, 3]], overlap),
    ):
        with pytest.raises(ValueError, match="publication policy"):
            validate_origin_order(np.array(clocks), policy)

def test_signed_moves_and_price_bands_reconcile_with_trade_pairs():
    from .metrics import Metrics
    from .model import SLOTS

    stats = Metrics("cpu")
    anchor = torch.tensor([.50, 1., 5., 20., 100., 100., 100., 100.], dtype=torch.float64)
    actual = anchor * torch.tensor([.99, .998, 1., 1.002, 1.02, 1., .98, 1.02],
                                   dtype=torch.float64)
    forecast = anchor * torch.tensor([.99, 1.02, 1.01, 1.002, 1.02, .99, .98, 1.02],
                                     dtype=torch.float64)
    raw = actual[:, None, None].expand(-1, SLOTS, 18)
    logs = forecast.log()[:, None, None].expand_as(raw)
    base = anchor[:, None, None].expand(-1, SLOTS, 3)
    mask = torch.ones_like(base, dtype=torch.bool)
    mask[-1] = False
    stats.add({"logs": logs}, torch.ones_like(raw, dtype=torch.bool), (raw, base, mask))
    result = stats.result()
    trade = "1s/h01/trade/"
    for family, labels, expected in (
        ("signed_move", ("down_large","down_small","flat","up_small","up_large"), (2,1,2,1,1)),
        ("price_band", ("lt1","1_5","5_20","20_100","ge100"), (1,1,1,1,3)),
    ):
        keys = [trade + f"{family}/{label}/" for label in labels]
        assert tuple(result[k+"count"] for k in keys) == expected
        for metric in ("close_mae", "persistence_close_mae"):
            overall = ("comparable_close_mae" if metric == "close_mae" else metric)
            value = sum(result[k+metric]*n for k,n in zip(keys,expected)) / sum(expected)
            assert abs(value - result[trade+overall]) < 1e-12
    assert result[trade+"signed_move/down_small/direction_accuracy_pct"] == 0
    assert result[trade+"signed_move/flat/persistence_close_mae"] == 0
    stats.reset()
    assert stats.result()[trade+"price_band/ge100/count"] == 0
