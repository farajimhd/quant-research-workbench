from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    model: str
    commission: float
    regulatory_fee: float
    tax: float
    total: float
    sec_fee: float = 0.0
    finra_taf: float = 0.0
    finra_cat: float = 0.0

    def to_dict(self) -> dict[str, float | str]:
        return asdict(self)


class IbkrUsStockFixedFeeModel:
    """Estimated IBKR Canada fixed-pricing costs for US stock fills."""

    name = "ibkr_ca_us_stock_fixed"
    commission_per_share = 0.005
    min_commission = 1.00
    max_commission_pct = 0.01
    sec_sale_value_rate = 0.0000206
    finra_taf_per_share = 0.000195
    finra_taf_max = 9.79
    finra_cat_per_share = 0.000003

    def __init__(self, tax_rate: float = 0.0):
        self.tax_rate = max(0.0, float(tax_rate))

    def estimate(self, *, side: str, quantity: int, fill_price: float) -> FeeBreakdown:
        shares = abs(int(quantity))
        trade_value = max(0.0, shares * float(fill_price))
        if shares <= 0 or trade_value <= 0:
            return FeeBreakdown(model=self.name, commission=0.0, regulatory_fee=0.0, tax=0.0, total=0.0)

        commission = min(max(shares * self.commission_per_share, self.min_commission), trade_value * self.max_commission_pct)
        is_sale = str(side).upper() == "SELL"
        sec_fee = trade_value * self.sec_sale_value_rate if is_sale else 0.0
        finra_taf = min(shares * self.finra_taf_per_share, self.finra_taf_max) if is_sale else 0.0
        finra_cat = shares * self.finra_cat_per_share
        regulatory_fee = sec_fee + finra_taf + finra_cat
        tax = commission * self.tax_rate
        total = commission + regulatory_fee + tax
        return FeeBreakdown(
            model=self.name,
            commission=commission,
            regulatory_fee=regulatory_fee,
            tax=tax,
            total=total,
            sec_fee=sec_fee,
            finra_taf=finra_taf,
            finra_cat=finra_cat,
        )


class ZeroFeeModel:
    name = "none"

    def estimate(self, *, side: str, quantity: int, fill_price: float) -> FeeBreakdown:
        return FeeBreakdown(model=self.name, commission=0.0, regulatory_fee=0.0, tax=0.0, total=0.0)


def fee_model_for_name(name: str, *, tax_rate: float = 0.0) -> IbkrUsStockFixedFeeModel | ZeroFeeModel:
    normalized = str(name or "").strip().lower()
    if normalized in {"", "none", "zero"}:
        return ZeroFeeModel()
    if normalized != IbkrUsStockFixedFeeModel.name:
        raise ValueError(f"Unsupported fee model: {name}")
    return IbkrUsStockFixedFeeModel(tax_rate=tax_rate)
