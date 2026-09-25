"""Read-only SQL boundary for arte products and certified metadata."""
import json
from pipelines.market_sip.events.trade_reporting_flags import DELAYED

from research.mlops.clickhouse import (ClickHouseHttpClient, default_clickhouse_url,
    default_clickhouse_user, default_clickhouse_password)

TABLES = frozenset({'bars_v1', 'indicators_v1', 'structural_levels_v7',
                    'structural_level_observations_v7', 'structural_level_coverage_v7'})
POLICY = 'live_market_ssd'


def literal(value):
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"


def bounds(day):
    return f"toUInt64(toUnixTimestamp64Micro(toDateTime64('{day} 00:00:00',6,'America/New_York')))"


def selection(build, day, ticker, attempt):
    return (f"build_id={literal(build)} AND session_date=toDate({literal(day)}) "
            f"AND ticker={literal(ticker)} AND attempt_id=toUUID({literal(attempt)})")


def _approved(statement):
    import re
    normalized = statement.strip()
    if not re.match(r'^SELECT\b', normalized, re.IGNORECASE):
        raise ValueError('RL trading SQL must be SELECT-only')
    if ';' in normalized or '--' in normalized or '/*' in normalized:
        raise ValueError('Multiple statements and SQL comments are forbidden')
    referenced = set(re.findall(r'\b(?:FROM|JOIN)\s+([a-zA-Z_][\w.]*)', normalized, re.IGNORECASE))
    allowed = {f'arte.{table}' for table in TABLES} | {
        'q_live.feature_tradable_universe_snapshot_v2',
        'q_live.market_security_float_v1', 'q_live.market_stock_split_v1',
        'system.tables', 'system.parts'}
    if not referenced or not referenced <= allowed:
        raise ValueError('RL trading SQL may read only arte products, certified population, and storage metadata')
    if referenced & {'system.tables', 'system.parts'} and not re.search(r"\bdatabase\s*=\s*'arte'(?:\s|$)", normalized, re.IGNORECASE):
        raise ValueError('Storage metadata reads must be restricted to arte')
    if re.search(r'\b(?:INSERT|ALTER|CREATE|DROP|TRUNCATE|OPTIMIZE|SETTINGS|INTO|OUTFILE|REMOTE|URL|FILE|MERGE)\b', normalized, re.IGNORECASE):
        raise ValueError('RL trading SQL contains a forbidden operation')
    return normalized


class ArteReader:
    def __init__(self, threads=2):
        self._client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(),
            default_clickhouse_password(), timeout_seconds=180,
            default_query_params=dict(readonly=1, max_execution_time=150, max_threads=threads,
                max_memory_usage=2147483648, max_result_rows=700000,
                max_result_bytes=100000000, result_overflow_mode='throw'))

    def execute(self, statement):
        return self._client.execute(_approved(statement))

    def close(self):
        self._client.close()


def query(client, statement):
    return [json.loads(line) for line in client.execute(_approved(statement) + ' FORMAT JSONEachRow').splitlines() if line]
