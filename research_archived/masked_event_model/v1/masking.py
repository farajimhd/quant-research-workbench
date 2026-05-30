from __future__ import annotations

from dataclasses import dataclass

import torch

from research.masked_event_model.v1.config import MaskConfig


@dataclass(slots=True)
class MaskBatch:
    quote_value_mask: torch.Tensor
    trade_value_mask: torch.Tensor
    summary_value_mask: torch.Tensor
    event_kind_mask: torch.Tensor
    quote_token_mask: torch.Tensor
    trade_token_mask: torch.Tensor
    chunk_mask: torch.Tensor

    def diagnostics(self) -> dict[str, float]:
        return {
            "mask/quote_value_ratio": float(self.quote_value_mask.float().mean().detach().cpu()),
            "mask/trade_value_ratio": float(self.trade_value_mask.float().mean().detach().cpu()),
            "mask/summary_value_ratio": float(self.summary_value_mask.float().mean().detach().cpu()),
            "mask/event_kind_ratio": float(self.event_kind_mask.float().mean().detach().cpu()),
            "mask/chunk_ratio": float(self.chunk_mask.float().mean().detach().cpu()),
            "mask/ratio_actual": float(
                torch.cat(
                    [
                        self.quote_value_mask.flatten(),
                        self.trade_value_mask.flatten(),
                        self.summary_value_mask.flatten(),
                    ]
                )
                .float()
                .mean()
                .detach()
                .cpu()
            ),
        }


def build_structured_masks(
    *,
    quote_values: torch.Tensor,
    trade_values: torch.Tensor,
    chunk_summary: torch.Tensor,
    event_kinds: torch.Tensor,
    config: MaskConfig,
) -> MaskBatch:
    device = quote_values.device
    batch, chunks, quote_events, quote_features = quote_values.shape
    trade_events, trade_features = trade_values.shape[2], trade_values.shape[3]
    summary_features = chunk_summary.shape[-1]
    total_events = event_kinds.shape[-1]

    chunk_mask = torch.rand((batch, chunks), device=device) < float(config.chunk_mask_ratio)
    chunk_mask |= build_span_mask(batch, chunks, config, device=device)
    chunk_mask |= build_tail_mask(batch, chunks, config, device=device)

    quote_token_mask = torch.rand((batch, chunks, quote_events), device=device) < float(config.event_mask_ratio)
    trade_token_mask = torch.rand((batch, chunks, trade_events), device=device) < float(config.event_mask_ratio)
    quote_token_mask |= chunk_mask.unsqueeze(-1)
    trade_token_mask |= chunk_mask.unsqueeze(-1)

    modality_draw = torch.rand((batch, chunks), device=device)
    quote_modality = modality_draw < (float(config.modality_mask_ratio) * 0.5)
    trade_modality = (modality_draw >= (float(config.modality_mask_ratio) * 0.5)) & (modality_draw < float(config.modality_mask_ratio))
    quote_token_mask |= quote_modality.unsqueeze(-1)
    trade_token_mask |= trade_modality.unsqueeze(-1)

    quote_value_mask = quote_token_mask.unsqueeze(-1).expand(-1, -1, -1, quote_features).clone()
    trade_value_mask = trade_token_mask.unsqueeze(-1).expand(-1, -1, -1, trade_features).clone()
    summary_value_mask = chunk_mask.unsqueeze(-1).expand(-1, -1, summary_features).clone()

    quote_value_mask |= torch.rand_like(quote_value_mask.float()) < float(config.field_mask_ratio)
    trade_value_mask |= torch.rand_like(trade_value_mask.float()) < float(config.field_mask_ratio)
    summary_value_mask |= torch.rand_like(summary_value_mask.float()) < float(config.field_mask_ratio)

    event_kind_mask = (torch.rand((batch, chunks, total_events), device=device) < float(config.event_mask_ratio)) | chunk_mask.unsqueeze(-1)
    event_kind_mask &= event_kinds != 2

    quote_value_mask, trade_value_mask, summary_value_mask = enforce_min_mask_ratio(
        quote_value_mask,
        trade_value_mask,
        summary_value_mask,
        min_ratio=float(config.min_mask_ratio),
    )
    return MaskBatch(
        quote_value_mask=quote_value_mask,
        trade_value_mask=trade_value_mask,
        summary_value_mask=summary_value_mask,
        event_kind_mask=event_kind_mask,
        quote_token_mask=quote_token_mask,
        trade_token_mask=trade_token_mask,
        chunk_mask=chunk_mask,
    )


def build_span_mask(batch: int, chunks: int, config: MaskConfig, *, device: torch.device) -> torch.Tensor:
    mask = torch.zeros((batch, chunks), dtype=torch.bool, device=device)
    for row in range(batch):
        if torch.rand((), device=device) >= float(config.span_mask_ratio):
            continue
        length = int(torch.randint(1, max(2, min(chunks, config.max_span_chunks) + 1), (), device=device).item())
        start = int(torch.randint(0, max(1, chunks - length + 1), (), device=device).item())
        mask[row, start : start + length] = True
    return mask


def build_tail_mask(batch: int, chunks: int, config: MaskConfig, *, device: torch.device) -> torch.Tensor:
    mask = torch.zeros((batch, chunks), dtype=torch.bool, device=device)
    for row in range(batch):
        if torch.rand((), device=device) >= float(config.tail_mask_ratio):
            continue
        length = int(torch.randint(1, max(2, min(chunks, config.max_tail_chunks) + 1), (), device=device).item())
        mask[row, chunks - length :] = True
    return mask


def enforce_min_mask_ratio(
    quote_mask: torch.Tensor,
    trade_mask: torch.Tensor,
    summary_mask: torch.Tensor,
    *,
    min_ratio: float,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    total = quote_mask.numel() + trade_mask.numel() + summary_mask.numel()
    current = int(quote_mask.sum().item() + trade_mask.sum().item() + summary_mask.sum().item())
    required = int(total * min_ratio)
    if current >= required:
        return quote_mask, trade_mask, summary_mask
    missing = required - current
    flat = torch.cat([quote_mask.flatten(), trade_mask.flatten(), summary_mask.flatten()])
    candidates = torch.where(~flat)[0]
    if candidates.numel() == 0:
        return quote_mask, trade_mask, summary_mask
    chosen = candidates[torch.randperm(candidates.numel(), device=flat.device)[:missing]]
    flat = flat.clone()
    flat[chosen] = True
    qn = quote_mask.numel()
    tn = trade_mask.numel()
    return (
        flat[:qn].reshape_as(quote_mask),
        flat[qn : qn + tn].reshape_as(trade_mask),
        flat[qn + tn :].reshape_as(summary_mask),
    )
