"""Explicit device-to-host reporting boundary, outside timed tensor replay."""

import numpy as np
import polars as pl


def to_frames(result, tape, *, candidate=0):
    """Export one candidate in the Polars reference's accounting/state schema."""
    account = result["accounts"][:, candidate].detach().cpu().numpy()
    clocks = np.arange(len(account), dtype=np.int64) * tape.step_us + tape.start_us
    accounts = pl.DataFrame(
        {
            "time_us": clocks,
            "cash": account[:, 0],
            "realized_pnl": account[:, 1],
            "equity": account[:, 2],
            "market_value": account[:, 3],
        }
    )
    state = pl.DataFrame(
        {
            "listing_id": list(tape.listing_ids),
            **{
                name: tensor[candidate].detach().cpu().numpy()
                for name, tensor in result["state"].items()
            },
        }
    )
    return accounts, state.filter(
        (pl.col("quantity") > 0) | (pl.col("remaining") > 0)
    ).sort("listing_id")
