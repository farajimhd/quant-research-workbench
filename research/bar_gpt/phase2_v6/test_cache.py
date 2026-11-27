from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import torch
from ..phase0_v7.test_model import parameters
from ..phase1_v29.model import Autoencoder
from ..phase2_v5.data import DeviceSession, Session
from ..phase2_v5.model import Forecast, HORIZONS
from ..phase2_v5.objectives import LossWeights
from ..phase2_v5.run_train import batch_objective
from ..v5.phase2_ar_shards.contract import BAR, FRAMES
from .data import BarSession
from .model import BarForecast, CachedGradients


def test_causal_bar_bank_and_matching_labels():
    teacher = Autoencoder(parameters()).requires_grad_(False).eval()
    pools = []
    for frame in FRAMES:
        p = np.zeros(140, dtype=BAR)
        p["end_us"] = 1_700_000_000_000_000 + np.arange(140)*frame
        p["values"] = 1 + np.arange(140)[:, None]
        p["valid"] = 1
        p["valid"][64:70, 3] = 0
        pools.append(p)
    ends = np.tile([[64], [65], [100]], (1, 5))
    targets = [np.tile(ends[:, :1]+1, (1, width)) for width in HORIZONS]
    session = Session(pools, ends, targets, np.arange(3), "sample", 0)
    cached = BarSession(session, teacher, 8192, "cpu", loss_anchors=True)
    old = DeviceSession(session, teacher, 8192, "cpu", loss_anchors=True)
    ids = torch.arange(3)
    a, logs, valid, evidence = cached.batch(ids, metrics=True)
    b = old.batch(ids, metrics=True)
    for x, y in zip((logs, valid, *evidence, *cached.forecast_anchors(ids)),
                    (*b[1:3], *b[3], *old.forecast_anchors(ids))):
        torch.testing.assert_close(x, y)
    model = BarForecast(Forecast())
    direct = model.temporal(a[0][a[1][..., None]+model.offsets].flatten(-2))
    torch.testing.assert_close(model.summaries(a), direct)
    original = model(a)
    for p in pools:
        p["values"][101:] *= 2
    changed = BarSession(session, teacher, 8192, "cpu")
    torch.testing.assert_close(original, model(changed.batch(ids)[0]), atol=0, rtol=0)


def test_gradient_bridge():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    teacher = Autoencoder(parameters()).to(device).eval().requires_grad_(False)
    m = BarForecast(Forecast()).to(device)
    direct = deepcopy(m)
    args = SimpleNamespace(graph_updates=device=="cuda", graph_tail_bucket=1024, batch_size=1024,
                           head_bf16=False, loss_weights=LossWeights(.65,.35,.1,.1,.1,.1,0,True))
    engine = CachedGradients(m, teacher, args)
    bank = torch.randn(300,32,device=device)
    for n in (1024,1024,833,1024):
        m.zero_grad(set_to_none=True)
        direct.zero_grad(set_to_none=True)
        inputs = bank, torch.randint(63,300,(n,5),device=device)
        logs = torch.randn(n,19,18,device=device)
        valid = torch.rand_like(logs)>.2
        anchors = (logs.flip([1]),valid.flip([1]))
        loss = engine(inputs, logs, valid, *anchors)
        ref = batch_objective(teacher,teacher.decode(direct(inputs)),logs,valid,args,anchors)
        ref.backward()
        torch.testing.assert_close(loss,ref,rtol=1e-5,atol=1e-5)
        for p,q in zip(m.parameters(),direct.parameters()):
            torch.testing.assert_close(p.grad,q.grad,rtol=2e-4,atol=2e-5)
