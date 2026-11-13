"""Explicit encoder-only transfer; never inherit the v3 decoder or optimizer."""
from pathlib import Path
import torch
from .shards.contract import file_hash, write_json


def initialize_encoder(model, checkpoint):
    path = Path(checkpoint).resolve()
    sha = file_hash(path)
    saved = torch.load(path, map_location='cpu', weights_only=False)
    state = {k.removeprefix('encoder.'): v for k, v in saved['model'].items()
             if k.startswith('encoder.')}
    expected = model.encoder.state_dict()
    if set(state) != set(expected) or any(state[k].shape != expected[k].shape for k in expected):
        raise ValueError('Checkpoint encoder architecture differs from v4')
    if any(not torch.isfinite(v).all() for v in state.values()):
        raise ValueError('Checkpoint encoder contains nonfinite values')
    if file_hash(path) != sha:
        raise ValueError('Initialization checkpoint changed during read')
    model.encoder.load_state_dict(state, strict=True)
    return dict(path=str(path), sha256=sha, source_epoch=saved.get('epoch'),
                transfer='encoder-only', decoder='fresh', optimizer='fresh')


def initialize_for_run(model, args):
    if args.resume:
        return
    if not args.encoder_checkpoint:
        raise ValueError('Choose the explicit v3 encoder checkpoint (planned epoch 50)')
    proof = initialize_encoder(model, args.encoder_checkpoint)
    write_json(args.output / 'encoder-initialization.json', proof)
