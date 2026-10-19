"""Account/side-specific observed permissions; never historical broker inference."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

from research.mlops.clickhouse import quote_ident, sql_string
from services.reference_gateway.providers import IbkrReferenceClient

EVIDENCE = "market_ibkr_account_eligibility_v1"
POLICY = "market_ibkr_live_account_v1"
LIVE_VIEW = "feature_live_tradable_universe_v1"


def rows(client, sql):
    return [json.loads(line) for line in client.execute(sql + " FORMAT JSONEachRow").splitlines() if line]


def insert(client, table, records):
    if records:
        client.execute("INSERT INTO " + table + " FORMAT JSONEachRow\n" +
                       "\n".join(json.dumps(r, sort_keys=True) for r in records))


def classify_rules(payload, account_id):
    """Absence/malformed data is unknown, never an affirmative permission."""
    if not isinstance(payload, dict) or payload.get("error"):
        return "unknown", "rules_unavailable"
    accounts = payload.get("canTradeAcctIds")
    if not isinstance(accounts, list) or any(not isinstance(a, str) or not a for a in accounts):
        return "unknown", "missing_or_invalid_canTradeAcctIds"
    return ("eligible", "account_permitted") if account_id in accounts else ("restricted", "account_not_permitted")


def choose_account(payload, configured):
    accounts = payload.get("accounts")
    if not isinstance(accounts, list) or not accounts or any(not isinstance(a, str) or not a for a in accounts):
        raise ValueError("IBKR trading accounts unavailable")
    if configured:
        if configured not in accounts:
            raise ValueError("Configured eligibility account is not authenticated")
        return configured
    if len(accounts) != 1:
        raise ValueError("Set REFERENCE_GATEWAY_IBKR_ACCOUNT_ID for a multi-account session")
    return accounts[0]


def ensure_schema(client, database):
    db = quote_ident(database)
    policy = rows(client, "SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'")
    if len(policy) != 1 or policy[0]["disks"] != ["live_market_ssd"]:
        raise RuntimeError("Eligibility requires live_market_ssd policy and disk")
    client.execute(f"""CREATE TABLE IF NOT EXISTS {db}.{EVIDENCE} (
        observed_date Date, account_id String, conid UInt64, side LowCardinality(String),
        observed_at_utc DateTime64(6,'UTC'), valid_until_utc DateTime64(6,'UTC'),
        status LowCardinality(String), reason String, response_json String,
        response_sha256 String, observation_id String, source_endpoint String
    ) ENGINE=MergeTree PARTITION BY toYYYYMM(observed_date)
    ORDER BY (account_id,conid,side,observed_at_utc,observation_id)
    SETTINGS storage_policy='live_market_ssd'""")
    client.execute(f"""CREATE TABLE IF NOT EXISTS {db}.{POLICY} (
        policy_id UInt8, account_id String, observed_at_utc DateTime64(6,'UTC'),
        valid_until_utc DateTime64(6,'UTC'), observation_id String
    ) ENGINE=MergeTree ORDER BY (policy_id,observed_at_utc,observation_id)
    SETTINGS storage_policy='live_market_ssd'""")
    for name in (EVIDENCE, POLICY):
        info = rows(client, f"SELECT storage_policy FROM system.tables WHERE database={sql_string(database)} AND name={sql_string(name)}")
        misplaced = rows(client, f"SELECT count() AS n FROM system.parts WHERE database={sql_string(database)} AND table={sql_string(name)} AND active AND disk_name!='live_market_ssd'")
        if not info or info[0]["storage_policy"] != "live_market_ssd" or misplaced[0]["n"]:
            raise RuntimeError("Eligibility table policy or part placement is invalid")
    def permitted(side):
        return f"""toUInt64OrZero(ifNull(u.ibkr_conid,'')) IN (
            SELECT conid FROM latest WHERE side='{side}' AND evidence.1='eligible'
            AND evidence.4<=now64(6) AND evidence.3>now64(6)
            AND (SELECT valid_until_utc FROM account)>now64(6))"""
    client.execute(f"""CREATE OR REPLACE VIEW {db}.{LIVE_VIEW} AS
    WITH account AS (
        SELECT account_id, valid_until_utc FROM {db}.{POLICY}
        ORDER BY observed_at_utc DESC,observation_id DESC LIMIT 1
    ), latest AS (
        SELECT account_id,conid,side,
            argMax(tuple(status,reason,valid_until_utc,observed_at_utc),
                   tuple(observed_at_utc,observation_id)) AS evidence
        FROM {db}.{EVIDENCE}
        WHERE account_id IN (SELECT account_id FROM account)
        GROUP BY account_id,conid,side
    )
    SELECT u.* EXCEPT(is_tradable,exclusion_reason),
        (SELECT account_id FROM account) AS eligibility_account_id,
        toUInt8(u.is_tradable=1 AND {permitted('buy')}) AS is_tradable,
        toUInt8(u.is_tradable=1 AND {permitted('sell')}) AS is_sell_eligible,
        if(u.is_tradable=0,u.exclusion_reason,
           if(is_tradable=1,CAST(NULL AS Nullable(String)),
              'ibkr_buy_restricted_unknown_or_stale')) AS exclusion_reason
    FROM {db}.feature_tradable_universe_v1 AS u FINAL
    WHERE universe_date=(SELECT max(universe_date) FROM {db}.feature_tradable_universe_v1)
    """)


def stamp(value):
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def invalidate_account(client, database, now=None):
    now = now or datetime.now(UTC)
    existing = rows(client, f"SELECT storage_policy FROM system.tables WHERE database={sql_string(database)} AND name={sql_string(POLICY)}")
    if existing:
        misplaced = rows(client, f"SELECT count() n FROM system.parts WHERE database={sql_string(database)} AND table={sql_string(POLICY)} AND active AND disk_name!='live_market_ssd'")
        if existing[0]["storage_policy"] != "live_market_ssd" or misplaced[0]["n"]:
            raise RuntimeError("Invalid live account policy storage")
        insert(client, f"{quote_ident(database)}.{POLICY}", [dict(policy_id=1,account_id="",
            observed_at_utc=stamp(now),valid_until_utc=stamp(now),observation_id=uuid.uuid4().hex)])


def sync_eligibility(client, database, broker, *, account_id="", batch_size=100,
                     max_age_seconds=21600, refresh_seconds=1800, request_interval=0.3,
                     clock=lambda: datetime.now(UTC), sleep=time.sleep, on_progress=None):
    """Bounded oldest-first sweep, durable each contract; failed checks supersede approvals."""
    if not 1 <= batch_size <= 500 or not 60 <= refresh_seconds < max_age_seconds <= 86400 or request_interval < 0.2:
        raise ValueError("Invalid bounded eligibility refresh settings")
    ensure_schema(client, database)
    db = quote_ident(database)
    now = clock()
    try:
        selected = choose_account(broker.trading_accounts(), account_id)
    except Exception:
        # Immediately invalidate the previous selected account on authentication/config failure.
        insert(client, f"{db}.{POLICY}", [dict(policy_id=1,account_id="",observed_at_utc=stamp(now),
            valid_until_utc=stamp(now),observation_id=uuid.uuid4().hex)])
        raise RuntimeError("IBKR eligibility account unavailable; live buy eligibility disabled") from None
    insert(client, f"{db}.{POLICY}", [dict(policy_id=1,account_id=selected,observed_at_utc=stamp(now),
        valid_until_utc=stamp(now+timedelta(seconds=max_age_seconds)),observation_id=uuid.uuid4().hex)])
    candidates = rows(client, f"""SELECT toUInt64OrZero(ifNull(u.ibkr_conid,'')) AS conid,
        ifNull(e.checked,toDateTime64('1970-01-01',6,'UTC')) AS checked
        FROM {db}.feature_tradable_universe_v1 u FINAL
        LEFT JOIN (SELECT conid,min(latest_check) AS checked FROM (
            SELECT conid,side,max(observed_at_utc) AS latest_check FROM {db}.{EVIDENCE}
            WHERE account_id={sql_string(selected)} GROUP BY conid,side
        ) GROUP BY conid HAVING count()=2) e ON e.conid=toUInt64OrZero(ifNull(u.ibkr_conid,''))
        WHERE u.universe_date=(SELECT max(universe_date) FROM {db}.feature_tradable_universe_v1)
        AND u.is_tradable=1 AND conid>0
        AND checked<toDateTime64({sql_string(stamp(now-timedelta(seconds=refresh_seconds)))},6,'UTC')
        GROUP BY conid,checked ORDER BY checked,conid LIMIT {int(batch_size)}""")
    totals = dict(checked=0,eligible=0,restricted=0,unknown=0)
    if on_progress:
        on_progress(dict(totals, queued=len(candidates)))
    for candidate in candidates:
        records = []
        for side in ("buy", "sell"):
            sleep(request_interval)
            try:
                payload = broker.contract_rules(candidate["conid"], is_buy=side == "buy")
                status, reason = classify_rules(payload, selected)
            except Exception:
                payload, status, reason = {}, "unknown", "request_failed"
            observed = clock()
            raw = json.dumps(payload,sort_keys=True,separators=(",", ":"))
            records.append(dict(observed_date=observed.date().isoformat(),account_id=selected,
                conid=int(candidate["conid"]),side=side,observed_at_utc=stamp(observed),
                valid_until_utc=stamp(observed+timedelta(seconds=max_age_seconds)),status=status,
                reason=reason,response_json=raw,response_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                observation_id=uuid.uuid4().hex,source_endpoint="/iserver/contract/rules"))
            totals[status] += 1
            insert(client, f"{db}.{EVIDENCE}", [records[-1]])
        totals["checked"] += 1
        if on_progress:
            on_progress(dict(totals, queued=len(candidates)-totals["checked"]))
    return totals


def run_gateway_eligibility(config, *, on_progress=None):
    from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password
    return sync_eligibility(
        ClickHouseHttpClient(config.clickhouse_url,config.clickhouse_user,default_clickhouse_password()),
        config.clickhouse_write_database, IbkrReferenceClient(base_url=config.ibkr_base_url),
        account_id=os.environ.get("REFERENCE_GATEWAY_IBKR_ACCOUNT_ID", ""),
        batch_size=int(os.environ.get("REFERENCE_GATEWAY_IBKR_ELIGIBILITY_BATCH_SIZE", "100")),
        max_age_seconds=int(os.environ.get("REFERENCE_GATEWAY_IBKR_ELIGIBILITY_MAX_AGE_SECONDS", "21600")),
        refresh_seconds=int(os.environ.get("REFERENCE_GATEWAY_IBKR_ELIGIBILITY_REFRESH_SECONDS", "1800")),
        on_progress=on_progress)
