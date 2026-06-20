use chrono::{DateTime, Utc};
use serde::Serialize;
use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Instant;

#[derive(Clone)]
pub struct SharedMetrics {
    inner: Arc<MetricsInner>,
}

struct MetricsInner {
    bar_rows_emitted: AtomicU64,
    bar_events_dropped: AtomicU64,
    bar_rows_persist_queued: AtomicU64,
    bar_rows_scanner_dropped: AtomicU64,
    bar_rows_indicator_dropped: AtomicU64,
    bar_rows_writer_dropped: AtomicU64,
    clickhouse_events_dropped: AtomicU64,
    compact_event_broadcast_dropped: AtomicU64,
    compact_event_queue_dropped: AtomicU64,
    compact_event_rejected: AtomicU64,
    compact_events_emitted: AtomicU64,
    compact_events_persisted: AtomicU64,
    events_broadcast_dropped: AtomicU64,
    gap_fill_failures: AtomicU64,
    gap_fill_last_duration_ms: AtomicU64,
    gap_fill_rows_written: AtomicU64,
    gap_fill_runs: AtomicU64,
    gap_fill_total_duration_ms: AtomicU64,
    indicator_events_dropped: AtomicU64,
    ingest_events: AtomicU64,
    ingest_quotes: AtomicU64,
    ingest_trades: AtomicU64,
    last_event_unix_ms: AtomicI64,
    massive_connect_failures: AtomicU64,
    massive_disconnects: AtomicU64,
    parse_failures: AtomicU64,
    scanner_candidates_emitted: AtomicU64,
    service_start_unix_ms: AtomicI64,
}

#[derive(Clone, Debug, Serialize)]
pub struct MetricsSnapshot {
    pub bar_rows_emitted: u64,
    pub bar_events_dropped: u64,
    pub bar_rows_indicator_dropped: u64,
    pub bar_rows_persist_queued: u64,
    pub bar_rows_scanner_dropped: u64,
    pub bar_rows_writer_dropped: u64,
    pub clickhouse_events_dropped: u64,
    pub compact_event_broadcast_dropped: u64,
    pub compact_event_queue_dropped: u64,
    pub compact_event_rejected: u64,
    pub compact_events_emitted: u64,
    pub compact_events_persisted: u64,
    pub events_broadcast_dropped: u64,
    pub gap_fill_failures: u64,
    pub gap_fill_last_duration_ms: u64,
    pub gap_fill_rows_written: u64,
    pub gap_fill_runs: u64,
    pub gap_fill_total_duration_ms: u64,
    pub indicator_events_dropped: u64,
    pub ingest_events: u64,
    pub ingest_quotes: u64,
    pub ingest_trades: u64,
    pub last_event_lag_ms: Option<i64>,
    pub last_event_ts: Option<DateTime<Utc>>,
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
                bar_rows_persist_queued: AtomicU64::new(0),
                bar_rows_scanner_dropped: AtomicU64::new(0),
                bar_rows_indicator_dropped: AtomicU64::new(0),
                bar_rows_writer_dropped: AtomicU64::new(0),
                clickhouse_events_dropped: AtomicU64::new(0),
                compact_event_broadcast_dropped: AtomicU64::new(0),
                compact_event_queue_dropped: AtomicU64::new(0),
                compact_event_rejected: AtomicU64::new(0),
                compact_events_emitted: AtomicU64::new(0),
                compact_events_persisted: AtomicU64::new(0),
                events_broadcast_dropped: AtomicU64::new(0),
                gap_fill_failures: AtomicU64::new(0),
                gap_fill_last_duration_ms: AtomicU64::new(0),
                gap_fill_rows_written: AtomicU64::new(0),
                gap_fill_runs: AtomicU64::new(0),
                gap_fill_total_duration_ms: AtomicU64::new(0),
                indicator_events_dropped: AtomicU64::new(0),
                ingest_events: AtomicU64::new(0),
                ingest_quotes: AtomicU64::new(0),
                ingest_trades: AtomicU64::new(0),
                last_event_unix_ms: AtomicI64::new(0),
                massive_connect_failures: AtomicU64::new(0),
                massive_disconnects: AtomicU64::new(0),
                parse_failures: AtomicU64::new(0),
                scanner_candidates_emitted: AtomicU64::new(0),
                service_start_unix_ms: AtomicI64::new(Utc::now().timestamp_millis()),
            }),
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
            bar_rows_persist_queued: self.get(&self.inner.bar_rows_persist_queued),
            bar_rows_scanner_dropped: self.get(&self.inner.bar_rows_scanner_dropped),
            bar_rows_writer_dropped: self.get(&self.inner.bar_rows_writer_dropped),
            clickhouse_events_dropped: self.get(&self.inner.clickhouse_events_dropped),
            compact_event_broadcast_dropped: self.get(&self.inner.compact_event_broadcast_dropped),
            compact_event_queue_dropped: self.get(&self.inner.compact_event_queue_dropped),
            compact_event_rejected: self.get(&self.inner.compact_event_rejected),
            compact_events_emitted: self.get(&self.inner.compact_events_emitted),
            compact_events_persisted: self.get(&self.inner.compact_events_persisted),
            events_broadcast_dropped: self.get(&self.inner.events_broadcast_dropped),
            gap_fill_failures: self.get(&self.inner.gap_fill_failures),
            gap_fill_last_duration_ms: self.get(&self.inner.gap_fill_last_duration_ms),
            gap_fill_rows_written: self.get(&self.inner.gap_fill_rows_written),
            gap_fill_runs: self.get(&self.inner.gap_fill_runs),
            gap_fill_total_duration_ms: self.get(&self.inner.gap_fill_total_duration_ms),
            indicator_events_dropped: self.get(&self.inner.indicator_events_dropped),
            ingest_events: self.get(&self.inner.ingest_events),
            ingest_quotes: self.get(&self.inner.ingest_quotes),
            ingest_trades: self.get(&self.inner.ingest_trades),
            last_event_lag_ms: if last_event_ms > 0 { Some(now_ms - last_event_ms) } else { None },
            last_event_ts: if last_event_ms > 0 {
                DateTime::<Utc>::from_timestamp_millis(last_event_ms)
            } else {
                None
            },
            massive_connect_failures: self.get(&self.inner.massive_connect_failures),
            massive_disconnects: self.get(&self.inner.massive_disconnects),
            parse_failures: self.get(&self.inner.parse_failures),
            process_uptime_ms: now_ms - start_ms,
            scanner_candidates_emitted: self.get(&self.inner.scanner_candidates_emitted),
        }
    }

    pub fn observe_event(&self, kind: &str, ts: DateTime<Utc>) {
        self.inc(&self.inner.ingest_events, 1);
        self.inner.last_event_unix_ms.store(ts.timestamp_millis(), Ordering::Relaxed);
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

    pub fn inc_bar_persist_queued(&self) {
        self.inc(&self.inner.bar_rows_persist_queued, 1);
    }

    pub fn inc_bar_indicator_dropped(&self) {
        self.inc(&self.inner.bar_rows_indicator_dropped, 1);
    }

    pub fn inc_bar_scanner_dropped(&self) {
        self.inc(&self.inner.bar_rows_scanner_dropped, 1);
    }

    pub fn inc_bar_writer_dropped(&self) {
        self.inc(&self.inner.bar_rows_writer_dropped, 1);
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

    pub fn inc_compact_events_emitted(&self, count: u64) {
        self.inc(&self.inner.compact_events_emitted, count);
    }

    pub fn inc_compact_events_persisted(&self, count: u64) {
        self.inc(&self.inner.compact_events_persisted, count);
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

impl Drop for TimingGuard {
    fn drop(&mut self) {
        let elapsed_ms = self.started_at.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
        match self.target {
            TimingTarget::GapFillRun => {
                self.metrics.set(&self.metrics.inner.gap_fill_last_duration_ms, elapsed_ms);
                self.metrics.inc(&self.metrics.inner.gap_fill_total_duration_ms, elapsed_ms);
            }
        }
    }
}
