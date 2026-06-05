use chrono::{DateTime, Utc};
use serde::Serialize;
use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};
use std::sync::Arc;

#[derive(Clone)]
pub struct SharedMetrics {
    inner: Arc<MetricsInner>,
}

struct MetricsInner {
    articles_broadcast_dropped: AtomicU64,
    articles_persisted_queued: AtomicU64,
    benzinga_poll_failures: AtomicU64,
    duplicate_updates_seen: AtomicU64,
    extraction_failures: AtomicU64,
    massive_news_poll_failures: AtomicU64,
    last_article_unix_ns: AtomicI64,
    malformed_rows: AtomicU64,
    poll_runs: AtomicU64,
    provider_rows_seen: AtomicU64,
    service_start_unix_ms: AtomicI64,
    valid_articles_seen: AtomicU64,
    writer_dropped: AtomicU64,
}

#[derive(Clone, Debug, Serialize)]
pub struct MetricsSnapshot {
    pub articles_broadcast_dropped: u64,
    pub articles_persisted_queued: u64,
    pub benzinga_poll_failures: u64,
    pub duplicate_updates_seen: u64,
    pub extraction_failures: u64,
    pub massive_news_poll_failures: u64,
    pub last_article_lag_ms: Option<i64>,
    pub last_article_ts: Option<DateTime<Utc>>,
    pub malformed_rows: u64,
    pub poll_runs: u64,
    pub process_uptime_ms: i64,
    pub provider_rows_seen: u64,
    pub valid_articles_seen: u64,
    pub writer_dropped: u64,
}

impl SharedMetrics {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(MetricsInner {
                articles_broadcast_dropped: AtomicU64::new(0),
                articles_persisted_queued: AtomicU64::new(0),
                benzinga_poll_failures: AtomicU64::new(0),
                duplicate_updates_seen: AtomicU64::new(0),
                extraction_failures: AtomicU64::new(0),
                massive_news_poll_failures: AtomicU64::new(0),
                last_article_unix_ns: AtomicI64::new(0),
                malformed_rows: AtomicU64::new(0),
                poll_runs: AtomicU64::new(0),
                provider_rows_seen: AtomicU64::new(0),
                service_start_unix_ms: AtomicI64::new(Utc::now().timestamp_millis()),
                valid_articles_seen: AtomicU64::new(0),
                writer_dropped: AtomicU64::new(0),
            }),
        }
    }

    pub fn snapshot(&self) -> MetricsSnapshot {
        let now_ms = Utc::now().timestamp_millis();
        let last_ns = self.inner.last_article_unix_ns.load(Ordering::Relaxed);
        let last_ms = last_ns / 1_000_000;
        MetricsSnapshot {
            articles_broadcast_dropped: self.get(&self.inner.articles_broadcast_dropped),
            articles_persisted_queued: self.get(&self.inner.articles_persisted_queued),
            benzinga_poll_failures: self.get(&self.inner.benzinga_poll_failures),
            duplicate_updates_seen: self.get(&self.inner.duplicate_updates_seen),
            extraction_failures: self.get(&self.inner.extraction_failures),
            massive_news_poll_failures: self.get(&self.inner.massive_news_poll_failures),
            last_article_lag_ms: if last_ms > 0 { Some(now_ms - last_ms) } else { None },
            last_article_ts: if last_ms > 0 {
                DateTime::<Utc>::from_timestamp_millis(last_ms)
            } else {
                None
            },
            malformed_rows: self.get(&self.inner.malformed_rows),
            poll_runs: self.get(&self.inner.poll_runs),
            process_uptime_ms: now_ms - self.inner.service_start_unix_ms.load(Ordering::Relaxed),
            provider_rows_seen: self.get(&self.inner.provider_rows_seen),
            valid_articles_seen: self.get(&self.inner.valid_articles_seen),
            writer_dropped: self.get(&self.inner.writer_dropped),
        }
    }

    pub fn observe_article(&self, published_at: DateTime<Utc>) {
        self.inner
            .last_article_unix_ns
            .store(to_unix_ns(published_at), Ordering::Relaxed);
        self.inc(&self.inner.valid_articles_seen, 1);
    }

    pub fn inc_broadcast_drop(&self) {
        self.inc(&self.inner.articles_broadcast_dropped, 1);
    }

    pub fn inc_duplicate(&self) {
        self.inc(&self.inner.duplicate_updates_seen, 1);
    }

    pub fn inc_extraction_failure(&self) {
        self.inc(&self.inner.extraction_failures, 1);
    }

    pub fn inc_malformed(&self) {
        self.inc(&self.inner.malformed_rows, 1);
    }

    pub fn inc_persist_queued(&self) {
        self.inc(&self.inner.articles_persisted_queued, 1);
    }

    pub fn inc_poll_failure(&self, source: &str) {
        if source == "massive_benzinga" {
            self.inc(&self.inner.benzinga_poll_failures, 1);
        } else {
            self.inc(&self.inner.massive_news_poll_failures, 1);
        }
    }

    pub fn inc_poll_run(&self) {
        self.inc(&self.inner.poll_runs, 1);
    }

    pub fn inc_provider_rows(&self, count: u64) {
        self.inc(&self.inner.provider_rows_seen, count);
    }

    pub fn inc_writer_drop(&self) {
        self.inc(&self.inner.writer_dropped, 1);
    }

    fn get(&self, value: &AtomicU64) -> u64 {
        value.load(Ordering::Relaxed)
    }

    fn inc(&self, value: &AtomicU64, count: u64) {
        value.fetch_add(count, Ordering::Relaxed);
    }
}

fn to_unix_ns(value: DateTime<Utc>) -> i64 {
    value.timestamp()
        .saturating_mul(1_000_000_000)
        .saturating_add(i64::from(value.timestamp_subsec_nanos()))
}
