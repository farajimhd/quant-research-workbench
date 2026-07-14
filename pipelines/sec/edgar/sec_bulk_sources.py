from __future__ import annotations


BULK_SOURCE_NAMES = (
    "submissions",
    "companyfacts",
    "company_tickers",
    "company_tickers_exchange",
    "company_tickers_mf",
)
DEFAULT_BULK_SOURCES = ",".join(BULK_SOURCE_NAMES)


def parse_bulk_sources(raw: str, *, allow_none: bool = False) -> list[str]:
    normalized = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not normalized or normalized == ["none"]:
        if allow_none:
            return []
        raise ValueError("bulk source selection cannot be empty")
    if "all" in normalized:
        return list(BULK_SOURCE_NAMES)
    invalid = sorted(set(normalized) - set(BULK_SOURCE_NAMES))
    if invalid:
        raise ValueError(f"unknown bulk sources: {', '.join(invalid)}")
    return list(dict.fromkeys(normalized))


def require_complete_bulk_sources(raw: str) -> str:
    selected = parse_bulk_sources(raw)
    missing = [source for source in BULK_SOURCE_NAMES if source not in selected]
    if missing:
        raise ValueError(
            "historical SEC fill requires the complete bulk snapshot; "
            f"missing={','.join(missing)}. Use the component download/ingest scripts for targeted repairs."
        )
    return DEFAULT_BULK_SOURCES
