//! Normalized receipt contract for QMD-accepted compact-event batches.
//!
//! A batch can contain sparse global arrival sequences because QMD reorders
//! by ticker before persistence. These rows describe exact membership, not a
//! fictitious complete interval. They do not certify upstream packet delivery,
//! fence old ClickHouse INSERTs, or authorize live trading by themselves.

use crate::compact_event::LiveCompactEvent;
use chrono::{NaiveDate, NaiveDateTime};
use ring::digest::{digest, SHA256};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

pub const BATCH_TABLE: &str = "strategy_one_source_batch_v1";
pub const MEMBER_TABLE: &str = "strategy_one_source_member_v1";

pub fn batch_table_sql() -> String {
    format!(
        "CREATE TABLE IF NOT EXISTS {BATCH_TABLE} (\
         source_date Date, producer_epoch FixedString(32), batch_id FixedString(64), \
         event_count UInt32, first_arrival_sequence UInt64, last_arrival_sequence UInt64, \
         acknowledged_at DateTime64(6, 'UTC')) \
         ENGINE = MergeTree PARTITION BY toYYYYMM(source_date) \
         ORDER BY (source_date, producer_epoch, batch_id) \
         SETTINGS storage_policy = 'live_market_ssd'"
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
         SETTINGS storage_policy = 'live_market_ssd'"
    )
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

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct BatchReceipt {
    pub source_date: String,
    pub producer_epoch: String,
    pub batch_id: String,
    pub event_count: u32,
    pub first_arrival_sequence: u64,
    pub last_arrival_sequence: u64,
    pub acknowledged_at: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
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
    fn both_tables_are_typed_and_ssd_placed() {
        for sql in [batch_table_sql(), member_table_sql()] {
            assert!(sql.contains("storage_policy = 'live_market_ssd'"));
            assert!(sql.contains("PARTITION BY toYYYYMM(source_date)"));
            assert!(!sql.to_lowercase().contains("json"));
        }
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
}
