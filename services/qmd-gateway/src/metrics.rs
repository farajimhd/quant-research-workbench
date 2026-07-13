use chrono::{DateTime, Utc};
use serde::Serialize;
use std::collections::{BTreeMap, VecDeque};
use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};
use std::sync::{Arc, RwLock};
use std::time::Instant;

#[derive(Clone)]
pub struct SharedMetrics {
    inner: Arc<MetricsInner>,
}

struct MetricsInner {
    bar_rows_emitted: AtomicU64,
    bar_events_dropped: AtomicU64,
    bar_rows_scanner_dropped: AtomicU64,
    bar_rows_indicator_dropped: AtomicU64,
    clickhouse_events_dropped: AtomicU64,
    compact_event_broadcast_dropped: AtomicU64,
    compact_event_queue_dropped: AtomicU64,
    compact_event_rejected: AtomicU64,
    compact_event_rejected_empty_ticker: AtomicU64,
    compact_event_rejected_zero_sequence: AtomicU64,
    compact_event_rejected_zero_timestamp: AtomicU64,
    compact_event_reorder_forced_flushes: AtomicU64,
    compact_event_reorder_late_arrivals: AtomicU64,
    compact_events_emitted: AtomicU64,
    compact_events_persisted: AtomicU64,
    compact_events_reorder_buffered: AtomicU64,
    compact_events_reorder_flushed: AtomicU64,
    compact_events_reorder_pending: AtomicU64,
    events_broadcast_dropped: AtomicU64,
    gap_fill_failures: AtomicU64,
    gap_fill_last_duration_ms: AtomicU64,
    gap_fill_rows_written: AtomicU64,
    gap_fill_runs: AtomicU64,
    gap_fill_total_duration_ms: AtomicU64,
    indicator_events_dropped: AtomicU64,
    intraday_bar_events_dropped: AtomicU64,
    intraday_bar_rows_emitted: AtomicU64,
    intraday_bar_rows_persisted: AtomicU64,
    intraday_bar_repairs_completed: AtomicU64,
    intraday_bar_repairs_requested: AtomicU64,
    ingest_events: AtomicU64,
    ingest_quotes: AtomicU64,
    ingest_trades: AtomicU64,
    last_event_unix_ms: AtomicI64,
    live_market_state_broadcast_dropped: AtomicU64,
    live_market_state_events_emitted: AtomicU64,
    live_market_state_events_persisted: AtomicU64,
    live_market_state_persist_failures: AtomicU64,
    massive_connect_failures: AtomicU64,
    massive_disconnects: AtomicU64,
    parse_failures: AtomicU64,
    scanner_candidates_emitted: AtomicU64,
    service_start_unix_ms: AtomicI64,
    operational: RwLock<OperationalState>,
}

#[derive(Clone, Debug, Serialize)]
pub struct OperationalLaneSnapshot {
    pub key: String,
    pub label: String,
    pub kind: String,
    pub enabled: bool,
    pub required: bool,
    pub state: String,
    pub detail: String,
    pub pending_rows: u64,
    pub max_pending_rows: u64,
    pub successful_rows: u64,
    pub failures: u64,
    pub consecutive_failures: u64,
    pub last_success_utc: Option<DateTime<Utc>>,
    pub last_failure_utc: Option<DateTime<Utc>>,
    pub last_transition_utc: DateTime<Utc>,
}

#[derive(Clone, Debug, Serialize)]
pub struct OperationalRecoverySnapshot {
    pub area: String,
    pub message: String,
    pub recovered_at_utc: DateTime<Utc>,
}

#[derive(Clone, Debug, Serialize)]
pub struct OperationalSnapshot {
    pub lanes: Vec<OperationalLaneSnapshot>,
    pub recent_recoveries: Vec<OperationalRecoverySnapshot>,
}

#[derive(Default)]
struct OperationalState {
    lanes: BTreeMap<String, OperationalLaneSnapshot>,
    recent_recoveries: VecDeque<OperationalRecoverySnapshot>,
}

#[derive(Clone, Debug, Serialize)]
pub struct MetricsSnapshot {
    pub bar_rows_emitted: u64,
    pub bar_events_dropped: u64,
    pub bar_rows_indicator_dropped: u64,
    pub bar_rows_scanner_dropped: u64,
    pub clickhouse_events_dropped: u64,
    pub compact_event_broadcast_dropped: u64,
    pub compact_event_queue_dropped: u64,
    pub compact_event_rejected: u64,
    pub compact_event_rejected_empty_ticker: u64,
    pub compact_event_rejected_zero_sequence: u64,
    pub compact_event_rejected_zero_timestamp: u64,
    pub compact_event_reorder_forced_flushes: u64,
    pub compact_event_reorder_late_arrivals: u64,
    pub compact_events_emitted: u64,
    pub compact_events_persisted: u64,
    pub compact_events_reorder_buffered: u64,
    pub compact_events_reorder_flushed: u64,
    pub compact_events_reorder_pending: u64,
    pub events_broadcast_dropped: u64,
    pub gap_fill_failures: u64,
    pub gap_fill_last_duration_ms: u64,
    pub gap_fill_rows_written: u64,
    pub gap_fill_runs: u64,
    pub gap_fill_total_duration_ms: u64,
    pub indicator_events_dropped: u64,
    pub intraday_bar_events_dropped: u64,
    pub intraday_bar_rows_emitted: u64,
    pub intraday_bar_rows_persisted: u64,
    pub intraday_bar_repairs_completed: u64,
    pub intraday_bar_repairs_requested: u64,
    pub ingest_events: u64,
    pub ingest_quotes: u64,
    pub ingest_trades: u64,
    pub last_event_lag_ms: Option<i64>,
    pub last_event_ts: Option<DateTime<Utc>>,
    pub live_market_state_broadcast_dropped: u64,
    pub live_market_state_events_emitted: u64,
    pub live_market_state_events_persisted: u64,
    pub live_market_state_persist_failures: u64,
    pub massive_connect_failures: u64,
    pub massive_disconnects: u64,
    pub parse_failures: u64,
    pub process_uptime_ms: i64,
    pub scanner_candidates_emitted: u64,
}

#[derive(Clone)]
pub struct TimingGuard {
    metrics: SharedMetrics,
    started_at: Instant,
    target: TimingTarget,
}

#[derive(Clone, Copy)]
pub enum TimingTarget {
    GapFillRun,
}

impl SharedMetrics {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(MetricsInner {
                bar_rows_emitted: AtomicU64::new(0),
                bar_events_dropped: AtomicU64::new(0),
                bar_rows_scanner_dropped: AtomicU64::new(0),
                bar_rows_indicator_dropped: AtomicU64::new(0),
                clickhouse_events_dropped: AtomicU64::new(0),
                compact_event_broadcast_dropped: AtomicU64::new(0),
                compact_event_queue_dropped: AtomicU64::new(0),
                compact_event_rejected: AtomicU64::new(0),
                compact_event_rejected_empty_ticker: AtomicU64::new(0),
                compact_event_rejected_zero_sequence: AtomicU64::new(0),
                compact_event_rejected_zero_timestamp: AtomicU64::new(0),
                compact_event_reorder_forced_flushes: AtomicU64::new(0),
                compact_event_reorder_late_arrivals: AtomicU64::new(0),
                compact_events_emitted: AtomicU64::new(0),
                compact_events_persisted: AtomicU64::new(0),
                compact_events_reorder_buffered: AtomicU64::new(0),
                compact_events_reorder_flushed: AtomicU64::new(0),
                compact_events_reorder_pending: AtomicU64::new(0),
                events_broadcast_dropped: AtomicU64::new(0),
                gap_fill_failures: AtomicU64::new(0),
                gap_fill_last_duration_ms: AtomicU64::new(0),
                gap_fill_rows_written: AtomicU64::new(0),
                gap_fill_runs: AtomicU64::new(0),
                gap_fill_total_duration_ms: AtomicU64::new(0),
                indicator_events_dropped: AtomicU64::new(0),
                intraday_bar_events_dropped: AtomicU64::new(0),
                intraday_bar_rows_emitted: AtomicU64::new(0),
                intraday_bar_rows_persisted: AtomicU64::new(0),
                intraday_bar_repairs_completed: AtomicU64::new(0),
                intraday_bar_repairs_requested: AtomicU64::new(0),
                ingest_events: AtomicU64::new(0),
                ingest_quotes: AtomicU64::new(0),
                ingest_trades: AtomicU64::new(0),
                last_event_unix_ms: AtomicI64::new(0),
                live_market_state_broadcast_dropped: AtomicU64::new(0),
                live_market_state_events_emitted: AtomicU64::new(0),
                live_market_state_events_persisted: AtomicU64::new(0),
                live_market_state_persist_failures: AtomicU64::new(0),
                massive_connect_failures: AtomicU64::new(0),
                massive_disconnects: AtomicU64::new(0),
                parse_failures: AtomicU64::new(0),
                scanner_candidates_emitted: AtomicU64::new(0),
                service_start_unix_ms: AtomicI64::new(Utc::now().timestamp_millis()),
                operational: RwLock::new(OperationalState::default()),
            }),
        }
    }

    pub fn register_lane(&self, key: &str, label: &str, kind: &str, enabled: bool, required: bool) {
        let now = Utc::now();
        let mut state = self
            .inner
            .operational
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        state.lanes.insert(
            key.to_string(),
            OperationalLaneSnapshot {
                key: key.to_string(),
                label: label.to_string(),
                kind: kind.to_string(),
                enabled,
                required,
                state: if enabled { "starting" } else { "disabled" }.to_string(),
                detail: if enabled {
                    "Awaiting first successful operation."
                } else {
                    "Disabled by configuration."
                }
                .to_string(),
                pending_rows: 0,
                max_pending_rows: 0,
                successful_rows: 0,
                failures: 0,
                consecutive_failures: 0,
                last_success_utc: None,
                last_failure_utc: None,
                last_transition_utc: now,
            },
        );
    }

    pub fn set_lane_state(&self, key: &str, lane_state: &str, detail: &str) {
        let now = Utc::now();
        let mut state = self
            .inner
            .operational
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let Some(lane) = state.lanes.get_mut(key) else {
            return;
        };
        if lane.state != lane_state || lane.detail != detail {
            lane.last_transition_utc = now;
        }
        lane.state = lane_state.to_string();
        lane.detail = detail.to_string();
    }

    pub fn set_lane_pending(&self, key: &str, pending_rows: u64) {
        let mut state = self
            .inner
            .operational
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Some(lane) = state.lanes.get_mut(key) {
            lane.pending_rows = pending_rows;
            lane.max_pending_rows = lane.max_pending_rows.max(pending_rows);
        }
    }

    pub fn record_lane_success(&self, key: &str, rows: u64, detail: &str) {
        let now = Utc::now();
        let mut state = self
            .inner
            .operational
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let Some(lane) = state.lanes.get_mut(key) else {
            return;
        };
        let recovered = lane.consecutive_failures > 0;
        let label = lane.label.clone();
        lane.state = "healthy".to_string();
        lane.detail = detail.to_string();
        lane.successful_rows = lane.successful_rows.saturating_add(rows);
        lane.consecutive_failures = 0;
        lane.last_success_utc = Some(now);
        lane.last_transition_utc = now;
        if recovered {
            state
                .recent_recoveries
                .push_back(OperationalRecoverySnapshot {
                    area: label,
                    message: detail.to_string(),
                    recovered_at_utc: now,
                });
            while state.recent_recoveries.len() > 12 {
                state.recent_recoveries.pop_front();
            }
        }
    }

    pub fn record_lane_failure(&self, key: &str, error: &str) {
        let now = Utc::now();
        let mut state = self
            .inner
            .operational
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let Some(lane) = state.lanes.get_mut(key) else {
            return;
        };
        lane.state = "failed".to_string();
        lane.detail = truncate_error(error);
        lane.failures = lane.failures.saturating_add(1);
        lane.consecutive_failures = lane.consecutive_failures.saturating_add(1);
        lane.last_failure_utc = Some(now);
        lane.last_transition_utc = now;
    }

    pub fn operational_snapshot(&self) -> OperationalSnapshot {
        let state = self
            .inner
            .operational
            .read()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        OperationalSnapshot {
            lanes: state.lanes.values().cloned().collect(),
            recent_recoveries: state.recent_recoveries.iter().cloned().collect(),
        }
    }

    pub fn snapshot(&self) -> MetricsSnapshot {
        let now_ms = Utc::now().timestamp_millis();
        let last_event_ms = self.inner.last_event_unix_ms.load(Ordering::Relaxed);
        let start_ms = self.inner.service_start_unix_ms.load(Ordering::Relaxed);
        MetricsSnapshot {
            bar_rows_emitted: self.get(&self.inner.bar_rows_emitted),
            bar_events_dropped: self.get(&self.inner.bar_events_dropped),
            bar_rows_indicator_dropped: self.get(&self.inner.bar_rows_indicator_dropped),
            bar_rows_scanner_dropped: self.get(&self.inner.bar_rows_scanner_dropped),
            clickhouse_events_dropped: self.get(&self.inner.clickhouse_events_dropped),
            compact_event_broadcast_dropped: self.get(&self.inner.compact_event_broadcast_dropped),
            compact_event_queue_dropped: self.get(&self.inner.compact_event_queue_dropped),
            compact_event_rejected: self.get(&self.inner.compact_event_rejected),
            compact_event_rejected_empty_ticker: self
                .get(&self.inner.compact_event_rejected_empty_ticker),
            compact_event_rejected_zero_sequence: self
                .get(&self.inner.compact_event_rejected_zero_sequence),
            compact_event_rejected_zero_timestamp: self
                .get(&self.inner.compact_event_rejected_zero_timestamp),
            compact_event_reorder_forced_flushes: self
                .get(&self.inner.compact_event_reorder_forced_flushes),
            compact_event_reorder_late_arrivals: self
                .get(&self.inner.compact_event_reorder_late_arrivals),
            compact_events_emitted: self.get(&self.inner.compact_events_emitted),
            compact_events_persisted: self.get(&self.inner.compact_events_persisted),
            compact_events_reorder_buffered: self.get(&self.inner.compact_events_reorder_buffered),
            compact_events_reorder_flushed: self.get(&self.inner.compact_events_reorder_flushed),
            compact_events_reorder_pending: self.get(&self.inner.compact_events_reorder_pending),
            events_broadcast_dropped: self.get(&self.inner.events_broadcast_dropped),
            gap_fill_failures: self.get(&self.inner.gap_fill_failures),
            gap_fill_last_duration_ms: self.get(&self.inner.gap_fill_last_duration_ms),
            gap_fill_rows_written: self.get(&self.inner.gap_fill_rows_written),
            gap_fill_runs: self.get(&self.inner.gap_fill_runs),
            gap_fill_total_duration_ms: self.get(&self.inner.gap_fill_total_duration_ms),
            indicator_events_dropped: self.get(&self.inner.indicator_events_dropped),
            intraday_bar_events_dropped: self.get(&self.inner.intraday_bar_events_dropped),
            intraday_bar_rows_emitted: self.get(&self.inner.intraday_bar_rows_emitted),
            intraday_bar_rows_persisted: self.get(&self.inner.intraday_bar_rows_persisted),
            intraday_bar_repairs_completed: self.get(&self.inner.intraday_bar_repairs_completed),
            intraday_bar_repairs_requested: self.get(&self.inner.intraday_bar_repairs_requested),
            ingest_events: self.get(&self.inner.ingest_events),
            ingest_quotes: self.get(&self.inner.ingest_quotes),
            ingest_trades: self.get(&self.inner.ingest_trades),
            last_event_lag_ms: if last_event_ms > 0 {
                Some(now_ms - last_event_ms)
            } else {
                None
            },
            last_event_ts: if last_event_ms > 0 {
                DateTime::<Utc>::from_timestamp_millis(last_event_ms)
            } else {
                None
            },
            live_market_state_broadcast_dropped: self
                .get(&self.inner.live_market_state_broadcast_dropped),
            live_market_state_events_emitted: self
                .get(&self.inner.live_market_state_events_emitted),
            live_market_state_events_persisted: self
                .get(&self.inner.live_market_state_events_persisted),
            live_market_state_persist_failures: self
                .get(&self.inner.live_market_state_persist_failures),
            massive_connect_failures: self.get(&self.inner.massive_connect_failures),
            massive_disconnects: self.get(&self.inner.massive_disconnects),
            parse_failures: self.get(&self.inner.parse_failures),
            process_uptime_ms: now_ms - start_ms,
            scanner_candidates_emitted: self.get(&self.inner.scanner_candidates_emitted),
        }
    }

    pub fn observe_event(&self, kind: &str, ts: DateTime<Utc>) {
        self.inc(&self.inner.ingest_events, 1);
        self.inner
            .last_event_unix_ms
            .store(ts.timestamp_millis(), Ordering::Relaxed);
        match kind {
            "trade" => self.inc(&self.inner.ingest_trades, 1),
            "quote" => self.inc(&self.inner.ingest_quotes, 1),
            _ => {}
        }
    }

    pub fn inc_bar_emitted(&self, count: u64) {
        self.inc(&self.inner.bar_rows_emitted, count);
    }

    pub fn inc_bar_event_dropped(&self) {
        self.inc(&self.inner.bar_events_dropped, 1);
    }

    pub fn inc_bar_indicator_dropped(&self) {
        self.inc(&self.inner.bar_rows_indicator_dropped, 1);
    }

    pub fn inc_bar_scanner_dropped(&self) {
        self.inc(&self.inner.bar_rows_scanner_dropped, 1);
    }

    pub fn inc_clickhouse_event_dropped(&self) {
        self.inc(&self.inner.clickhouse_events_dropped, 1);
    }

    pub fn inc_compact_event_broadcast_dropped(&self) {
        self.inc(&self.inner.compact_event_broadcast_dropped, 1);
    }

    pub fn inc_compact_event_queue_dropped(&self) {
        self.inc(&self.inner.compact_event_queue_dropped, 1);
    }

    pub fn inc_compact_event_rejected(&self) {
        self.inc(&self.inner.compact_event_rejected, 1);
    }

    pub fn inc_compact_event_rejected_empty_ticker(&self) {
        self.inc(&self.inner.compact_event_rejected_empty_ticker, 1);
        self.inc_compact_event_rejected();
    }

    pub fn inc_compact_event_rejected_zero_sequence(&self) {
        self.inc(&self.inner.compact_event_rejected_zero_sequence, 1);
        self.inc_compact_event_rejected();
    }

    pub fn inc_compact_event_rejected_zero_timestamp(&self) {
        self.inc(&self.inner.compact_event_rejected_zero_timestamp, 1);
        self.inc_compact_event_rejected();
    }

    pub fn inc_compact_event_reorder_forced_flush(&self) {
        self.inc(&self.inner.compact_event_reorder_forced_flushes, 1);
    }

    pub fn inc_compact_event_reorder_late_arrival(&self) {
        self.inc(&self.inner.compact_event_reorder_late_arrivals, 1);
    }

    pub fn inc_compact_events_emitted(&self, count: u64) {
        self.inc(&self.inner.compact_events_emitted, count);
    }

    pub fn inc_compact_events_persisted(&self, count: u64) {
        self.inc(&self.inner.compact_events_persisted, count);
    }

    pub fn inc_compact_events_reorder_buffered(&self, count: u64) {
        self.inc(&self.inner.compact_events_reorder_buffered, count);
    }

    pub fn inc_compact_events_reorder_flushed(&self, count: u64) {
        self.inc(&self.inner.compact_events_reorder_flushed, count);
    }

    pub fn set_compact_events_reorder_pending(&self, count: u64) {
        self.set(&self.inner.compact_events_reorder_pending, count);
    }

    pub fn inc_event_broadcast_dropped(&self) {
        self.inc(&self.inner.events_broadcast_dropped, 1);
    }

    pub fn inc_gap_fill_failure(&self) {
        self.inc(&self.inner.gap_fill_failures, 1);
    }

    pub fn inc_gap_fill_rows(&self, rows: u64) {
        self.inc(&self.inner.gap_fill_rows_written, rows);
    }

    pub fn inc_gap_fill_run(&self) {
        self.inc(&self.inner.gap_fill_runs, 1);
    }

    pub fn inc_indicator_event_dropped(&self) {
        self.inc(&self.inner.indicator_events_dropped, 1);
    }

    pub fn inc_intraday_bar_event_dropped(&self) {
        self.inc(&self.inner.intraday_bar_events_dropped, 1);
    }

    pub fn inc_intraday_bar_emitted(&self, count: u64) {
        self.inc(&self.inner.intraday_bar_rows_emitted, count);
    }

    pub fn inc_intraday_bar_persisted(&self, count: u64) {
        self.inc(&self.inner.intraday_bar_rows_persisted, count);
    }

    pub fn inc_intraday_bar_repair_completed(&self) {
        self.inc(&self.inner.intraday_bar_repairs_completed, 1);
    }

    pub fn inc_intraday_bar_repair_requested(&self) {
        self.inc(&self.inner.intraday_bar_repairs_requested, 1);
    }

    pub fn inc_live_market_state_broadcast_dropped(&self) {
        self.inc(&self.inner.live_market_state_broadcast_dropped, 1);
    }

    pub fn inc_live_market_state_emitted(&self, count: u64) {
        self.inc(&self.inner.live_market_state_events_emitted, count);
    }

    pub fn inc_live_market_state_persisted(&self, count: u64) {
        self.inc(&self.inner.live_market_state_events_persisted, count);
    }

    pub fn inc_live_market_state_persist_failed(&self) {
        self.inc(&self.inner.live_market_state_persist_failures, 1);
    }

    pub fn inc_massive_connect_failure(&self) {
        self.inc(&self.inner.massive_connect_failures, 1);
    }

    pub fn inc_massive_disconnect(&self) {
        self.inc(&self.inner.massive_disconnects, 1);
    }

    pub fn inc_parse_failure(&self) {
        self.inc(&self.inner.parse_failures, 1);
    }

    pub fn inc_scanner_candidates(&self, count: u64) {
        self.inc(&self.inner.scanner_candidates_emitted, count);
    }

    pub fn timing(&self, target: TimingTarget) -> TimingGuard {
        TimingGuard {
            metrics: self.clone(),
            started_at: Instant::now(),
            target,
        }
    }

    fn get(&self, value: &AtomicU64) -> u64 {
        value.load(Ordering::Relaxed)
    }

    fn inc(&self, value: &AtomicU64, count: u64) {
        value.fetch_add(count, Ordering::Relaxed);
    }

    fn set(&self, value: &AtomicU64, count: u64) {
        value.store(count, Ordering::Relaxed);
    }
}

fn truncate_error(value: &str) -> String {
    const LIMIT: usize = 400;
    let value = value.trim();
    if value.chars().count() <= LIMIT {
        return value.to_string();
    }
    value.chars().take(LIMIT - 3).collect::<String>() + "..."
}

#[cfg(test)]
mod operational_tests {
    use super::SharedMetrics;

    #[test]
    fn operational_lane_records_failure_and_recovery() {
        let metrics = SharedMetrics::new();
        metrics.register_lane("compact", "Compact persistence", "writer", true, true);
        metrics.set_lane_pending("compact", 25);
        metrics.record_lane_failure("compact", "ClickHouse unavailable");
        let failed = metrics.operational_snapshot();
        assert_eq!(failed.lanes[0].state, "failed");
        assert_eq!(failed.lanes[0].pending_rows, 25);
        assert_eq!(failed.lanes[0].failures, 1);

        metrics.record_lane_success("compact", 25, "Committed compact events.");
        metrics.set_lane_pending("compact", 0);
        let recovered = metrics.operational_snapshot();
        assert_eq!(recovered.lanes[0].state, "healthy");
        assert_eq!(recovered.lanes[0].successful_rows, 25);
        assert_eq!(recovered.recent_recoveries.len(), 1);
    }
}

impl Drop for TimingGuard {
    fn drop(&mut self) {
        let elapsed_ms = self
            .started_at
            .elapsed()
            .as_millis()
            .min(u128::from(u64::MAX)) as u64;
        match self.target {
            TimingTarget::GapFillRun => {
                self.metrics
                    .set(&self.metrics.inner.gap_fill_last_duration_ms, elapsed_ms);
                self.metrics
                    .inc(&self.metrics.inner.gap_fill_total_duration_ms, elapsed_ms);
            }
        }
    }
}
