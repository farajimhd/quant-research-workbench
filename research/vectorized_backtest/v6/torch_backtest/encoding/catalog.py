"""Stable atomic labels for every persisted ARTE bar/indicator resolution.

Catalog breadth does not imply fetching every field: the compiler returns the
exact dependency projection for each program (or a declared search envelope).
"""

from .core import AtomicInput, Catalog, Parameter, Unit

RESOLUTIONS = (100, 1000, 5000, 10000, 30000, 60000, 300000, 3600000)
BAR_FIELDS = {
    "open": ("open_int", Unit.PRICE, 0.0001),
    "high": ("high_int", Unit.PRICE, 0.0001),
    "low": ("low_int", Unit.PRICE, 0.0001),
    "close": ("close_int", Unit.PRICE, 0.0001),
    "volume": ("volume", Unit.SHARES, 1.0),
    "trade_count": ("trade_count", Unit.COUNT, 1.0),
    "notional": ("notional", Unit.MONEY, 1.0),
    "execution_volume": ("execution_volume", Unit.SHARES, 1.0),
    "execution_notional": ("execution_notional", Unit.MONEY, 1.0),
}
INDICATOR_FIELDS = {
    **{f"ema_{period}": Unit.PRICE for period in (7, 9, 12, 15, 20, 26, 50)},
    "macd_line": Unit.PRICE,
    "macd_signal": Unit.PRICE,
    "macd_histogram": Unit.PRICE,
    "rsi_14": Unit.RSI,
    "atr_14": Unit.PRICE,
    "previous_close": Unit.PRICE,
    "sample_count": Unit.COUNT,
    "avg_gain": Unit.PRICE,
    "avg_loss": Unit.PRICE,
    "rsi_ready": Unit.BOOLEAN,
    "atr_ready": Unit.BOOLEAN,
}


def arte_catalog(parameters: tuple[Parameter, ...] = (), **kwargs) -> Catalog:
    inputs = []
    for resolution_index, resolution in enumerate(RESOLUTIONS):
        for field_index, (name, (physical, unit, scale)) in enumerate(
            BAR_FIELDS.items()
        ):
            validity = "extremes_valid" if name in {"high", "low"} else "price_valid"
            # Raw scaled integer prices are atomic. Scaling is metadata, not a
            # separately searchable alternative source of price authority.
            inputs.append(
                AtomicInput(
                    resolution_index * 100 + field_index,
                    f"{name}@{resolution}ms",
                    f"{physical}_{resolution}",
                    unit,
                    resolution,
                    f"{validity}_{resolution}",
                    f"bar_at_{resolution}",
                    scale,
                    "bars",
                )
            )
        for field_index, (name, unit) in enumerate(INDICATOR_FIELDS.items(), start=20):
            valid = (
                f"{name.split('_')[0]}_ready_{resolution}"
                if name in {"rsi_14", "atr_14"}
                else f"indicator_valid_{resolution}"
            )
            inputs.append(
                AtomicInput(
                    resolution_index * 100 + field_index,
                    f"{name}@{resolution}ms",
                    f"{name}_{resolution}",
                    unit,
                    resolution,
                    valid,
                    f"indicator_at_{resolution}",
                    1.0,
                    "indicators",
                )
            )
    for offset, (name, unit) in enumerate(
        (
            ("quantity", Unit.SHARES),
            ("average_entry", Unit.PRICE),
            ("stop", Unit.PRICE),
            ("target", Unit.PRICE),
            ("remaining", Unit.SHARES),
            ("cash", Unit.MONEY),
            ("watchlisted", Unit.BOOLEAN),
        )
    ):
        inputs.append(AtomicInput(10000 + offset, name, name, unit))
    return Catalog(tuple(inputs), parameters, **kwargs)
