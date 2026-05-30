from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
from torch import nn

from research.inhouse_transformer.model_artifacts import save_model_architecture_artifacts as _save_model_architecture_artifacts
from research.masked_event_model.v1.masking import MaskBatch


def save_model_architecture_artifacts(
    *,
    model: Any,
    data_config: Any,
    output_dir: Path,
    version: str,
    torch_module: Any,
    wandb_run: Any = None,
    summary_batch_size: int = 1,
    summary_depth: int = 8,
    graph_depth: int = 3,
) -> dict[str, Any]:
    wrapped = MaskedAutoencoderSummaryWrapper(model, torch_module)
    return _save_model_architecture_artifacts(
        model=wrapped,
        data_config=data_config,
        output_dir=output_dir,
        version=version,
        torch_module=torch_module,
        wandb_run=wandb_run,
        summary_batch_size=summary_batch_size,
        summary_depth=summary_depth,
        graph_depth=graph_depth,
    )


class MaskedAutoencoderSummaryWrapper(nn.Module):
    """Expose a mask-free forward signature for torchinfo/torchview architecture export."""

    def __init__(self, model: Any, torch_module: Any) -> None:
        super().__init__()
        self.model = model
        self.torch = torch_module
        self.context_chunks = model.context_chunks
        self.max_quote_events = model.max_quote_events
        self.max_trade_events = model.max_trade_events
        self.max_total_events = model.max_total_events
        self.quote_feature_count = model.quote_feature_count
        self.trade_feature_count = model.trade_feature_count
        self.chunk_summary_count = model.summary_feature_count

    def forward(
        self,
        quote_values: Any,
        trade_values: Any,
        event_kinds: Any,
        event_indices: Any,
        chunk_summary: Any,
    ) -> Any:
        masks = MaskBatch(
            quote_value_mask=self.torch.zeros_like(quote_values, dtype=self.torch.bool),
            trade_value_mask=self.torch.zeros_like(trade_values, dtype=self.torch.bool),
            summary_value_mask=self.torch.zeros_like(chunk_summary, dtype=self.torch.bool),
            event_kind_mask=self.torch.zeros_like(event_kinds, dtype=self.torch.bool),
            quote_token_mask=self.torch.zeros_like(quote_values[..., 0], dtype=self.torch.bool),
            trade_token_mask=self.torch.zeros_like(trade_values[..., 0], dtype=self.torch.bool),
            chunk_mask=self.torch.zeros_like(chunk_summary[..., 0], dtype=self.torch.bool),
        )
        output = self.model(quote_values, trade_values, event_kinds, event_indices, chunk_summary, masks)
        return (
            output.quote_reconstruction,
            output.trade_reconstruction,
            output.summary_reconstruction,
            output.event_kind_logits,
            output.forecast_logits,
            output.embeddings,
            output.encoded_chunks,
        )
