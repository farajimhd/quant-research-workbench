"""Paired warm-start diagnostic on train-only batches and fixed development masks."""
import copy
import numpy as np
import torch

from ..phase1_v7.full_data import prepare_raw
from ..phase2_v2.train import rng_state, restore_rng
from .data import forward_batch
from .objectives import objective


def compare(model, args):
    original_rng = rng_state()
    train = np.load(args.data / "train.npy", mmap_mode="r")
    dev = np.load(args.data / "development.npy", mmap_mode="r")
    generator = np.random.default_rng(1901)
    batches = [generator.choice(len(train), 128, replace=False) for _ in range(128)]
    held = prepare_raw(dev[np.linspace(0, len(dev)-1, 512, dtype=int)], native=True).to(args.device)
    result = {}
    try:
        for hidden_weight in (.5, .1):
            trial = copy.deepcopy(model)
            torch.manual_seed(1902)
            encoder = [p for n,p in trial.named_parameters() if n.startswith(("stem.", "blocks.", "readout."))]
            others = [p for p in trial.parameters() if p.requires_grad and all(p is not q for q in encoder)]
            optimizer = torch.optim.AdamW([dict(params=encoder, lr=1e-5), dict(params=others, lr=1e-4)],weight_decay=0)
            stats = {}
            for step, indices in enumerate(batches):
                trial.train()
                batch = prepare_raw(train[indices], native=True).to(args.device)
                out, values, valid = forward_batch(trial, batch)
                loss, terms = objective(trial, out, values, valid)
                loss = loss + (hidden_weight-.1)*(terms["masked_reconstruction"]-terms["visible_reconstruction"])
                if step == 0:
                    for name, weight in (("masked_reconstruction",hidden_weight),("visible_reconstruction",1-hidden_weight),("direction",.05),("range",.05),("ohlc",.05)):
                        gradients = torch.autograd.grad(weight*terms[name], encoder+others, retain_graph=True,allow_unused=True)
                        for group, subset in (("encoder",gradients[:len(encoder)]),("mixer",gradients[len(encoder):])):
                            stats[f"{name}/{group}_norm"] = float(sum(g.square().sum() for g in subset if g is not None).sqrt())
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(encoder+others,1.,error_if_nonfinite=True)
                optimizer.step()
            trial.eval()
            with torch.no_grad():
                for masked in (False, True):
                    trial.eval_mask = masked
                    torch.manual_seed(1903)
                    out, values, valid = forward_batch(trial, held)
                    _, terms = objective(trial,out,values,valid)
                    if masked:
                        stats["hidden_loss"] = float(terms["masked_reconstruction"])
                        raw = held.raw[...,3].double()
                        observed = valid[...,3] & ~out["hidden"]
                        median = torch.nanmedian(raw.masked_fill(~observed,float("nan")),dim=1,keepdim=True).values
                        keep = out["hidden"] & valid[...,3] & median.isfinite()
                        for name, price in (("model",out["logs"][...,3].double().exp()),("visible_median",median)):
                            stats[f"{name}_hidden_close_mape_pct"] = float((100*(price-raw).abs()/raw)[keep].mean())
                    else:
                        stats["visible_loss"] = float(terms["reconstruction"])
            result[str(hidden_weight)] = stats
    finally:
        restore_rng(original_rng)
    return result
