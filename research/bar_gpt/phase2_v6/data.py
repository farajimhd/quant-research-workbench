"""Store one frozen 32D embedding per physical bar in a session/timeframe."""
import numpy as np
import torch

from ..phase2_v5.data import DeviceSession, packed_pools


class BarSession(DeviceSession):
    def __init__(self, session, teacher, token_batch, device, *, cached=True, verify=False,
                 metrics=True, slow_cache=None, loss_anchors=False):
        if not cached or slow_cache is not None:
            raise ValueError("This experiment uses only a session-local physical bar cache")
        self.targets, self.anchors, self.loss_anchors, self.missing = [], [], [], []
        banks, rows = [], []
        base = self.num_windows = self.cache_hits = 0
        for f, (raw, valid, _, logs, features) in enumerate(packed_pools(teacher, session.pools, device, True)):
            # Window position now belongs to the temporal MLP. An unavailable
            # predecessor at the start of a pool must not borrow another frame's clock.
            features[:, 18] = 0
            features[0, 22] = 0
            embeddings = [teacher.readout(teacher.blocks(teacher.stem(tile)))
                          for tile in features.flatten(1).split(teacher.config.chunk_bars)]
            banks.extend(embeddings)
            ends, reverse = np.unique(session.endpoints[:, f], return_inverse=True)
            endpoints = torch.from_numpy(ends.astype(np.int64)).to(device, non_blocking=True)
            inverse = torch.from_numpy(reverse.reshape(-1)).to(device, non_blocking=True)
            rows.append(base + endpoints[inverse])
            base += len(raw)
            targets = torch.from_numpy(session.targets[f]).to(device, non_blocking=True)
            self.targets.append((raw, logs, valid, targets))
            self.missing.append(int((session.targets[f] < 0).sum()))
            self.num_windows += len(ends)
            if loss_anchors:
                self.loss_anchors.append((logs[endpoints], valid[endpoints], inverse))
            if metrics:
                # Last valid close inside each 64-bar window, independently for
                # trade, bid and ask. A cumulative maximum avoids expanding windows.
                ids = torch.arange(len(raw), device=device)[:, None]
                channels = [3, 9, 15]
                last = torch.where(valid[:, channels], ids, -1).cummax(0).values[endpoints]
                self.anchors.append((raw[last.clamp_min(0), channels], last >= endpoints[:, None] - 63, inverse))
        self.bank = torch.cat(banks)
        self.rows = torch.stack(rows, dim=1)

    def batch(self, indices, *, metrics=False, window_buffer=None):
        return self.targets_for(indices, (self.bank, self.rows[indices]), metrics)
