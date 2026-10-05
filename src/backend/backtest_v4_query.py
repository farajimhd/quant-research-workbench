"""SELECT-only, bounded saved-Canvas queries; positions retain their full reader."""
from src.backend.backtest_v4_saved_review import declared_saved_read_operation

from datetime import datetime
from uuid import UUID

from src.backend.backtest_v4_saved_review import _terminal_attestation
from src.backend.typed_backtest_review_core import _head_matches
from src.trading_runtime.arte_journal_reader import _detail_family, _verified_row, load_typed_event_page
from src.trading_runtime.arte_journal_writer import _CONTRACTS, _committed_batch_filter, _literal, _rows


@declared_saved_read_operation
def query_saved_journal(client, run_id, *, domain="activity", facets=False,
                        ticker="", event_type="", start="", end="",
                        after_sequence=0, limit=250, journal_only=False):
    if domain not in {"activity", "orders", "fills"}:
        raise ValueError("Unknown journal query domain")
    if type(limit) is not int or not 1 <= limit <= 500 or after_sequence < 0:
        raise ValueError("Invalid query bounds")
    instants = []
    for value in (start, end):
        if not value:
            instants.append(None)
            continue
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("Query time requires an explicit timezone")
        instants.append(instant)
    if all(instants) and instants[0] > instants[1]:
        raise ValueError("Query start exceeds end")
    if journal_only:
        from src.backend.backtest_recorded_journal import load_recorded_attestation
        prefix = load_recorded_attestation(client, str(UUID(run_id)))["prefix"]
    else:
        prefix = _terminal_attestation(client, str(UUID(run_id)), None)["prefix"]
    fence = (f"run_id={_literal(prefix.run_id)} AND sequence<={prefix.last_sequence} "
             f"{_committed_batch_filter(prefix)}")
    scope = {
        "activity": "category IN ('strategy','portfolio_management','protection','order_management','execution','broker','command')",
        "orders": "((category='command' AND entity_type='order') OR (category='order_management' AND entity_type='order_command'))",
        "fills": "category='execution' AND entity_type='fill'",
    }[domain]
    # Only present detail families are read. The aggregation transports keys,
    # not journal payloads; all joins retain the verified batch fence.
    kinds = _rows(client, f"SELECT category,entity_type FROM arte.trading_event_v1 WHERE {fence} AND {scope} GROUP BY category,entity_type FORMAT JSONEachRow")
    families = sorted({family for row in kinds
                       if (family := _detail_family(prefix, (row['category'], row['entity_type'])))
                       and 'ticker' in dict(_CONTRACTS[family].columns)})
    sources = [f"SELECT record_id,ticker FROM arte.{'trading_strategy_signal_v2' if family == 'trading_strategy_signal_v1' else family} WHERE run_id={_literal(prefix.run_id)} {_committed_batch_filter(prefix)} AND record_id IN (SELECT record_id FROM arte.trading_event_v1 WHERE {fence} AND {scope})"
               for family in families]
    ticker_source = " UNION ALL ".join(sources)
    if facets:
        tickers = _rows(client, f"SELECT DISTINCT ticker FROM ({ticker_source}) WHERE ticker!='' ORDER BY ticker FORMAT JSONEachRow") if sources else []
        result = {"tickers": [row['ticker'] for row in tickers],
                  "events": sorted({row['entity_type'] for row in kinds}),
                  "verified_sequence": prefix.last_sequence}
    else:
        predicates = [scope, f"sequence>{int(after_sequence)}"]
        if event_type:
            predicates.append(f"entity_type={_literal(event_type)}")
        if ticker:
            predicates.append(f"record_id IN (SELECT record_id FROM ({ticker_source}) WHERE ticker={_literal(ticker)})" if sources else "0")
        for field, op, value in (("event_time", ">=", start), ("event_time", "<=", end)):
            if value:
                predicates.append(f"{field}{op}parseDateTime64BestEffort({_literal(value)},9,'UTC')")
        selected = _rows(client, f"SELECT sequence FROM arte.trading_event_v1 WHERE {fence} AND {' AND '.join(predicates)} ORDER BY sequence LIMIT {limit + 1} FORMAT JSONEachRow")
        sequences = tuple(int(row['sequence']) for row in selected[:limit])
        rows = load_typed_event_page(client, prefix, limit=limit, selected_sequences=sequences) if sequences else ()
        result = {"events": [{"event": row.event, "detail_family": row.detail_family, "detail": row.detail} for row in rows],
                  "complete": len(selected) <= limit,
                  "next_sequence": sequences[-1] if sequences else after_sequence,
                  "verified_sequence": prefix.last_sequence}
        if domain == "fills" and rows:
            # Preserve final fees even when a fee revision is outside the selected
            # fill-time window. Never sum revisions or label an absent fee as zero.
            ids = ",".join(_literal(row.detail['execution_id']) for row in rows)
            table = "trading_commission_v1"
            columns = ",".join("d." + name for name, _ in _CONTRACTS[table].columns)
            fees = _rows(client, f"SELECT {columns},e.sequence AS fee_sequence FROM arte.{table} d INNER JOIN (SELECT record_id,sequence FROM arte.trading_event_v1 WHERE {fence} AND category='execution' AND entity_type='commission') e USING record_id WHERE d.run_id={_literal(prefix.run_id)} AND d.execution_id IN ({ids}) FORMAT JSONEachRow")
            latest = {}
            for fee in fees:
                sequence = int(fee.pop('fee_sequence'))
                verified = _verified_row(table, fee)
                if verified['batch_id'] not in prefix.batch_ids:
                    raise RuntimeError("Commission is outside the verified prefix")
                identity = verified['execution_id']
                if identity not in latest or latest[identity][0] < sequence:
                    latest[identity] = (sequence, verified)
            result['commissions'] = [value[1] for value in latest.values()]
    if not _head_matches(client, prefix.run_id, prefix):
        raise RuntimeError("Journal head changed during query")
    return result
