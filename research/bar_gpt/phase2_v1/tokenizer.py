"""Frozen V4 and a bounded, session-identity-bound device token bank."""
from contextlib import nullcontext
import re
import numpy as np
import torch
from ..v5.encoder_v3 import ColumnEncoderV3, EncoderConfig
from ..v5.encoder_v4 import ColumnDecoderV4
from ..v5.pretraining_v1 import PRICES, ACTIVITY
from ..v5.pretraining_v3 import local_references, REFERENCE_POLICY, TRANSFORM
from ..v5.transforms_v1 import BarInputTransformV1
from ..v5.shards.contract import digest, file_hash
from .config import FRAME_IDS
from ..v5.phase2_ar_shards.contract import FRAMES


def load_teacher(path, sha256, device='cpu', *, required_epoch=None):
    if not re.fullmatch('[0-9a-f]{64}', sha256) or file_hash(path) != sha256:
        raise ValueError('Explicit V4 checkpoint SHA256 must match')
    saved = torch.load(path, map_location='cpu', weights_only=False)
    if required_epoch is not None:
        history = saved.get('history',[])
        if (saved.get('epoch') != required_epoch or saved.get('phase') != 'train' or not history
                or history[-1].get('epoch') != required_epoch
                or history[-1].get('training',{}).get('complete_pass') is not True
                or history[-1].get('validation',{}).get('complete_pass') is not True):
            raise ValueError('Teacher checkpoint does not contain the required fully completed epoch')
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
    decoder = ColumnDecoderV4(config)
    decoder.load_state_dict({k.removeprefix('decoder.'):v for k,v in saved['model'].items()
                             if k.startswith('decoder.')}, strict=True)
    if not all(torch.isfinite(v).all() for v in decoder.state_dict().values()):
        raise ValueError('Nonfinite teacher decoder weights')
    encoder.reconstruction_decoder = decoder
    if file_hash(path) != sha256:
        raise ValueError('Checkpoint changed during teacher loading')
    encoder.requires_grad_(False).eval().to(device)
    return encoder, dict(sha256=sha256, epoch=saved['epoch'], config=contract['model'],
                         preprocessing=TRANSFORM, reference_policy=REFERENCE_POLICY,
                         decoder='same-checkpoint frozen V4; validation only')


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
        # Include the packed clock bank and lazy per-bar UTC feature cache.
        required = sum(n * (18*4 + 18 + 8 + 512*4 + 7*4 + 1 + 8 + 9*5) for n in counts)
        if required > memory_mib * 1024**2:
            raise MemoryError('Session pools/token bank exceed explicit device-cache budget')
        if self.device.type == 'cuda' and required + 512*1024**2 > torch.cuda.mem_get_info(self.device)[0]:
            raise MemoryError('Insufficient device headroom for tokenizer session')
        self.identity = digest(dict(receipt=reader.receipt['hash'], teacher=teacher_sha,
                                    transform=TRANSFORM, basis=reader.price_basis, precision=precision,
                                    tiles=token_batch,algorithm='fixed-pool-endpoint-tiles-v1'))
        self.transform = BarInputTransformV1().to(self.device)
        self.offsets = np.cumsum([0] + counts[:-1], dtype=np.int64)
        self.token_bank = torch.empty((sum(counts),512),device=self.device,dtype=torch.float32)
        self.reference_bank = torch.empty((sum(counts),7),device=self.device,dtype=torch.float32)
        banks, references = self.token_bank.split(counts), self.reference_bank.split(counts)
        self.time_cache = {}
        self.pools, self.bank, self.refs, self.ready = [], [], [], []
        for f in range(5):
            raw = reader.view(f)  # verifies immutable file SHA and dtype once
            if (np.any(np.diff(raw['end_us']) <= 0) or not np.isin(raw['valid'], (0, 1)).all()):
                raise ValueError('Invalid ordered pool/masks')
            self.pools.append(tuple(torch.from_numpy(np.array(raw[k], copy=True)).to(self.device)
                                    for k in ('values', 'valid', 'end_us')))
            self.bank.append(banks[f])
            self.refs.append(references[f])
            self.ready.append(np.zeros(len(raw), dtype=bool))
        self.encoded_windows = 0
        self.clock_bank = torch.cat([p[2] for p in self.pools])

    def window_time(self, frame, gather):
        """Reuse exact shared-transform UTC fields; window position/gap stay local.

        Pool clocks have already passed the immutable reader and ordering checks.
        The shared transform validates them once in contiguous 64-bar tiles.
        Only window-independent clock fields are reused. Tile-boundary gaps are
        restored from actual adjacent clocks; every requested window resets its
        first gap and relative positions exactly as the original transform does.
        """
        if frame not in self.time_cache:
            clocks=self.pools[frame][2];n=len(clocks)
            padded=torch.zeros(((n+63)//64)*64,device=self.device,dtype=torch.int64)
            padded[:n]=clocks
            shape=(len(padded)//64,64,18)
            zero=torch.zeros(shape,device=self.device,dtype=torch.float32)
            absent=torch.zeros(shape,device=self.device,dtype=torch.bool)
            made=self.transform(zero,absent,padded.reshape(-1,64),FRAMES[frame])
            values=made.values[...,18:].reshape(-1,9)[:n].clone()
            valid=made.valid[...,18:].reshape(-1,9)[:n].clone()
            values[1:,4]=torch.log1p(torch.diff(clocks).double()/FRAMES[frame])
            valid[:,4]=True
            self.time_cache[frame]=(values,valid)
        values,valid=(v[gather].clone() for v in self.time_cache[frame])
        values[...,0]=torch.arange(64,device=self.device,dtype=torch.float32)/63
        values[:,0,4]=0;valid[:,0,4]=False
        return values,valid

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
            time,time_valid = self.window_time(frame,gather)
            # Preserve the original transform's column-view strides as well as
            # values: reduction/GEMM dispatch may depend on input layout.
            time=torch.cat((scaled,time),dim=-1)[...,18:]
            time_valid=torch.cat((evidence,time_valid),dim=-1)[...,18:]
            with self.autocast():
                encoded = self.teacher.encode_full(scaled, evidence,
                                                  time,time_valid)
            # normalize checks source evidence/references; preserve the original
            # Float32-overflow guard formerly provided by the per-window transform.
            if not (torch.isfinite(scaled).all() & torch.isfinite(encoded).all()):
                raise FloatingPointError('Nonfinite scaled input or frozen teacher embedding')
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
        selected_frames = []
        for f, selected in enumerate(plan.endpoints):
            selected = selected[start:stop]
            if not self.ready[f][selected].all():
                raise ValueError('Requested uncached historical embeddings')
            selected_frames.append(selected + self.offsets[f])
        targets=plan.targets[start:stop]
        if not self.ready[0][targets].all():
            raise ValueError('Requested uncached future target')
        # One bounded transfer replaces a separate blocking transfer per frame.
        indices=torch.from_numpy(np.concatenate(selected_frames+[targets[:,None]],axis=1))
        indices=(indices.pin_memory().to(self.device,non_blocking=True)
                 if self.device.type=='cuda' else indices.to(self.device))
        ids=indices[:,:16]
        token,reference=self.token_bank[ids],self.reference_bank[ids]
        origin = torch.as_tensor(plan.origin_us[start:stop].copy(), device=self.device)
        age_start = origin[:, None] - self.clock_bank[ids - 63]
        age_end = origin[:, None] - self.clock_bank[ids]
        if (age_start < 0).any() or (age_end < 0).any():
            raise ValueError('Lookahead in model input')
        metadata = torch.cat((torch.log1p(age_start.double()/1e6)[..., None],
                              torch.log1p(age_end.double()/1e6)[..., None],
                              reference - reference[:, 7:8]), -1).float()
        target = self.token_bank[indices[:,16]]
        return token, metadata, target.detach()

    @torch.no_grad()
    def reconstruction(self, plan, start, stop):
        """Target-only evaluation evidence; never returned by the input batch API.

        Recompute exact Float64 references from raw target bars, rather than the
        Float32 log references cached for historical input metadata.
        """
        ids = torch.as_tensor(plan.targets[start:stop].copy(), device=self.device)
        gather = ids[:, None] - torch.arange(63, -1, -1, device=self.device)
        values, valid, clocks = self.pools[0]
        raw, mask, ends = values[gather], valid[gather].bool(), clocks[gather]
        reference, stats = local_references(raw, mask)
        scaled, _ = normalize(raw, mask)
        transformed = self.transform(scaled, mask, ends, FRAMES[0])
        return dict(raw=raw.transpose(1,2).contiguous(), valid=mask.transpose(1,2).contiguous(),
                    reference=reference, stats=stats, time=transformed.values[...,18:],
                    time_valid=transformed.valid[...,18:])
