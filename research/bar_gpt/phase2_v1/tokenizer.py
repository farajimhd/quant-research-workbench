"""Frozen V4 and a bounded, session-identity-bound device token bank."""
from contextlib import nullcontext
import re
import numpy as np
import torch
from ..v5.encoder_v3 import ColumnEncoderV3, EncoderConfig
from ..v5.pretraining_v1 import PRICES, ACTIVITY
from ..v5.pretraining_v3 import local_references, REFERENCE_POLICY, TRANSFORM
from ..v5.transforms_v1 import BarInputTransformV1
from ..v5.shards.contract import digest, file_hash
from .config import FRAME_IDS
from ..v5.phase2_ar_shards.contract import FRAMES


def load_teacher(path, sha256, device='cpu'):
    if not re.fullmatch('[0-9a-f]{64}', sha256) or file_hash(path) != sha256:
        raise ValueError('Explicit V4 checkpoint SHA256 must match')
    saved = torch.load(path, map_location='cpu', weights_only=False)
    contract = saved['contract']
    if (contract.get('architecture') != 'column-encoder-small-mlp-v4'
            or contract.get('preprocessing') != TRANSFORM
            or digest(contract.get('reference_policy')) != digest(REFERENCE_POLICY)):
        raise ValueError('Teacher must be the exact V4 local-reference architecture')
    config = EncoderConfig(**contract['model'])
    if config.width != 512:
        raise ValueError('Latent v1 requires the 512-dimensional V4 encoder')
    encoder = ColumnEncoderV3(config)
    state = {k.removeprefix('encoder.'): v for k, v in saved['model'].items()
             if k.startswith('encoder.')}
    if not all(torch.isfinite(v).all() for v in state.values()):
        raise ValueError('Nonfinite teacher weights')
    encoder.load_state_dict(state, strict=True)
    if file_hash(path) != sha256:
        raise ValueError('Checkpoint changed during teacher loading')
    encoder.requires_grad_(False).eval().to(device)
    return encoder, dict(sha256=sha256, epoch=saved['epoch'], config=contract['model'],
                         preprocessing=TRANSFORM, reference_policy=REFERENCE_POLICY)


def normalize(values, valid):
    """The same Float64 formulas and shared reference authority as V4, on device."""
    if (not torch.isfinite(values).all() or ((values[..., PRICES] <= 0) & valid[..., PRICES]).any()
            or ((values[..., ACTIVITY] < 0) & valid[..., ACTIVITY]).any()):
        raise ValueError('Invalid observed market evidence')
    price, quantity = local_references(values, valid)
    scaled = torch.where(valid, values, 0).double()
    prices = torch.where(valid[..., PRICES], scaled[..., PRICES], price)
    scaled[..., PRICES] = 100 * torch.log(prices / price)
    scaled[..., ACTIVITY] = torch.log1p(scaled[..., ACTIVITY]) - torch.log1p(quantity[:, None])
    return torch.where(valid, scaled, 0).float(), torch.cat(
        (torch.log(price[:, 0]), torch.log1p(quantity)), dim=1).float()


class SessionTokens:
    """Unique pool endpoints encoded once; no per-origin raw I/O or teacher gradients.

    Lifetime is one immutable receipt on one teacher/precision. Dense ready bits
    avoid unbounded Python cache entries. No cache can cross a share-basis change.
    """
    def __init__(self, reader, teacher, teacher_sha, *, device='cpu', precision='fp32',
                 memory_mib=512, token_batch=256):
        if token_batch < 1 or memory_mib < 1 or precision not in ('fp32', 'bf16'):
            raise ValueError('Invalid tokenizer resource settings')
        if teacher.training or any(p.requires_grad for p in teacher.parameters()):
            raise ValueError('Teacher must be frozen and eval')
        self.teacher, self.device = teacher, torch.device(device)
        self.precision, self.token_batch = precision, token_batch
        counts = [v['rows'] for v in reader.receipt['views']]
        required = sum(n * (18*4 + 18 + 8 + 512*4 + 7*4 + 1) for n in counts)
        if required > memory_mib * 1024**2:
            raise MemoryError('Session pools/token bank exceed explicit device-cache budget')
        if self.device.type == 'cuda' and required + 512*1024**2 > torch.cuda.mem_get_info(self.device)[0]:
            raise MemoryError('Insufficient device headroom for tokenizer session')
        self.identity = digest(dict(receipt=reader.receipt['hash'], teacher=teacher_sha,
                                    transform=TRANSFORM, basis=reader.price_basis, precision=precision,
                                    tiles=token_batch,algorithm='fixed-pool-endpoint-tiles-v1'))
        self.transform = BarInputTransformV1().to(self.device)
        self.pools, self.bank, self.refs, self.ready = [], [], [], []
        for f in range(5):
            raw = reader.view(f)  # verifies immutable file SHA and dtype once
            if (np.any(np.diff(raw['end_us']) <= 0) or not np.isin(raw['valid'], (0, 1)).all()):
                raise ValueError('Invalid ordered pool/masks')
            self.pools.append(tuple(torch.from_numpy(np.array(raw[k], copy=True)).to(self.device)
                                    for k in ('values', 'valid', 'end_us')))
            self.bank.append(torch.empty((len(raw), 512), device=self.device, dtype=torch.float32))
            self.refs.append(torch.empty((len(raw), 7), device=self.device, dtype=torch.float32))
            self.ready.append(np.zeros(len(raw), dtype=bool))
        self.encoded_windows = 0

    def autocast(self):
        return torch.autocast('cuda', dtype=torch.bfloat16) if (
            self.device.type == 'cuda' and self.precision == 'bf16') else nullcontext()

    @torch.no_grad()
    def ensure(self, frame, endpoints):
        ends = np.unique(np.asarray(endpoints, dtype=np.int64))
        if len(ends) and (ends[0] < 63 or ends[-1] >= len(self.ready[frame])):
            raise ValueError('Tokenizer window outside immutable pool')
        values, valid, clocks = self.pools[frame]
        # Fixed pool-relative tiles keep each window's batch shape/order identical
        # across miss sets, prior blocks and resumes. This avoids GEMM roundoff
        # changing a frozen target when the cache starts at a different cursor.
        for tile in np.unique((ends - 63) // self.token_batch):
            start = 63 + int(tile) * self.token_batch
            selected = np.arange(start,min(start+self.token_batch,len(self.ready[frame])))
            if self.ready[frame][selected].all():continue
            ids = torch.as_tensor(selected.copy(), device=self.device)
            gather = ids[:, None] - torch.arange(63, -1, -1, device=self.device)
            raw, evidence = values[gather], valid[gather].bool()
            scaled, references = normalize(raw, evidence)
            transformed = self.transform(scaled, evidence, clocks[gather], FRAMES[frame])
            with self.autocast():
                encoded = self.teacher.encode_full(scaled, evidence,
                                                  transformed.values[..., 18:],
                                                  transformed.valid[..., 18:])
            if not torch.isfinite(encoded).all():
                raise FloatingPointError('Nonfinite frozen teacher embedding')
            self.bank[frame][ids] = encoded.float()
            self.refs[frame][ids] = references
            self.ready[frame][selected] = True
            self.encoded_windows += len(selected)

    def prepare(self, plan):
        for f, ends in enumerate(plan.endpoints):
            self.ensure(f, ends.reshape(-1))
        self.ensure(0, plan.targets)

    def batch(self, plan, start, stop):
        """No target values/clocks/references contribute to input metadata."""
        tokens, refs, starts, ends = [], [], [], []
        for f, selected in enumerate(plan.endpoints):
            selected = selected[start:stop]
            if not self.ready[f][selected].all():
                raise ValueError('Requested uncached historical embeddings')
            ids = torch.as_tensor(selected.copy(), device=self.device)
            tokens.append(self.bank[f][ids])
            refs.append(self.refs[f][ids])
            starts.append(self.pools[f][2][ids - 63])
            ends.append(self.pools[f][2][ids])
        token = torch.cat(tokens, 1)
        reference = torch.cat(refs, 1)
        origin = torch.as_tensor(plan.origin_us[start:stop].copy(), device=self.device)
        age_start = origin[:, None] - torch.cat(starts, 1)
        age_end = origin[:, None] - torch.cat(ends, 1)
        if (age_start < 0).any() or (age_end < 0).any():
            raise ValueError('Lookahead in model input')
        metadata = torch.cat((torch.log1p(age_start.double()/1e6)[..., None],
                              torch.log1p(age_end.double()/1e6)[..., None],
                              reference - reference[:, 7:8]), -1).float()
        targets = plan.targets[start:stop]
        if not self.ready[0][targets].all():
            raise ValueError('Requested uncached future target')
        target = self.bank[0][torch.as_tensor(targets.copy(), device=self.device)]
        return token, metadata, target.detach()
