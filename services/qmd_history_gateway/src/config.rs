use qmd_core::config::HistoricalClickHouseConnection;
use serde::Serialize;
use std::env;

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalGatewayConfig {
    pub batch_size: usize,
    pub bind: String,
    pub cache_max_bars_per_entry: usize,
    pub cache_max_bytes: usize,
    pub cache_max_concurrent_builds: usize,
    pub cache_max_concurrent_fetches: usize,
    pub cache_max_updates_per_entry: usize,
    pub cache_max_entries: usize,
    pub cache_update_capacity: usize,
    pub clickhouse_database: String,
    #[serde(skip_serializing)]
    pub clickhouse_password: String,
    pub clickhouse_password_present: bool,
    pub clickhouse_url: String,
    pub clickhouse_user: String,
    pub max_events_per_request: usize,
    pub macro_bars_table: String,
    pub fetch_chunk_hours: usize,
    pub product_timeframes: Vec<String>,
    pub product_cache_max_rows_per_entry: usize,
    pub table_prefix: String,
}

impl HistoricalGatewayConfig {
    pub fn from_env() -> Self {
        let source = HistoricalClickHouseConnection::from_env();
        Self {
            batch_size: env_usize("QMD_HISTORY_BATCH_SIZE", 25_000).clamp(1, 100_000),
            bind: env_string("QMD_HISTORY_BIND", "127.0.0.1:8801"),
            cache_max_bars_per_entry: env_usize("QMD_HISTORY_CACHE_MAX_BARS_PER_ENTRY", 100_000)
                .clamp(1_000, 1_000_000),
            cache_max_bytes: env_usize("QMD_HISTORY_CACHE_MAX_BYTES", 1024 * 1024 * 1024)
                .clamp(16 * 1024 * 1024, 32 * 1024 * 1024 * 1024),
            cache_max_concurrent_builds: env_usize("QMD_HISTORY_CACHE_MAX_CONCURRENT_BUILDS", 4)
                .clamp(1, 64),
            cache_max_concurrent_fetches: env_usize("QMD_HISTORY_CACHE_MAX_CONCURRENT_FETCHES", 8)
                .clamp(1, 64),
            cache_max_updates_per_entry: env_usize(
                "QMD_HISTORY_CACHE_MAX_UPDATES_PER_ENTRY",
                500_000,
            )
            .clamp(10_000, 10_000_000),
            cache_max_entries: env_usize("QMD_HISTORY_CACHE_MAX_ENTRIES", 256).clamp(1, 10_000),
            cache_update_capacity: env_usize("QMD_HISTORY_CACHE_UPDATE_CAPACITY", 4_096)
                .clamp(16, 100_000),
            clickhouse_database: source.database,
            clickhouse_password_present: !source.password.is_empty(),
            clickhouse_password: source.password,
            clickhouse_url: source.url,
            clickhouse_user: source.user,
            max_events_per_request: env_usize("QMD_HISTORY_MAX_EVENTS_PER_REQUEST", 10_000_000)
                .max(1),
            macro_bars_table: env_string(
                "QMD_HISTORY_MACRO_BARS_TABLE",
                "macro_bars_by_time_symbol",
            ),
            fetch_chunk_hours: env_usize("QMD_HISTORY_FETCH_CHUNK_HOURS", 24).clamp(1, 168),
            product_timeframes: env_list(
                "QMD_HISTORY_PRODUCT_TIMEFRAMES",
                &["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"],
            ),
            product_cache_max_rows_per_entry: env_usize(
                "QMD_HISTORY_PRODUCT_CACHE_MAX_ROWS_PER_ENTRY",
                2_000_000,
            )
            .clamp(10_000, 20_000_000),
            table_prefix: env_string("QMD_HISTORY_TABLE_PREFIX", "events_"),
        }
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.clickhouse_url.trim().is_empty() {
            return Err("QMD_HISTORY_CLICKHOUSE_URL is required".to_string());
        }
        if self.clickhouse_user.trim().is_empty() {
            return Err("QMD_HISTORY_CLICKHOUSE_USER is required".to_string());
        }
        if !valid_identifier(&self.clickhouse_database) {
            return Err("QMD_HISTORY_DATABASE must be a ClickHouse identifier".to_string());
        }
        if !valid_identifier(&self.table_prefix) {
            return Err("QMD_HISTORY_TABLE_PREFIX must be an identifier prefix".to_string());
        }
        if !valid_identifier(&self.macro_bars_table) {
            return Err("QMD_HISTORY_MACRO_BARS_TABLE must be a ClickHouse identifier".to_string());
        }
        Ok(())
    }
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.chars().enumerate().all(|(index, ch)| {
            ch == '_' || ch.is_ascii_alphanumeric() && (index > 0 || !ch.is_ascii_digit())
        })
}

fn env_string(name: &str, default: &str) -> String {
    env::var(name)
        .unwrap_or_else(|_| default.to_string())
        .trim()
        .to_string()
}

fn env_usize(name: &str, default: usize) -> usize {
    env::var(name)
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .unwrap_or(default)
}

fn env_list(name: &str, default: &[&str]) -> Vec<String> {
    let values = env::var(name)
        .ok()
        .map(|value| {
            value
                .split(',')
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if values.is_empty() {
        default.iter().map(|value| (*value).to_string()).collect()
    } else {
        values
    }
}

#[cfg(test)]
mod tests {
    use super::valid_identifier;

    #[test]
    fn identifiers_are_strict() {
        assert!(valid_identifier("market_sip_compact"));
        assert!(valid_identifier("events_"));
        assert!(!valid_identifier("1events"));
        assert!(!valid_identifier("events_; DROP TABLE x"));
    }
}
