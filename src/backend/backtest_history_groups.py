"""SELECT-only comparison metadata, outside the sealed saved-review reader."""
from contextlib import closing
from hashlib import sha256

from src.trading_runtime.arte_journal_writer import _literal, _rows, backtest_v4_operator_client_from_env
from src.trading_runtime.journal_contract import canonical_json


GROUP_FIELDS = ("strategy_id", "strategy_revision", "configuration_hash", "code_hash",
    "initial_cash", "start_local_ms", "end_local_ms", "simulation_profile",
    "activation_delay_us", "minimum_p_norm", "structure_book", "ticker_population_mode", "ticker_hash")


def comparison_metadata(client, rows: list[dict]) -> list[dict]:
    """Enrich only inventoried V4 identities, without granting review authority."""
    ids = [row["run_id"] for row in rows if row.get("journal_backend") == "arte_typed_journal_v4"]
    if not ids:
        return rows
    if len(ids) > 100 or len(set(ids)) != len(ids):
        raise ValueError("Comparison inventory must contain at most 100 unique runs")
    contexts = _rows(client, """
        SELECT r.run_id AS run_id,r.configuration_hash AS configuration_hash,
               c.strategy_id AS strategy_id,c.strategy_revision AS strategy_revision,
               d.initial_cash AS initial_cash,r.code_hash AS code_hash,
               d.start_local_ms AS start_local_ms,d.end_local_ms AS end_local_ms,
               d.simulation_profile AS simulation_profile,d.activation_delay_us AS activation_delay_us,
               d.minimum_p_norm AS minimum_p_norm,d.structure_book AS structure_book,
               d.ticker_population_mode AS ticker_population_mode,df.ticker_hash AS ticker_hash
        FROM arte.trading_run_v1 AS r
        INNER JOIN arte.trading_runtime_config_v1 AS c
          ON r.run_id=c.run_id AND r.run_month=c.run_month
        INNER JOIN arte.trading_run_context_commit_v1 AS f
          ON r.run_id=f.run_id AND r.run_month=f.run_month
        INNER JOIN arte.trading_backtest_definition_v1 AS d
          ON r.run_id=d.run_id AND r.run_month=d.run_month
        INNER JOIN arte.trading_backtest_definition_commit_v1 AS df
          ON d.run_id=df.run_id AND d.run_month=df.run_month AND d.content_hash=df.definition_hash
        WHERE r.run_id IN ({ids}) LIMIT {limit} FORMAT JSONEachRow
    """.format(ids=",".join(_literal(value) for value in ids), limit=len(ids) + 1))
    by_id = {}
    for context in contexts:
        key = str(context["run_id"])
        if key not in ids or key in by_id:
            raise RuntimeError("Comparison metadata contains duplicate or unrequested run context")
        by_id[key] = context
    result = []
    for row in rows:
        context = by_id.get(row["run_id"])
        if context is None:
            result.append(row)
            continue
        if (context["configuration_hash"] != row["configuration_content_hash"]
                or int(context["strategy_revision"]) != row["strategy_revision"]
                or context["strategy_id"] != row["strategy_id"]
                or float(context["initial_cash"]) != row["initial_cash"]):
            raise RuntimeError("Comparison context changed after inventory read")
        result.append({**row, "comparison_group_key": sha256(canonical_json({
            name: context[name] for name in GROUP_FIELDS
        }).encode("utf-8")).hexdigest(), "code_fingerprint": str(context["code_hash"]),
            "start_local_ms": int(context["start_local_ms"]), "end_local_ms": int(context["end_local_ms"])})
    return result


def enrich_history(rows: list[dict]) -> list[dict]:
    if not any(row.get("journal_backend") == "arte_typed_journal_v4" for row in rows):
        return rows
    with closing(backtest_v4_operator_client_from_env()) as client:
        return comparison_metadata(client, rows)
