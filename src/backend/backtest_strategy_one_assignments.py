"""Build Strategy 1 fixed-run assignments from certified ARTE identities only."""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence

from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


_PERMISSIONS = {name: True for name in (
    "observe", "enter", "add", "reduce", "exit", "reenter")}


def certified_strategy_one_assignments(
    configuration: Mapping[str, Any], identities: CertifiedIdentityPlan,
    *, candidate_tickers: Sequence[str], account_keys: Sequence[str],
) -> list[dict[str, Any]]:
    """No QMD, Watchlist, external-signal, disk, or current-identity fallback."""
    strategy = dict(configuration.get("strategy") or {})
    if (strategy.get("strategy_id") != STRATEGY_ID
            or strategy.get("strategy_number") != STRATEGY_NUMBER
            or strategy.get("revision") != STRATEGY_NUMBER):
        raise ValueError("Fixed Strategy 1 assignment needs its immutable identity")
    execution = dict(dict(strategy.get("parameters") or {}).get("execution") or {})
    tick = execution.get("tick_size")
    if type(tick) not in (int, float) or not isfinite(tick) or tick <= 0:
        raise ValueError("Fixed Strategy 1 needs a pinned positive execution tick")
    candidates = tuple(sorted(set(candidate_tickers)))
    accounts = tuple(dict.fromkeys(account_keys))
    if (not candidates or not accounts or not all(accounts)
            or not set(candidates) <= set(identities.tickers)):
        raise ValueError("Fixed Strategy 1 assignment scope differs from identity seal")
    configured = {}
    for source in configuration.get("assignments") or ():
        row = dict(source)
        key = (str(row.get("account_key") or ""), str(row.get("ticker") or ""))
        if (key in configured or key[0] not in accounts or key[1] not in candidates
                or row.get("strategy_id", STRATEGY_ID) != STRATEGY_ID
                or row.get("strategy_revision", STRATEGY_NUMBER) != STRATEGY_NUMBER):
            raise ValueError("Fixed Strategy 1 has a foreign or duplicate assignment")
        if row.get("status") in {"disabled", "completed", "error"}:
            raise ValueError("Fixed Strategy 1 cannot disable a certified candidate")
        configured[key] = row
    conids = dict(zip(identities.tickers, identities.conids))
    rows = []
    for account in accounts:
        for ticker in candidates:
            source = configured.get((account, ticker), {})
            conid = conids[ticker]
            if source.get("conid") is not None and int(source["conid"]) != conid:
                raise ValueError("Configured broker conid differs from dated ARTE identity")
            rows.append({
                **source,
                "assignment_id": source.get("assignment_id")
                                 or f"strategy-1:{account}:{ticker}",
                "account_key": account, "ticker": ticker, "conid": conid,
                "status": source.get("status") or "watching",
                "permissions": source.get("permissions") or dict(_PERMISSIONS),
                "source": "arte_dated_identity_v1",
            })
    return rows
