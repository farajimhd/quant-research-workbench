from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SymbolExclusions:
    path: Path | None
    symbols: frozenset[str]
    sha256: str = ""

    @property
    def count(self) -> int:
        return len(self.symbols)

    def contains(self, symbol: str) -> bool:
        return normalize_symbol(symbol) in self.symbols

    def metadata(self) -> dict[str, str | int | list[str]]:
        sample = sorted(self.symbols)[:20]
        return {
            "excluded_symbols_file": str(self.path) if self.path else "",
            "excluded_symbols_count": self.count,
            "excluded_symbols_sha256": self.sha256,
            "excluded_symbols_sample": sample,
        }


def normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def load_symbol_exclusions(path: str | Path | None) -> SymbolExclusions:
    text = str(path or "").strip()
    if not text:
        return SymbolExclusions(path=None, symbols=frozenset())
    csv_path = Path(text)
    if not csv_path.exists():
        raise FileNotFoundError(f"Excluded symbols file not found: {csv_path}")
    raw = csv_path.read_bytes()
    symbols = frozenset(read_symbol_column(raw.decode("utf-8-sig"), csv_path))
    return SymbolExclusions(path=csv_path, symbols=symbols, sha256=hashlib.sha256(raw).hexdigest())


def read_symbol_column(text: str, path: Path) -> set[str]:
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        return set()
    symbol_key = next((name for name in reader.fieldnames if name and name.strip().lower() in {"symbol", "ticker"}), None)
    symbol_key = symbol_key or reader.fieldnames[0]
    symbols: set[str] = set()
    for row in reader:
        symbol = normalize_symbol(row.get(symbol_key))
        if symbol:
            symbols.add(symbol)
    return symbols
