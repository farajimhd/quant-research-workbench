//! Normalized receipt contract for QMD-accepted compact-event batches.
//!
//! A batch can contain sparse global arrival sequences because QMD reorders
//! by ticker before persistence. These rows describe exact membership, not a
//! fictitious complete interval. They do not certify upstream packet delivery,
//! fence old ClickHouse INSERTs, or authorize live trading by themselves.

use crate::compact_event::LiveCompactEvent;
use chrono::{NaiveDate, NaiveDateTime};
use ring::digest::{digest, SHA256};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

pub const BATCH_TABLE: &str = "strategy_one_source_batch_v1";
pub const MEMBER_TABLE: &str = "strategy_one_source_member_v1";
pub const EVENT_TABLE: &str = "strategy_one_source_event_v1";

/// Strategy 1's exact live source is separate from the older q_live.events
/// ReplacingMergeTree, whose sorting key can merge distinct arrivals.
pub fn event_table_sql() -> String {
    format!(
        "CREATE TABLE IF NOT EXISTS {EVENT_TABLE} (\
         source_date Date, producer_epoch FixedString(32), arrival_sequence UInt64, \
         schema_version UInt16, ingest_ts DateTime64(3, 'UTC'), \
         ticker LowCardinality(String), event_meta UInt8, \
         execution_timestamp_us UInt64, sip_timestamp_us UInt64, \
         price_primary_int UInt32, price_secondary_int UInt32, \
         size_primary Float32, size_secondary Float32, \
         exchange_primary UInt8, exchange_secondary UInt8, \
         condition_token_1 UInt8, condition_token_2 UInt8, condition_token_3 UInt8, \
         condition_token_4 UInt8, condition_token_5 UInt8, \
         source_sequence UInt64, issue_flags UInt16) \
         ENGINE = ReplacingMergeTree(ingest_ts) \
         PARTITION BY toYYYYMM(source_date) \
         ORDER BY (source_date, producer_epoch, ticker, arrival_sequence) \
         SETTINGS storage_policy = 'live_market_ssd', \
         non_replicated_deduplication_window = 1000"
    )
}

pub fn batch_table_sql() -> String {
    format!(
        "CREATE TABLE IF NOT EXISTS {BATCH_TABLE} (\
         source_date Date, producer_epoch FixedString(32), batch_id FixedString(64), \
         event_count UInt32, first_arrival_sequence UInt64, last_arrival_sequence UInt64, \
         acknowledged_at DateTime64(6, 'UTC')) \
         ENGINE = MergeTree PARTITION BY toYYYYMM(source_date) \
         ORDER BY (source_date, producer_epoch, batch_id) \
         SETTINGS storage_policy = 'live_market_ssd', \
         non_replicated_deduplication_window = 1000"
    )
}

pub fn member_table_sql() -> String {
    format!(
        "CREATE TABLE IF NOT EXISTS {MEMBER_TABLE} (\
         source_date Date, producer_epoch FixedString(32), \
         arrival_sequence UInt64, batch_id FixedString(64), \
         ticker LowCardinality(String), canonical_row_hash FixedString(64)) \
         ENGINE = MergeTree PARTITION BY toYYYYMM(source_date) \
         ORDER BY (source_date, producer_epoch, arrival_sequence) \
         SETTINGS storage_policy = 'live_market_ssd', \
         non_replicated_deduplication_window = 1000"
    )
}

/// Validate physical storage before QMD admits a receipt writer. A table
/// setting alone is insufficient when older active parts remain elsewhere.
pub fn verify_storage_contract(tables: &str, parts: &str) -> Result<(), &'static str> {
    let mut seen = BTreeSet::new();
    for line in tables.lines() {
        let fields = line.split('\t').collect::<Vec<_>>();
        if fields.len() != 5
            || !seen.insert(fields[0])
            || fields[1] != (if fields[0] == EVENT_TABLE {
                "ReplacingMergeTree"
            } else { "MergeTree" })
            || fields[2] != "toYYYYMM(source_date)"
            || fields[4] != "live_market_ssd"
            || (fields[0] == BATCH_TABLE && fields[3] != "source_date, producer_epoch, batch_id")
            || (fields[0] == MEMBER_TABLE
                && fields[3] != "source_date, producer_epoch, arrival_sequence")
            || (fields[0] == EVENT_TABLE
                && fields[3] != "source_date, producer_epoch, ticker, arrival_sequence")
        {
            return Err("Strategy 1 source receipt table contract or SSD policy differs");
        }
    }
    if seen != BTreeSet::from([BATCH_TABLE, MEMBER_TABLE, EVENT_TABLE]) {
        return Err("Strategy 1 source receipt table contract or SSD policy differs");
    }
    for line in parts.lines() {
        let fields = line.split('\t').collect::<Vec<_>>();
        if fields.len() != 2 || !seen.contains(fields[0])
            || fields[1] != "live_market_ssd" {
            return Err("Strategy 1 source receipt has active parts outside live_market_ssd");
        }
    }
    Ok(())
}

pub fn verify_event_columns(rows: &str) -> Result<(), &'static str> {
    const COLUMNS: [(&str, &str); 21] = [
        ("source_date", "Date"), ("producer_epoch", "FixedString(32)"),
        ("arrival_sequence", "UInt64"), ("schema_version", "UInt16"),
        ("ingest_ts", "DateTime64(3, 'UTC')"),
        ("ticker", "LowCardinality(String)"), ("event_meta", "UInt8"),
        ("execution_timestamp_us", "UInt64"), ("sip_timestamp_us", "UInt64"),
        ("price_primary_int", "UInt32"), ("price_secondary_int", "UInt32"),
        ("size_primary", "Float32"), ("size_secondary", "Float32"),
        ("exchange_primary", "UInt8"), ("exchange_secondary", "UInt8"),
        ("condition_token_1", "UInt8"), ("condition_token_2", "UInt8"),
        ("condition_token_3", "UInt8"), ("condition_token_4", "UInt8"),
        ("condition_token_5", "UInt8"), ("source_sequence", "UInt64"),
    ];
    let actual = rows.lines().map(|row| row.split_once('\t'))
        .collect::<Option<Vec<_>>>()
        .ok_or("Strategy 1 source event has malformed columns")?;
    if actual.len() != COLUMNS.len() + 1
        || actual[..COLUMNS.len()] != COLUMNS
        || actual[COLUMNS.len()] != ("issue_flags", "UInt16") {
        return Err("Strategy 1 source event has incompatible columns");
    }
    Ok(())
}

fn hex_hash(bytes: &[u8]) -> String {
    digest(&SHA256, bytes)
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Hash canonical scalar values, excluding the ingest clock. The source read
/// must decode the same typed fields and compare this digest after readback.
pub fn canonical_row_hash(event: &LiveCompactEvent) -> String {
    let wire = serde_json::json!([
        event.event_date,
        event.schema_version,
        event.arrival_sequence,
        event.ticker,
        event.event_meta,
        event.execution_timestamp_us,
        event.sip_timestamp_us,
        event.price_primary_int,
        event.price_secondary_int,
        event.size_primary,
        event.size_secondary,
        event.exchange_primary,
        event.exchange_secondary,
        event.condition_token_1,
        event.condition_token_2,
        event.condition_token_3,
        event.condition_token_4,
        event.condition_token_5,
        event.source_sequence,
        event.issue_flags,
    ]);
    hex_hash(wire.to_string().as_bytes())
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct BatchReceipt {
    pub source_date: String,
    pub producer_epoch: String,
    pub batch_id: String,
    pub event_count: u32,
    pub first_arrival_sequence: u64,
    pub last_arrival_sequence: u64,
    pub acknowledged_at: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct MemberReceipt {
    pub source_date: String,
    pub producer_epoch: String,
    pub arrival_sequence: u64,
    pub batch_id: String,
    pub ticker: String,
    pub canonical_row_hash: String,
}

pub fn prepare_receipts(
    producer_epoch: &str,
    acknowledged_at: &str,
    events: &[LiveCompactEvent],
) -> Result<(Vec<BatchReceipt>, Vec<MemberReceipt>), &'static str> {
    if producer_epoch.len() != 32
        || !producer_epoch
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || NaiveDateTime::parse_from_str(acknowledged_at, "%Y-%m-%d %H:%M:%S%.6f")
            .map(|clock| clock.format("%Y-%m-%d %H:%M:%S%.6f").to_string() != acknowledged_at)
            .unwrap_or(true)
        || events.is_empty()
        || events.len() > 100_000
    {
        return Err("source receipt needs a bounded epoch, clock, and event batch");
    }
    let mut grouped: BTreeMap<String, Vec<(u64, String, String)>> = BTreeMap::new();
    let mut seen = BTreeSet::new();
    for event in events {
        if event.arrival_sequence == 0
            || !seen.insert(event.arrival_sequence)
            || NaiveDate::parse_from_str(&event.event_date, "%Y-%m-%d")
                .map(|day| day.to_string() != event.event_date)
                .unwrap_or(true)
            || event.ticker.is_empty()
            || !event.size_primary.is_finite()
            || !event.size_secondary.is_finite()
            || !event.ticker.bytes().all(|byte| {
                byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'.' || byte == b'-'
            })
        {
            return Err("source receipt has duplicate or invalid event identity");
        }
        grouped.entry(event.event_date.clone()).or_default().push((
            event.arrival_sequence,
            event.ticker.clone(),
            canonical_row_hash(event),
        ));
    }
    let mut batches = Vec::with_capacity(grouped.len());
    let mut members = Vec::with_capacity(events.len());
    for (day, mut rows) in grouped {
        rows.sort_unstable_by_key(|row| row.0);
        let identity = serde_json::to_string(&(producer_epoch, &day, &rows))
            .map_err(|_| "source receipt identity cannot be serialized")?;
        let batch_id = hex_hash(identity.as_bytes());
        batches.push(BatchReceipt {
            source_date: day.clone(),
            producer_epoch: producer_epoch.to_owned(),
            batch_id: batch_id.clone(),
            event_count: rows.len() as u32,
            first_arrival_sequence: rows[0].0,
            last_arrival_sequence: rows[rows.len() - 1].0,
            acknowledged_at: acknowledged_at.to_owned(),
        });
        members.extend(
            rows.into_iter()
                .map(|(sequence, ticker, hash)| MemberReceipt {
                    source_date: day.clone(),
                    producer_epoch: producer_epoch.to_owned(),
                    arrival_sequence: sequence,
                    batch_id: batch_id.clone(),
                    ticker,
                    canonical_row_hash: hash,
                }),
        );
    }
    Ok((batches, members))
}

/// Verify a complete, bounded cold read of one batch. Callers must query
/// member and event inventories with `LIMIT event_count + 1` and reject the
/// overflow row; this function never treats a partial page as complete.
pub fn verify_batch_readback(
    batch: &BatchReceipt,
    members: &[MemberReceipt],
    events: &[LiveCompactEvent],
) -> Result<(), &'static str> {
    if batch.event_count == 0
        || batch.event_count > 100_000
        || members.len() != batch.event_count as usize
        || events.len() != batch.event_count as usize
        || events.iter().any(|row| row.event_date != batch.source_date)
    {
        return Err("source batch readback has incomplete or foreign rows");
    }
    let (reconstructed, mut expected) = prepare_receipts(
        &batch.producer_epoch, &batch.acknowledged_at, events,
    )?;
    if reconstructed.len() != 1 || &reconstructed[0] != batch {
        return Err("source batch readback differs from the acknowledged batch");
    }
    let mut actual = members.to_vec();
    actual.sort_unstable_by_key(|row| row.arrival_sequence);
    expected.sort_unstable_by_key(|row| row.arrival_sequence);
    if actual != expected {
        return Err("source batch readback differs from exact member hashes");
    }
    Ok(())
}

pub fn batch_readback_sql(
    source_date: &str, epoch: &str, batch_id: &str,
) -> Result<String, &'static str> {
    if NaiveDate::parse_from_str(source_date, "%Y-%m-%d")
        .map(|day| day.to_string() != source_date).unwrap_or(true)
        || !valid_hex(epoch, 32) || !valid_hex(batch_id, 64) {
        return Err("source batch readback has invalid identity");
    }
    Ok(format!(
        "SELECT source_date,producer_epoch,batch_id,event_count,\
         first_arrival_sequence,last_arrival_sequence,\
         toString(acknowledged_at) AS acknowledged_at \
         FROM {BATCH_TABLE} WHERE source_date='{source_date}' \
         AND producer_epoch='{epoch}' AND batch_id='{batch_id}' \
         LIMIT 2 SETTINGS output_format_json_quote_64bit_integers=0 \
         FORMAT JSONEachRow"
    ))
}

pub fn member_readback_sql(batch: &BatchReceipt) -> Result<String, &'static str> {
    batch_readback_sql(&batch.source_date, &batch.producer_epoch, &batch.batch_id)?;
    let limit = checked_readback_limit(batch.event_count)?;
    Ok(format!(
        "SELECT source_date,producer_epoch,arrival_sequence,batch_id,ticker,\
         canonical_row_hash FROM {MEMBER_TABLE} \
         WHERE source_date='{day}' AND producer_epoch='{epoch}' \
         AND batch_id='{batch_id}' ORDER BY arrival_sequence LIMIT {limit} \
         SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow",
        day=batch.source_date, epoch=batch.producer_epoch, batch_id=batch.batch_id,
    ))
}

pub fn event_readback_sql(batch: &BatchReceipt) -> Result<String, &'static str> {
    batch_readback_sql(&batch.source_date, &batch.producer_epoch, &batch.batch_id)?;
    let limit = checked_readback_limit(batch.event_count)?;
    Ok(format!(
        "SELECT source_date AS event_date,schema_version,\
         concat(replaceOne(toString(ingest_ts),' ','T'),'Z') AS ingest_ts,\
         arrival_sequence,ticker,event_meta,execution_timestamp_us,\
         sip_timestamp_us,price_primary_int,price_secondary_int,\
         size_primary,size_secondary,exchange_primary,exchange_secondary,\
         condition_token_1,condition_token_2,condition_token_3,\
         condition_token_4,condition_token_5,source_sequence,issue_flags \
         FROM {EVENT_TABLE} FINAL WHERE source_date='{day}' \
         AND producer_epoch='{epoch}' AND (ticker,arrival_sequence) IN \
         (SELECT ticker,arrival_sequence FROM {MEMBER_TABLE} \
         WHERE source_date='{day}' AND producer_epoch='{epoch}' \
         AND batch_id='{batch_id}') \
         ORDER BY arrival_sequence LIMIT {limit} \
         SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow",
        day=batch.source_date, epoch=batch.producer_epoch, batch_id=batch.batch_id,
    ))
}

fn valid_hex(value: &str, width: usize) -> bool {
    value.len() == width && value.bytes().all(|byte| {
        byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
    })
}

fn checked_readback_limit(count: u32) -> Result<u32, &'static str> {
    if !(1..=100_000).contains(&count) {
        return Err("source batch readback exceeds bounded inventory");
    }
    Ok(count + 1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    fn event(day: &str, ticker: &str, sequence: u64) -> LiveCompactEvent {
        LiveCompactEvent::from_persisted_fields(
            sequence,
            0,
            0,
            0,
            0,
            0,
            day.to_owned(),
            0,
            1,
            1,
            1,
            Utc::now(),
            0,
            100,
            100,
            6,
            1,
            1.0,
            1.0,
            sequence,
            ticker.to_owned(),
        )
    }

    #[test]
    fn exact_membership_does_not_invent_sequence_between_extrema() {
        let epoch = "0123456789abcdef0123456789abcdef";
        let (batches, members) = prepare_receipts(
            epoch,
            "2026-08-18 12:00:00.000000",
            &[event("2026-08-18", "A", 3), event("2026-08-18", "B", 1)],
        )
        .unwrap();
        assert_eq!(batches.len(), 1);
        assert_eq!(batches[0].event_count, 2);
        assert_eq!(batches[0].first_arrival_sequence, 1);
        assert_eq!(batches[0].last_arrival_sequence, 3);
        assert_eq!(
            members
                .iter()
                .map(|row| row.arrival_sequence)
                .collect::<Vec<_>>(),
            vec![1, 3]
        );
        assert!(members
            .iter()
            .all(|row| row.batch_id == batches[0].batch_id));
    }

    #[test]
    fn dates_partition_batches_and_duplicate_sequences_fail() {
        let epoch = "0123456789abcdef0123456789abcdef";
        let (batches, members) = prepare_receipts(
            epoch,
            "2026-08-18 12:00:00.000000",
            &[event("2026-08-19", "A", 4), event("2026-08-18", "A", 3)],
        )
        .unwrap();
        assert_eq!(batches.len(), 2);
        assert_eq!(members.len(), 2);
        assert!(prepare_receipts(
            epoch,
            "2026-08-18 12:00:00.000000",
            &[event("2026-08-18", "A", 1), event("2026-08-18", "B", 1)]
        )
        .is_err());
        assert!(prepare_receipts(epoch, "clock", &[event("2026-08-18", "A", 1)]).is_err());
    }

    #[test]
    fn source_tables_are_typed_and_ssd_placed() {
        for sql in [batch_table_sql(), member_table_sql(), event_table_sql()] {
            assert!(sql.contains("storage_policy = 'live_market_ssd'"));
            assert!(sql.contains("PARTITION BY toYYYYMM(source_date)"));
            assert!(!sql.to_lowercase().contains("json"));
        }
        let source = event_table_sql();
        assert!(source.contains(
            "ORDER BY (source_date, producer_epoch, ticker, arrival_sequence)"));
        assert!(source.contains("ENGINE = ReplacingMergeTree(ingest_ts)"));
        for field in ["execution_timestamp_us", "sip_timestamp_us",
                      "source_sequence", "issue_flags", "condition_token_5"] {
            assert!(source.contains(field));
        }
    }

    #[test]
    fn storage_contract_rejects_missing_or_off_disk_parts() {
        let tables = format!(
            "{BATCH_TABLE}\tMergeTree\ttoYYYYMM(source_date)\tsource_date, producer_epoch, batch_id\tlive_market_ssd\n\
             {MEMBER_TABLE}\tMergeTree\ttoYYYYMM(source_date)\tsource_date, producer_epoch, arrival_sequence\tlive_market_ssd\n\
             {EVENT_TABLE}\tReplacingMergeTree\ttoYYYYMM(source_date)\tsource_date, producer_epoch, ticker, arrival_sequence\tlive_market_ssd\n"
        );
        assert!(verify_storage_contract(&tables, &format!("{EVENT_TABLE}\tlive_market_ssd\n")).is_ok());
        assert!(verify_storage_contract(&tables, &format!("{EVENT_TABLE}\tdefault\n")).is_err());
        assert!(verify_storage_contract(&tables, "foreign\tlive_market_ssd\n").is_err());
        assert!(verify_storage_contract(&tables, &format!("{EVENT_TABLE}\tlive_market_ssd\textra\n")).is_err());
        assert!(verify_storage_contract(&tables.replace("MergeTree", "Memory"), "").is_err());
        assert!(verify_storage_contract(&tables.lines().next().unwrap().to_owned(), "").is_err());
    }

    #[test]
    fn source_event_columns_are_exact_and_ordered() {
        let columns = [
            "source_date\tDate", "producer_epoch\tFixedString(32)",
            "arrival_sequence\tUInt64", "schema_version\tUInt16",
            "ingest_ts\tDateTime64(3, 'UTC')", "ticker\tLowCardinality(String)",
            "event_meta\tUInt8", "execution_timestamp_us\tUInt64",
            "sip_timestamp_us\tUInt64", "price_primary_int\tUInt32",
            "price_secondary_int\tUInt32", "size_primary\tFloat32",
            "size_secondary\tFloat32", "exchange_primary\tUInt8",
            "exchange_secondary\tUInt8", "condition_token_1\tUInt8",
            "condition_token_2\tUInt8", "condition_token_3\tUInt8",
            "condition_token_4\tUInt8", "condition_token_5\tUInt8",
            "source_sequence\tUInt64", "issue_flags\tUInt16",
        ].join("\n");
        assert!(verify_event_columns(&columns).is_ok());
        assert!(verify_event_columns(&columns.replace("arrival_sequence\tUInt64",
                                                      "arrival_sequence\tString")).is_err());
        assert!(verify_event_columns(&columns.replace("issue_flags\tUInt16", "")).is_err());
    }

    #[test]
    fn canonical_digest_ignores_ingest_clock_but_not_market_values() {
        let first = event("2026-08-18", "A", 1);
        let mut second = first.clone();
        second.ingest_ts = first.ingest_ts + chrono::Duration::seconds(1);
        assert_eq!(canonical_row_hash(&first), canonical_row_hash(&second));
        second.price_primary_int += 1;
        assert_ne!(canonical_row_hash(&first), canonical_row_hash(&second));
    }

    #[test]
    fn exact_cold_readback_rejects_missing_extra_and_mutated_arrivals() {
        let epoch = "0123456789abcdef0123456789abcdef";
        let source = [event("2026-08-18", "A", 3),
                      event("2026-08-18", "B", 1)];
        let (batches, members) = prepare_receipts(
            epoch, "2026-08-18 12:00:00.000000", &source).unwrap();
        let batch = &batches[0];
        assert!(verify_batch_readback(batch, &members, &source).is_ok());
        assert!(verify_batch_readback(batch, &members[..1], &source).is_err());
        assert!(verify_batch_readback(batch, &members, &source[..1]).is_err());
        let mut extra = source.to_vec();
        extra.push(event("2026-08-18", "C", 4));
        assert!(verify_batch_readback(batch, &members, &extra).is_err());
        let mut mutated = source.to_vec();
        mutated[0].price_primary_int += 1;
        assert!(verify_batch_readback(batch, &members, &mutated).is_err());
        let mut wrong_epoch = members.clone();
        wrong_epoch[0].producer_epoch = "fedcba9876543210fedcba9876543210".into();
        assert!(verify_batch_readback(batch, &wrong_epoch, &source).is_err());
    }

    #[test]
    fn cold_queries_are_identity_pinned_and_bounded() {
        let epoch = "0123456789abcdef0123456789abcdef";
        let (batches, _) = prepare_receipts(
            epoch, "2026-08-18 12:00:00.000000",
            &[event("2026-08-18", "A", 1)]).unwrap();
        let batch = &batches[0];
        assert!(batch_readback_sql("2026-08-18", epoch, &batch.batch_id)
            .unwrap().contains("LIMIT 2"));
        let member = member_readback_sql(batch).unwrap();
        let source = event_readback_sql(batch).unwrap();
        for sql in [&member, &source] {
            assert!(sql.contains("LIMIT 2"));
            assert!(sql.contains(&format!("batch_id='{}'", batch.batch_id)));
            assert!(sql.contains(&format!("producer_epoch='{epoch}'")));
        }
        assert!(source.contains("FROM strategy_one_source_event_v1 FINAL"));
        assert!(batch_readback_sql("2026-08-18' OR 1=1", epoch,
                                   &batch.batch_id).is_err());
        assert!(batch_readback_sql("2026-08-18", epoch,
                                   "f'. OR 1=1").is_err());
        let mut oversized = batch.clone();
        oversized.event_count = 100_001;
        assert!(event_readback_sql(&oversized).is_err());
    }
}
