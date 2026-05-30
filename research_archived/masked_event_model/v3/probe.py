from __future__ import annotations

from typing import Any

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from research.masked_event_model.v3.config import DataConfig, ProbeConfig
from research.masked_event_model.v3.data import EventChunkDataset
from research.masked_event_model.v3.losses import forecast_bce_loss
from research.masked_event_model.v3.metrics import forecast_metrics
from research.masked_event_model.v3.model import MaskedEventAutoencoder


class LinearProbe(nn.Module):
    def __init__(self, input_dim: int, output_dim: int, hidden_dim: int = 0) -> None:
        super().__init__()
        if hidden_dim > 0:
            self.net = nn.Sequential(nn.Linear(input_dim, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, output_dim))
        else:
            self.net = nn.Linear(input_dim, output_dim)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return self.net(values)


def run_linear_probe(
    *,
    encoder: MaskedEventAutoencoder,
    data_config: DataConfig,
    probe_config: ProbeConfig,
    device: torch.device,
    num_workers: int,
    seed: int,
) -> dict[str, float]:
    if not probe_config.enabled:
        return {}
    encoder_was_training = encoder.training
    encoder.eval()
    train_embeddings, train_targets, _ = collect_probe_tensors(
        encoder=encoder,
        data_config=data_config,
        split="train",
        limit=probe_config.train_windows,
        batch_size=probe_config.batch_size,
        device=device,
        num_workers=num_workers,
        seed=seed,
    )
    val_embeddings, val_targets, val_bps = collect_probe_tensors(
        encoder=encoder,
        data_config=data_config,
        split="validation",
        limit=probe_config.val_windows,
        batch_size=probe_config.batch_size,
        device=device,
        num_workers=num_workers,
        seed=seed + 101,
    )
    if train_embeddings.numel() == 0 or val_embeddings.numel() == 0:
        if encoder_was_training:
            encoder.train()
        return {"probe/status": 0.0}
    output_dim = int(train_targets[0].numel())
    probe = LinearProbe(train_embeddings.shape[-1], output_dim, hidden_dim=probe_config.hidden_dim).to(device)
    optimizer = torch.optim.AdamW(probe.parameters(), lr=probe_config.learning_rate)
    train_embeddings = train_embeddings.to(device)
    train_targets = train_targets.reshape(train_targets.shape[0], -1).to(device)
    for _ in range(max(1, probe_config.train_steps)):
        order = torch.randperm(train_embeddings.shape[0], device=device)
        for start in range(0, train_embeddings.shape[0], probe_config.batch_size):
            rows = order[start : start + probe_config.batch_size]
            logits = probe(train_embeddings[rows])
            loss, _ = forecast_bce_loss(logits.reshape_as(train_targets[rows]), train_targets[rows])
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
    probe.eval()
    with torch.no_grad():
        logits = probe(val_embeddings.to(device)).detach().cpu().numpy().reshape(val_targets.shape)
    metrics = forecast_metrics(logits, val_bps.numpy(), prefix="probe/val")
    metrics["probe/train_windows"] = float(train_embeddings.shape[0])
    metrics["probe/val_windows"] = float(val_embeddings.shape[0])
    if encoder_was_training:
        encoder.train()
    return metrics


def collect_probe_tensors(
    *,
    encoder: MaskedEventAutoencoder,
    data_config: DataConfig,
    split: str,
    limit: int,
    batch_size: int,
    device: torch.device,
    num_workers: int,
    seed: int,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    dataset = EventChunkDataset(config=data_config, split=split, batch_size=batch_size, seed=seed)
    loader = DataLoader(dataset, batch_size=None, num_workers=max(0, num_workers))
    embeddings: list[torch.Tensor] = []
    targets: list[torch.Tensor] = []
    bps: list[torch.Tensor] = []
    total = 0
    with torch.no_grad():
        for batch in loader:
            moved = move_batch(batch, device)
            emb = encoder.encode(
                moved["quote_values"],
                moved["trade_values"],
                moved["event_kinds"],
                moved["event_indices"],
                moved["chunk_summary"],
            ).detach().cpu()
            embeddings.append(emb)
            targets.append(batch["targets"].detach().cpu())
            bps.append(batch["target_bps"].detach().cpu())
            total += emb.shape[0]
            if limit > 0 and total >= limit:
                break
    if not embeddings:
        return torch.empty(0), torch.empty(0), torch.empty(0)
    return torch.cat(embeddings, dim=0)[:limit], torch.cat(targets, dim=0)[:limit], torch.cat(bps, dim=0)[:limit]


def move_batch(batch: dict[str, Any], device: torch.device) -> dict[str, Any]:
    out = dict(batch)
    for key in ("quote_values", "trade_values", "event_kinds", "event_indices", "chunk_summary", "targets", "target_bps"):
        out[key] = batch[key].to(device, non_blocking=True)
    return out
