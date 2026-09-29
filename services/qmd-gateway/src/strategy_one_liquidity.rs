//! Completed 100 ms liquidity projection for Strategy 1.
//!
//! Mirrors `arte.liquidity_100ms_v1` field semantics. This reducer does not
//! write market tables or synthesize intra-bucket event order. Its caller must
//! provide the canonical ordered compact lane and publish rows only after the
//! bucket boundary is complete.

use crate::bars::{TradeAggregationRules, REGULAR_SESSION_END_SECONDS, REGULAR_SESSION_START_SECONDS};
use crate::compact_event::{CompactEventDecoder, LiveCompactEvent, TRADE_EVENT_TYPE};
use crate::intraday_bars::{event_identity, EventIdentity};
use crate::strategy_one_trade_reporting::DELAYED;
use chrono::{Datelike, Timelike};
use chrono_tz::America::New_York;
use serde::Serialize;
use std::collections::HashSet;

const BUCKET_US: u64 = 100_000;
const START_US: u64 = 4 * 3_600_000_000;
const END_US: u64 = 20 * 3_600_000_000;
const PRE_0405_US: u64 = 4 * 3_600_000_000 + 5 * 60_000_000;
const MAX_QUOTE_AGE_US: u64 = 1_000_000;
const MAX_BUCKET_SOURCE_EVENTS: usize = 100_000;

#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct CompletedLiquidityBucket {
    pub session_date: String,
    pub ticker: String,
    pub resolution_ms: u32,
    pub bucket_index: u32,
    pub bucket_end_us: u64,
    pub first_event_us: u64,
    pub last_event_us: u64,
    pub source_sequence: u64,
    pub event_count: u32,
    pub source_trade_count: u32,
    pub quote_event_count: u32,
    pub reporting_delayed_trades: u32,
    pub volume_ineligible_trades: u32,
    pub price_ineligible_trades: u32,
    pub execution_ineligible_trades: u32,
    pub pre_0405_trades: u32,
    pub pre_0405_volume_eligible_trades: u32,
    pub invalid_trade_values: u32,
    pub open_int: u64,
    pub high_int: u64,
    pub low_int: u64,
    pub close_int: u64,
    pub volume: f64,
    pub trade_count: u32,
    pub notional: f64,
    pub execution_volume: f64,
    pub execution_notional: f64,
    pub price_valid: u8,
    pub extremes_valid: u8,
    pub volume_valid: u8,
    pub quote_timestamp_us: u64,
    pub bid_int: u64,
    pub ask_int: u64,
    pub bid_size: f64,
    pub ask_size: f64,
    pub cumulative_volume: f64,
    pub cumulative_notional: f64,
    pub cumulative_execution_volume: f64,
    pub cumulative_execution_notional: f64,
    pub execution_vwap: f64,
    pub spread: f64,
    pub quote_valid: u8,
    // Transient source receipts, never part of the public or persisted row.
    #[serde(skip)]
    pub(crate) source_arrival_sequences: Vec<u64>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum LiquidityUpdate {
    Completed { row: CompletedLiquidityBucket },
    Invalidated { ticker: String, reason: String },
}

#[derive(Clone, Copy, Debug, Default)]
struct Quote {
    timestamp_us: u64,
    bid_int: u64,
    ask_int: u64,
    bid_size: f64,
    ask_size: f64,
}

#[derive(Default)]
pub struct LiquidityReducer {
    ticker: Option<String>,
    session_date: Option<String>,
    last_key: Option<(u64, u64, u8, u64)>,
    pending: Option<CompletedLiquidityBucket>,
    pending_seen: HashSet<EventIdentity>,
    quote: Option<Quote>,
    cumulative_volume: f64,
    cumulative_notional: f64,
    cumulative_execution_volume: f64,
    cumulative_execution_notional: f64,
}

impl LiquidityReducer {
    /// A different ticker requires a different reducer, preserving independent
    /// causal state and allowing bounded parallel lanes upstream.
    pub fn push(
        &mut self,
        event: &LiveCompactEvent,
        decoder: &CompactEventDecoder,
        rules: &TradeAggregationRules,
    ) -> Result<Option<CompletedLiquidityBucket>, String> {
        if event.schema_version < 6 {
            return Err("Strategy 1 liquidity requires classified compact-event v6".into());
        }
        if self.ticker.as_ref().is_some_and(|ticker| ticker != &event.ticker) {
            return Err("Strategy 1 liquidity reducer received another ticker".into());
        }
        self.ticker.get_or_insert_with(|| event.ticker.clone());
        let key = (event.sip_timestamp_us, event.source_sequence, event.event_type(), event.arrival_sequence);
        if self.last_key.is_some_and(|last| key < last) {
            return Err("Strategy 1 liquidity input is out of canonical order".into());
        }
        self.last_key = Some(key);
        let (date, local_us) = local_coordinates(event.sip_timestamp_us)?;
        if !(START_US..END_US).contains(&local_us) {
            // An ordered later source event can close the final in-session
            // bucket even though that event does not belong in the session.
            // Silence or a wall-clock tick cannot make the same claim.
            let mut completed = self.take_completed_through(event.sip_timestamp_us);
            if let Some(row) = completed.as_mut() {
                // The later event is the evidence that closes this bucket. Its
                // source receipt must be durable before the row is published.
                row.source_arrival_sequences.push(event.arrival_sequence);
            }
            return Ok(completed);
        }
        let mut completed = None;
        if self.session_date.as_ref().is_some_and(|prior| prior != &date) {
            completed = self.pending.take();
            self.pending_seen.clear();
            self.quote = None;
            self.cumulative_volume = 0.0;
            self.cumulative_notional = 0.0;
            self.cumulative_execution_volume = 0.0;
            self.cumulative_execution_notional = 0.0;
        }
        self.session_date = Some(date.clone());
        let index = ((local_us - START_US) / BUCKET_US) as u32;
        if self.pending.as_ref().is_some_and(|row| row.bucket_index != index) {
            completed = self.pending.take();
            self.pending_seen.clear();
        }
        if let Some(row) = completed.as_mut() {
            // Do not publish a completed bucket before the closing event has
            // itself reached canonical persistence and coverage authority.
            row.source_arrival_sequences.push(event.arrival_sequence);
        }
        // Match ordinary bars' canonical identity. A replayed source event
        // must not add a second trade or grant broker liquidity twice.
        if !self.pending_seen.insert(event_identity(event)) {
            return Ok(completed);
        }
        let row = self.pending.get_or_insert_with(|| CompletedLiquidityBucket {
            session_date: date,
            ticker: event.ticker.clone(),
            resolution_ms: 100,
            bucket_index: index,
            bucket_end_us: event.sip_timestamp_us - (local_us - START_US) % BUCKET_US + BUCKET_US,
            first_event_us: event.sip_timestamp_us,
            ..CompletedLiquidityBucket::default()
        });
        row.cumulative_volume = self.cumulative_volume;
        row.cumulative_notional = self.cumulative_notional;
        row.cumulative_execution_volume = self.cumulative_execution_volume;
        row.cumulative_execution_notional = self.cumulative_execution_notional;
        row.execution_vwap = if self.cumulative_execution_volume > 0.0 {
            self.cumulative_execution_notional / self.cumulative_execution_volume
        } else { 0.0 };
        row.last_event_us = event.sip_timestamp_us;
        row.source_sequence = event.source_sequence;
        row.event_count = row.event_count.saturating_add(1);
        if row.source_arrival_sequences.len() >= MAX_BUCKET_SOURCE_EVENTS {
            return Err("Strategy 1 liquidity bucket exceeded its source-receipt budget".into());
        }
        row.source_arrival_sequences.push(event.arrival_sequence);
        // ARTE's windowed q tuple carries the last valid quote into every
        // later event-bearing bucket, including a bucket with trades only.
        // Match that row contract independently of quote age; the consumer
        // applies freshness at the completed boundary.
        self.project_quote();
        if event.event_type() == TRADE_EVENT_TYPE {
            self.apply_trade(event, decoder, rules, local_us)?;
        } else {
            self.apply_quote(event);
        }
        Ok(completed)
    }

    /// Only the reducer's ordered next source event may close a bucket here.
    /// Do not expose this as a public wall-clock or guessed-watermark flush:
    /// sparse completion needs a separate source-certified watermark contract.
    fn take_completed_through(&mut self, watermark_us: u64) -> Option<CompletedLiquidityBucket> {
        if self.pending.as_ref().is_some_and(|row| row.bucket_end_us <= watermark_us) {
            self.pending_seen.clear();
            self.pending.take()
        } else {
            None
        }
    }

    fn apply_quote(&mut self, event: &LiveCompactEvent) {
        let ask = price_int(event.price_primary_int, event.event_meta & 2 != 0);
        let bid = price_int(event.price_secondary_int, event.event_meta & 4 != 0);
        let ask_size = f64::from(event.size_primary);
        let bid_size = f64::from(event.size_secondary);
        let row = self.pending.as_mut().expect("pending liquidity bucket");
        row.quote_event_count = row.quote_event_count.saturating_add(1);
        if bid == 0 || ask == 0 || ask < bid || !ask_size.is_finite() || !bid_size.is_finite()
            || ask_size < 0.0 || bid_size < 0.0 {
            self.project_quote();
            return;
        }
        let quote = Quote { timestamp_us: event.sip_timestamp_us, bid_int: bid, ask_int: ask, bid_size, ask_size };
        self.quote = Some(quote);
        self.project_quote();
    }

    fn apply_trade(
        &mut self,
        event: &LiveCompactEvent,
        decoder: &CompactEventDecoder,
        rules: &TradeAggregationRules,
        local_us: u64,
    ) -> Result<(), String> {
        let flags = event.trade_reporting_flags().ok_or("Missing v6 trade reporting classification")?;
        let delayed = flags & DELAYED != 0;
        let price = price_int(event.price_primary_int, event.event_meta & 2 != 0);
        let size = f64::from(event.size_primary);
        let valid_values = price > 0 && size > 0.0 && size.is_finite();
        let extended = !((REGULAR_SESSION_START_SECONDS as u64 * 1_000_000)
            ..(REGULAR_SESSION_END_SECONDS as u64 * 1_000_000)).contains(&local_us);
        let rule = decoder.market_day_trade_rule(event, rules, extended);
        let usable = !delayed && valid_values;
        let price_valid = usable && rule.update_last;
        let extremes_valid = usable && rule.update_high_low;
        let volume_valid = usable && rule.update_volume;
        let execution_valid = volume_valid && self.quote.is_some_and(|quote| {
            event.sip_timestamp_us >= quote.timestamp_us
                && event.sip_timestamp_us - quote.timestamp_us <= MAX_QUOTE_AGE_US
                && (price as f64) + (price.max(quote.bid_int).max(quote.ask_int) as f64) * 1e-9 >= quote.bid_int as f64
                && (price as f64) <= (quote.ask_int as f64) + (price.max(quote.bid_int).max(quote.ask_int) as f64) * 1e-9
        });
        let row = self.pending.as_mut().expect("pending liquidity bucket");
        row.source_trade_count = row.source_trade_count.saturating_add(1);
        row.reporting_delayed_trades += u32::from(delayed);
        row.volume_ineligible_trades += u32::from(!volume_valid);
        row.price_ineligible_trades += u32::from(!price_valid);
        row.execution_ineligible_trades += u32::from(volume_valid && !execution_valid);
        row.pre_0405_trades += u32::from(local_us < PRE_0405_US);
        row.pre_0405_volume_eligible_trades += u32::from(local_us < PRE_0405_US && volume_valid);
        row.invalid_trade_values += u32::from(!valid_values);
        if price_valid {
            if row.price_valid == 0 { row.open_int = price; }
            row.close_int = price;
            row.price_valid = 1;
        }
        if extremes_valid {
            row.high_int = row.high_int.max(price);
            row.low_int = if row.low_int == 0 { price } else { row.low_int.min(price) };
            row.extremes_valid = 1;
        }
        if volume_valid {
            let notional = price as f64 / 10_000.0 * size;
            row.volume += size;
            row.trade_count = row.trade_count.saturating_add(1);
            row.notional += notional;
            row.volume_valid = 1;
            self.cumulative_volume += size;
            self.cumulative_notional += notional;
            if execution_valid {
                row.execution_volume += size;
                row.execution_notional += notional;
                self.cumulative_execution_volume += size;
                self.cumulative_execution_notional += notional;
            }
        }
        self.project_cumulative();
        Ok(())
    }

    fn project_quote(&mut self) {
        let Some(quote) = self.quote else { return; };
        let row = self.pending.as_mut().expect("pending liquidity bucket");
        row.quote_timestamp_us = quote.timestamp_us;
        row.bid_int = quote.bid_int;
        row.ask_int = quote.ask_int;
        row.bid_size = quote.bid_size;
        row.ask_size = quote.ask_size;
        row.spread = (quote.ask_int - quote.bid_int) as f64 / 10_000.0;
        row.quote_valid = 1;
    }

    fn project_cumulative(&mut self) {
        let row = self.pending.as_mut().expect("pending liquidity bucket");
        row.cumulative_volume = self.cumulative_volume;
        row.cumulative_notional = self.cumulative_notional;
        row.cumulative_execution_volume = self.cumulative_execution_volume;
        row.cumulative_execution_notional = self.cumulative_execution_notional;
        row.execution_vwap = if self.cumulative_execution_volume > 0.0 {
            self.cumulative_execution_notional / self.cumulative_execution_volume
        } else { 0.0 };
        if self.quote.is_some() { self.project_quote(); }
    }
}

fn price_int(value: u32, precise: bool) -> u64 {
    u64::from(value) * if precise { 1 } else { 100 }
}

fn local_coordinates(timestamp_us: u64) -> Result<(String, u64), String> {
    let seconds = (timestamp_us / 1_000_000) as i64;
    let nanos = ((timestamp_us % 1_000_000) * 1_000) as u32;
    let local = chrono::DateTime::from_timestamp(seconds, nanos)
        .ok_or("Invalid compact-event SIP timestamp")?.with_timezone(&New_York);
    let date = format!("{:04}-{:02}-{:02}", local.year(), local.month(), local.day());
    let local_us = ((local.hour() * 3_600 + local.minute() * 60 + local.second()) as u64)
        * 1_000_000 + u64::from(local.timestamp_subsec_micros());
    Ok((date, local_us))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bars::TradeUpdateRule;
    use crate::strategy_one_trade_reporting::{EVALUATED, DELAYED};
    use chrono::{TimeZone, Utc};

    fn event(ms: i64, sequence: u64, trade: bool) -> LiveCompactEvent {
        let ts = Utc.with_ymd_and_hms(2026, 8, 18, 8, 1, 0).unwrap()
            + chrono::Duration::milliseconds(ms);
        LiveCompactEvent::from_persisted_fields(
            sequence, 0, 0, 0, 0, 0,
            "2026-08-18".into(), if trade { 1 | EVALUATED } else { 0 },
            ts.timestamp_micros() as u64, 4, 4, ts, 0,
            if trade { 1_000 } else { 1_001 },
            if trade { 0 } else { 999 }, 6,
            ts.timestamp_micros() as u64,
            if trade { 100.0 } else { 50.0 },
            if trade { 0.0 } else { 60.0 },
            sequence, "TEST".into(),
        )
    }

    fn context() -> (CompactEventDecoder, TradeAggregationRules) {
        (CompactEventDecoder::default(),
         TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap())
    }

    #[test]
    fn opening_trade_uses_same_timestamp_prior_quote_and_quote_only_carries_totals() {
        let (decoder, rules) = context();
        let mut reducer = LiquidityReducer::default();
        assert!(reducer.push(&event(100, 1, false), &decoder, &rules).unwrap().is_none());
        assert!(reducer.push(&event(100, 2, true), &decoder, &rules).unwrap().is_none());
        let trade_bucket = reducer.push(&event(200, 3, false), &decoder, &rules)
            .unwrap().unwrap();
        assert_eq!(trade_bucket.pre_0405_trades, 1);
        assert_eq!(trade_bucket.pre_0405_volume_eligible_trades, 1);
        assert_eq!(trade_bucket.volume, 100.0);
        assert_eq!(trade_bucket.execution_volume, 100.0);
        assert_eq!(trade_bucket.open_int, 100_000);
        assert_eq!(trade_bucket.bid_int, 99_900);
        assert_eq!(trade_bucket.ask_int, 100_100);
        assert_eq!(trade_bucket.quote_timestamp_us, event(100, 1, false).sip_timestamp_us);
        assert_eq!(trade_bucket.execution_vwap, 10.0);
        assert_eq!(trade_bucket.source_arrival_sequences, vec![1, 2, 3]);
        assert!(serde_json::to_value(&trade_bucket).unwrap()
            .get("source_arrival_sequences").is_none());
        let quote_only = reducer.push(&event(300, 4, false), &decoder, &rules)
            .unwrap().unwrap();
        assert_eq!(quote_only.volume, 0.0);
        assert_eq!(quote_only.price_valid, 0);
        assert_eq!(quote_only.cumulative_volume, 100.0);
        assert_eq!(quote_only.execution_vwap, 10.0);
        assert_eq!(quote_only.source_arrival_sequences, vec![3, 4]);
    }

    #[test]
    fn trade_only_bucket_carries_the_last_valid_quote_like_arte() {
        let (decoder, rules) = context();
        let mut reducer = LiquidityReducer::default();
        let quote = event(100, 1, false);
        reducer.push(&quote, &decoder, &rules).unwrap();
        let first = reducer.push(&event(200, 2, true), &decoder, &rules)
            .unwrap().unwrap();
        assert_eq!(first.quote_timestamp_us, quote.sip_timestamp_us);
        let trade_only = reducer.push(&event(300, 3, true), &decoder, &rules)
            .unwrap().unwrap();
        assert_eq!(trade_only.quote_event_count, 0);
        assert_eq!(trade_only.quote_valid, 1);
        assert_eq!(trade_only.quote_timestamp_us, quote.sip_timestamp_us);
        assert_eq!(trade_only.bid_int, 99_900);
        assert_eq!(trade_only.ask_int, 100_100);
        assert_eq!(trade_only.bid_size, 60.0);
        assert_eq!(trade_only.ask_size, 50.0);
        assert_eq!(trade_only.execution_volume, 100.0);
    }

    #[test]
    fn replayed_trade_cannot_double_count_volume_or_broker_liquidity() {
        let (decoder, rules) = context();
        let mut reducer = LiquidityReducer::default();
        reducer.push(&event(100, 1, false), &decoder, &rules).unwrap();
        reducer.push(&event(100, 2, true), &decoder, &rules).unwrap();
        let mut repeated = event(100, 2, true);
        repeated.arrival_sequence = 3;
        repeated.issue_flags = 7;
        assert!(reducer.push(&repeated, &decoder, &rules).unwrap().is_none());
        let row = reducer.push(&event(200, 4, false), &decoder, &rules)
            .unwrap().unwrap();
        assert_eq!(row.event_count, 2);
        assert_eq!(row.source_trade_count, 1);
        assert_eq!(row.trade_count, 1);
        assert_eq!(row.volume, 100.0);
        assert_eq!(row.execution_volume, 100.0);
        assert_eq!(row.source_arrival_sequences, vec![1, 2, 4]);
    }

    #[test]
    fn delayed_trade_is_counted_but_never_priced_or_executable() {
        let (decoder, rules) = context();
        let mut reducer = LiquidityReducer::default();
        reducer.push(&event(100, 1, false), &decoder, &rules).unwrap();
        let mut delayed = event(150, 2, true);
        delayed.event_meta |= DELAYED;
        reducer.push(&delayed, &decoder, &rules).unwrap();
        let row = reducer.push(&event(200, 3, false), &decoder, &rules)
            .unwrap().unwrap();
        assert_eq!(row.source_trade_count, 1);
        assert_eq!(row.reporting_delayed_trades, 1);
        assert_eq!(row.volume_ineligible_trades, 1);
        assert_eq!(row.price_ineligible_trades, 1);
        assert_eq!(row.execution_ineligible_trades, 0);
        assert_eq!(row.volume, 0.0);
        assert_eq!(row.execution_volume, 0.0);
    }

    #[test]
    fn stale_or_crossed_quote_cannot_grant_execution_volume() {
        let (decoder, rules) = context();
        let mut reducer = LiquidityReducer::default();
        reducer.push(&event(100, 1, false), &decoder, &rules).unwrap();
        let mut crossed = event(1_100, 2, false);
        crossed.price_secondary_int = 1_002;
        reducer.push(&crossed, &decoder, &rules).unwrap();
        let crossed_only = reducer.push(&event(1_200, 3, true), &decoder, &rules)
            .unwrap().unwrap();
        assert_eq!(crossed_only.quote_event_count, 1);
        assert_eq!(crossed_only.quote_valid, 1);
        assert_eq!(crossed_only.quote_timestamp_us, event(100, 1, false).sip_timestamp_us);
        let row = reducer.push(&event(1_300, 4, false), &decoder, &rules)
            .unwrap().unwrap();
        assert_eq!(row.volume, 100.0);
        assert_eq!(row.execution_volume, 0.0);
        assert_eq!(row.execution_ineligible_trades, 1);
        assert_eq!(row.quote_valid, 1);
        assert_eq!(row.quote_timestamp_us, event(100, 1, false).sip_timestamp_us);
    }

    #[test]
    fn zero_ask_quote_does_not_replace_prior_valid_quote() {
        let (decoder, rules) = context();
        let mut reducer = LiquidityReducer::default();
        let valid = event(100, 1, false);
        reducer.push(&valid, &decoder, &rules).unwrap();
        let mut zero_ask = event(150, 2, false);
        zero_ask.price_primary_int = 0;
        zero_ask.price_secondary_int = 0;
        reducer.push(&zero_ask, &decoder, &rules).unwrap();
        let row = reducer.push(&event(200, 3, true), &decoder, &rules)
            .unwrap().unwrap();
        assert_eq!(row.quote_event_count, 2);
        assert_eq!(row.quote_timestamp_us, valid.sip_timestamp_us);
        assert_eq!(row.ask_int, 100_100);
        assert_eq!(row.bid_int, 99_900);
    }

    #[test]
    fn rejects_legacy_or_reordered_input() {
        let (decoder, rules) = context();
        let mut reducer = LiquidityReducer::default();
        let mut legacy = event(100, 1, true);
        legacy.schema_version = 5;
        assert!(reducer.push(&legacy, &decoder, &rules).is_err());
        reducer.push(&event(200, 2, false), &decoder, &rules).unwrap();
        assert!(reducer.push(&event(100, 1, true), &decoder, &rules).is_err());
    }

    #[test]
    fn ordered_after_hours_event_closes_last_session_bucket_without_joining_it() {
        let (decoder, rules) = context();
        let mut reducer = LiquidityReducer::default();
        reducer.push(&event(100, 1, false), &decoder, &rules).unwrap();
        let after_hours = event(16 * 3_600_000, 2, false);
        let row = reducer.push(&after_hours, &decoder, &rules).unwrap().unwrap();
        assert_eq!(row.event_count, 1);
        assert_eq!(row.quote_event_count, 1);
        assert_eq!(row.last_event_us, event(100, 1, false).sip_timestamp_us);
        assert_eq!(row.source_arrival_sequences, vec![1, 2]);
        assert!(reducer.take_completed_through(after_hours.sip_timestamp_us).is_none());
    }
}
