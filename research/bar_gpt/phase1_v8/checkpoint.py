"""Atomic checkpoint of the complete stochastic training continuation state."""
import random
import numpy as np
import torch


def save_checkpoint(path,model,opt,scheduler,gen,step,started,identity,best):
    state=dict(model=model.state_dict(),optimizer=opt.state_dict(),scheduler=scheduler.state_dict(),
        generator=gen.get_state(),torch_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),
        numpy_rng=np.random.get_state(),python_rng=random.getstate(),step=step,started=started,
        identity=identity,best=best,
        traversal='IID equal timeframe/price-band cell mass, uniform rows within cells; generator pins next indices and masks')
    temporary=path.with_suffix('.partial');torch.save(state,temporary);temporary.replace(path)
