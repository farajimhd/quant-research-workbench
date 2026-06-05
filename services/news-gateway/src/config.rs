use serde::Serialize;
use std::env;

#[derive(Clone, Debug, Serialize)]
pub struct NewsGatewayConfig {
    pub api_key_present: bool,
    pub benzinga_enabled: bool,
    pub benzinga_poll_interval_ms: u64,
    pub benzinga_url: String,
    pub bind: String,
    pub clickhouse_database: String,
    pub clickhouse_password_present: bool,
    pub clickhouse_url: String,
    pub clickhouse_user: String,
    pub extraction_enabled: bool,
    pub extraction_min_body_chars: usize,
    pub extraction_timeout_ms: u64,
    pub flush_interval_ms: u64,
    pub general_enabled: bool,
    pub general_poll_interval_ms: u64,
    pub general_url: String,
    pub live_lookback_minutes: i64,
    #[serde(skip_serializing)]
    pub massive_api_key: String,
    pub max_batch: usize,
    pub max_pages_per_poll: usize,
    pub pdf_extraction_enabled: bool,
    pub pdf_max_bytes: usize,
    pub poll_limit: usize,
    pub poll_overlap_seconds: i64,
    pub recent_history_limit: usize,
    pub writer_channel_capacity: usize,
}

impl NewsGatewayConfig {
    pub fn from_env() -> Self {
        let massive_api_key = env_string("MASSIVE_API_KEY", "");
        let clickhouse_password = env_string("NEWS_CLICKHOUSE_PASSWORD", "")
            .or_else_non_empty(|| env_string("QMD_CLICKHOUSE_PASSWORD", ""));
        Self {
            api_key_present: !massive_api_key.is_empty(),
            benzinga_enabled: env_bool("NEWS_BENZINGA_ENABLED", true),
            benzinga_poll_interval_ms: env_u64("NEWS_BENZINGA_POLL_INTERVAL_MS", 5_000),
            benzinga_url: env_string("NEWS_MASSIVE_BENZINGA_URL", "https://api.massive.com/benzinga/v2/news"),
            bind: env_string("NEWS_GATEWAY_BIND", "127.0.0.1:8796"),
            clickhouse_database: env_string("NEWS_CLICKHOUSE_DATABASE", &env_string("QMD_CLICKHOUSE_DATABASE", "q_live")),
            clickhouse_password_present: !clickhouse_password.is_empty(),
            clickhouse_url: env_string("NEWS_CLICKHOUSE_URL", &env_string("QMD_CLICKHOUSE_URL", "http://localhost:8123"))
                .trim_end_matches('/')
                .to_string(),
            clickhouse_user: env_string("NEWS_CLICKHOUSE_USER", &env_string("QMD_CLICKHOUSE_USER", "default")),
            extraction_enabled: env_bool("NEWS_EXTRACTION_ENABLED", true),
            extraction_min_body_chars: env_usize("NEWS_EXTRACTION_MIN_BODY_CHARS", 300),
            extraction_timeout_ms: env_u64("NEWS_EXTRACTION_TIMEOUT_MS", 2_500),
            flush_interval_ms: env_u64("NEWS_CLICKHOUSE_FLUSH_INTERVAL_MS", 1_000),
            general_enabled: env_bool("NEWS_GENERAL_ENABLED", true),
            general_poll_interval_ms: env_u64("NEWS_GENERAL_POLL_INTERVAL_MS", 30_000),
            general_url: env_string("NEWS_MASSIVE_GENERAL_URL", "https://api.massive.com/v2/reference/news"),
            live_lookback_minutes: env_i64("NEWS_LIVE_LOOKBACK_MINUTES", 30),
            massive_api_key,
            max_batch: env_usize("NEWS_CLICKHOUSE_MAX_BATCH", 1_000),
            max_pages_per_poll: env_usize("NEWS_MAX_PAGES_PER_POLL", 5),
            pdf_extraction_enabled: env_bool("NEWS_PDF_EXTRACTION_ENABLED", true),
            pdf_max_bytes: env_usize("NEWS_PDF_MAX_BYTES", 10_000_000),
            poll_limit: env_usize("NEWS_POLL_LIMIT", 1_000).min(50_000),
            poll_overlap_seconds: env_i64("NEWS_POLL_OVERLAP_SECONDS", 120),
            recent_history_limit: env_usize("NEWS_RECENT_HISTORY_LIMIT", 5_000),
            writer_channel_capacity: env_usize("NEWS_WRITER_CHANNEL_CAPACITY", 100_000),
        }
    }

    pub fn clickhouse_password(&self) -> String {
        env_string("NEWS_CLICKHOUSE_PASSWORD", "")
            .or_else_non_empty(|| env_string("QMD_CLICKHOUSE_PASSWORD", ""))
    }
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

fn env_bool(name: &str, default: bool) -> bool {
    match env::var(name).ok().map(|value| value.trim().to_ascii_lowercase()) {
        Some(value) if matches!(value.as_str(), "1" | "true" | "yes" | "on") => true,
        Some(value) if matches!(value.as_str(), "0" | "false" | "no" | "off") => false,
        _ => default,
    }
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

fn env_i64(name: &str, default: i64) -> i64 {
    env::var(name)
        .ok()
        .and_then(|value| value.trim().parse::<i64>().ok())
        .filter(|value| *value >= 0)
        .unwrap_or(default)
}
