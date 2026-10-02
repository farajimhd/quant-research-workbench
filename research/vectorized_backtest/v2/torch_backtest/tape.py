"""Immutable market tensors shared across independent candidate accounts."""
from dataclasses import dataclass, fields
import math
import torch


@dataclass
class SqueezeTape:
    tickers: tuple[str, ...]
    clocks: torch.Tensor                 # [T] completed UTC seconds, int64.
    admission: torch.Tensor              # [N] first squeeze UTC second, int64.
    close: torch.Tensor                  # [T,N] latest valid completed mark.
    observed: torch.Tensor               # [T,N] actual valid completed 1s bar.
    high: torch.Tensor                   # [T,N] completed extrema or NaN.
    low: torch.Tensor                    # [T,N] completed extrema or NaN.
    vwap: torch.Tensor                   # [T,N] cumulative eligible session VWAP.
    bid: torch.Tensor                    # [T,N] latest completed source quote.
    ask: torch.Tensor
    quote_valid: torch.Tensor            # [T,N] quote fresh at this boundary.
    volume: torch.Tensor                 # [T,N] eligible volume in THIS interval.
    notional: torch.Tensor               # [T,N] eligible notional in THIS interval.
    trades: torch.Tensor                 # [T,N] actual trade count in THIS interval.
    fill_price: torch.Tensor             # [T,N] interval notional / volume.
    macd_line: torch.Tensor               # [T,N,4], completed 1s/5s/10s/30s.
    macd_signal: torch.Tensor
    structural_clock: torch.Tensor       # [T,N] certified fresh structural clock.
    level_from: torch.Tensor             # [N,L] inclusive UTC availability second.
    level_to: torch.Tensor               # [N,L] exclusive UTC availability second.
    level_lower: torch.Tensor            # [N,L] causal distinct-identity intervals.
    level_resistance: torch.Tensor       # [N,L] true only for resistance role.
    provenance: dict

    @property
    def device(self):
        return self.close.device

    @property
    def bytes(self):
        return sum(getattr(self, f.name).numel() * getattr(self, f.name).element_size()
                   for f in fields(self) if isinstance(getattr(self, f.name), torch.Tensor))

    def validate(self):
        t, n = self.close.shape
        if not t or not n or len(self.tickers) != n or tuple(sorted(set(self.tickers))) != self.tickers:
            raise ValueError("Require nonempty stable unique sorted ticker axis")
        if self.clocks.shape != (t,) or self.admission.shape != (n,):
            raise ValueError("Clock/admission shape mismatch")
        if self.clocks.dtype != torch.int64 or self.admission.dtype != torch.int64:
            raise ValueError("Clocks and admission must be integer UTC seconds")
        if not bool(((self.clocks[1:] - self.clocks[:-1]) == 1).all()):
            raise ValueError("Tape must advance on the exact one-second clock")
        for name in ("observed", "high", "low", "vwap", "bid", "ask", "quote_valid",
                     "volume", "notional", "trades", "fill_price", "structural_clock"):
            if getattr(self, name).shape != (t, n):
                raise ValueError("Market field shape mismatch: " + name)
        for name in ("observed", "quote_valid", "structural_clock"):
            if getattr(self, name).dtype != torch.bool:
                raise ValueError("Validity fields must be Boolean")
        for name in ("macd_line", "macd_signal"):
            if getattr(self, name).shape != (t, n, 4):
                raise ValueError("MACD requires all four certified timeframe lanes")
        if self.level_lower.ndim != 2 or self.level_lower.shape[0] != n:
            raise ValueError("Invalid structural interval layout")
        for name in ("level_from", "level_to", "level_resistance"):
            if getattr(self, name).shape != self.level_lower.shape:
                raise ValueError("Structural interval shape mismatch")
        if self.level_from.dtype != torch.int64 or self.level_to.dtype != torch.int64:
            raise ValueError("Structural clocks must be integer")
        if self.level_resistance.dtype != torch.bool:
            raise ValueError("Structural roles must be Boolean")
        for name in ("volume", "notional", "trades"):
            value = getattr(self, name)
            if not bool((torch.isfinite(value) & (value >= 0)).all()):
                raise ValueError("Invalid interval capacity/activity")
        if not bool((~self.quote_valid | (torch.isfinite(self.bid) & torch.isfinite(self.ask)
                                         & (self.bid > 0) & (self.ask >= self.bid))).all()):
            raise ValueError("A valid quote requires finite positive noncrossed prices")
        if not bool(((self.volume == 0) | (torch.isfinite(self.fill_price) & (self.fill_price > 0))).all()):
            raise ValueError("Positive capacity requires a valid interval execution price")
        if any(getattr(self, f.name).device != self.device for f in fields(self)
               if isinstance(getattr(self, f.name), torch.Tensor)):
            raise ValueError("All tape tensors must share a device")
        return self

    def to(self, device, maximum_gib=4.0):
        device = torch.device(device)
        if not math.isfinite(maximum_gib) or maximum_gib <= 0 or self.bytes > maximum_gib * 1024**3:
            raise MemoryError("Resident tape exceeds the explicit memory envelope")
        if device.type == "cuda":
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA unavailable; no CPU fallback")
            free, _ = torch.cuda.mem_get_info(device)
            if self.bytes > free * 0.65:
                raise MemoryError("Tape exceeds GPU headroom")
        return SqueezeTape(**{f.name: getattr(self, f.name).to(device)
                             if isinstance(getattr(self, f.name), torch.Tensor)
                             else getattr(self, f.name) for f in fields(self)}).validate()
