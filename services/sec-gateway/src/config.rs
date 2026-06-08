use serde::Serialize;
use std::env;

#[derive(Clone, Debug, Serialize)]
pub struct SecGatewayConfig {
    pub artifact_root_win: String,
    pub bind: String,
    pub clickhouse_database: String,
    pub clickhouse_password_present: bool,
    pub clickhouse_storage_policy: String,
    pub clickhouse_url: String,
    pub clickhouse_user: String,
    pub document_max_bytes: usize,
    pub document_table: String,
    pub event_table: String,
    pub feed_poll_interval_ms: u64,
    pub feed_url: String,
    pub flush_interval_ms: u64,
    pub max_batch: usize,
    pub max_documents_per_filing: usize,
    pub recent_history_limit: usize,
    pub request_timeout_ms: u64,
    pub user_agent_present: bool,
    pub writer_channel_capacity: usize,
}

impl SecGatewayConfig {
    pub fn from_env() -> Self {
        let clickhouse_password = env_string("SEC_CLICKHOUSE_PASSWORD", "")
            .or_else_non_empty(|| env_string("NEWS_CLICKHOUSE_PASSWORD", ""))
            .or_else_non_empty(|| env_string("QMD_CLICKHOUSE_PASSWORD", ""));
        let user_agent = sec_user_agent();
        Self {
            artifact_root_win: env_string("SEC_ARTIFACT_ROOT_WIN", "D:/market-data/sec_live"),
            bind: env_string("SEC_GATEWAY_BIND", "127.0.0.1:8798"),
            clickhouse_database: env_string("SEC_CLICKHOUSE_DATABASE", &env_string("QMD_CLICKHOUSE_DATABASE", "q_live")),
            clickhouse_password_present: !clickhouse_password.is_empty(),
            clickhouse_storage_policy: env_string("SEC_CLICKHOUSE_STORAGE_POLICY", &env_string("CLICKHOUSE_LIVE_STORAGE_POLICY", "")),
            clickhouse_url: env_string("SEC_CLICKHOUSE_URL", &env_string("QMD_CLICKHOUSE_URL", "http://localhost:8123"))
                .trim_end_matches('/')
                .to_string(),
            clickhouse_user: env_string("SEC_CLICKHOUSE_USER", &env_string("QMD_CLICKHOUSE_USER", "default")),
            document_max_bytes: env_usize("SEC_DOCUMENT_MAX_BYTES", 12_000_000),
            document_table: env_string("SEC_DOCUMENT_TABLE", "live_sec_filing_documents_v1"),
            event_table: env_string("SEC_EVENT_TABLE", "live_sec_filing_events_v1"),
            feed_poll_interval_ms: env_u64("SEC_FEED_POLL_INTERVAL_MS", 5_000),
            feed_url: env_string(
                "SEC_LATEST_FEED_URL",
                "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&count=100&output=atom",
            ),
            flush_interval_ms: env_u64("SEC_CLICKHOUSE_FLUSH_INTERVAL_MS", 1_000),
            max_batch: env_usize("SEC_CLICKHOUSE_MAX_BATCH", 1_000),
            max_documents_per_filing: env_usize("SEC_MAX_DOCUMENTS_PER_FILING", 8),
            recent_history_limit: env_usize("SEC_RECENT_HISTORY_LIMIT", 5_000),
            request_timeout_ms: env_u64("SEC_REQUEST_TIMEOUT_MS", 10_000),
            user_agent_present: !user_agent.is_empty(),
            writer_channel_capacity: env_usize("SEC_WRITER_CHANNEL_CAPACITY", 100_000),
        }
    }

    pub fn clickhouse_password(&self) -> String {
        env_string("SEC_CLICKHOUSE_PASSWORD", "")
            .or_else_non_empty(|| env_string("NEWS_CLICKHOUSE_PASSWORD", ""))
            .or_else_non_empty(|| env_string("QMD_CLICKHOUSE_PASSWORD", ""))
    }

    pub fn user_agent(&self) -> String {
        sec_user_agent()
    }
}

fn sec_user_agent() -> String {
    env_string("SEC_USER_AGENT", &env_string("NEWS_SEC_USER_AGENT", &env_string("SEC_EDGAR_USER_AGENT", "")))
}

trait NonEmptyFallback {
    fn or_else_non_empty<F: FnOnce() -> String>(self, fallback: F) -> String;
}

impl NonEmptyFallback for String {
    fn or_else_non_empty<F: FnOnce() -> String>(self, fallback: F) -> String {
        if self.trim().is_empty() {
            fallback()
        } else {
            self
        }
    }
}

fn env_string(name: &str, default: &str) -> String {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| default.to_string())
}

fn env_usize(name: &str, default: usize) -> usize {
    env::var(name)
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(default)
}

fn env_u64(name: &str, default: u64) -> u64 {
    env::var(name)
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(default)
}
