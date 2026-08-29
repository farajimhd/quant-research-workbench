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
    pub live_gateway_url: String,
    pub max_events_per_request: usize,
    pub recent_database: String,
    pub recent_event_table: String,
    pub recent_event_coverage_table: String,
    pub recent_focused_repair_table: String,
    pub daily_session_bars_table: String,
    pub intraday_base_bars_table: String,
    pub fetch_chunk_hours: usize,
    pub product_timeframes: Vec<String>,
    pub product_cache_max_rows_per_entry: usize,
    pub scanner_cache_max_entries: usize,
    pub scanner_fetch_chunk_minutes: usize,
    pub scanner_max_events_per_snapshot: usize,
    pub scanner_shard_count: usize,
    pub structure_checkpoint_max_concurrent_advancements: usize,
    pub structure_checkpoint_max_events: usize,
    pub structure_checkpoint_rebuild_max_events: usize,
    pub structure_checkpoint_max_window_hours: usize,
    pub structure_book_lookback_days: usize,
    pub structure_book_max_seed_events: usize,
    pub structure_book_rebuild_days: usize,
    pub structure_database: String,
    pub structure_events_table: String,
    pub table_prefix: String,
    pub watchlist_max_concurrent_materializations: usize,
    pub watchlist_request_max_bytes: usize,
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
            live_gateway_url: env_string("QMD_HISTORY_LIVE_GATEWAY_URL", "http://127.0.0.1:8795"),
            max_events_per_request: env_usize("QMD_HISTORY_MAX_EVENTS_PER_REQUEST", 10_000_000)
                .max(1),
            recent_database: env_string("QMD_HISTORY_RECENT_DATABASE", "q_live"),
            recent_event_table: env_string("QMD_HISTORY_RECENT_EVENT_TABLE", "events"),
            recent_event_coverage_table: env_string(
                "QMD_HISTORY_RECENT_EVENT_COVERAGE_TABLE",
                "qmd_live_event_coverage_v1",
            ),
            recent_focused_repair_table: env_string(
                "QMD_HISTORY_RECENT_FOCUSED_REPAIR_TABLE",
                "qmd_gap_fill_symbol_universe_v1",
            ),
            daily_session_bars_table: env_string(
                "QMD_HISTORY_DAILY_SESSION_BARS_TABLE",
                "daily_session_bars_by_symbol_time_v1",
            ),
            intraday_base_bars_table: env_string(
                "QMD_HISTORY_INTRADAY_BASE_BARS_TABLE",
                "intraday_base_bars_by_time_ticker",
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
            scanner_cache_max_entries: env_usize("QMD_HISTORY_SCANNER_CACHE_MAX_ENTRIES", 2)
                .clamp(1, 16),
            scanner_fetch_chunk_minutes: env_usize("QMD_HISTORY_SCANNER_FETCH_CHUNK_MINUTES", 30)
                .clamp(1, 1_440),
            scanner_max_events_per_snapshot: env_usize(
                "QMD_HISTORY_SCANNER_MAX_EVENTS_PER_SNAPSHOT",
                250_000_000,
            )
            .max(1),
            scanner_shard_count: env_usize("QMD_HISTORY_SCANNER_SHARD_COUNT", 16).clamp(1, 128),
            structure_checkpoint_max_concurrent_advancements: env_usize(
                "QMD_HISTORY_STRUCTURE_CHECKPOINT_MAX_CONCURRENT_ADVANCEMENTS",
                4,
            )
            .clamp(1, 32),
            structure_checkpoint_max_events: env_usize(
                "QMD_HISTORY_STRUCTURE_CHECKPOINT_MAX_EVENTS",
                5_000_000,
            )
            .clamp(1_000, 50_000_000),
            structure_checkpoint_rebuild_max_events: env_usize(
                "QMD_HISTORY_STRUCTURE_CHECKPOINT_REBUILD_MAX_EVENTS",
                50_000_000,
            )
            .clamp(1_000, 250_000_000),
            structure_checkpoint_max_window_hours: env_usize(
                "QMD_HISTORY_STRUCTURE_CHECKPOINT_MAX_WINDOW_HOURS",
                72,
            )
            .clamp(1, 168),
            structure_book_lookback_days: env_usize(
                "QMD_HISTORY_STRUCTURE_BOOK_LOOKBACK_DAYS",
                180,
            )
            .clamp(2, 3_650),
            structure_book_max_seed_events: env_usize(
                "QMD_HISTORY_STRUCTURE_BOOK_MAX_SEED_EVENTS",
                2_000_000,
            )
            .clamp(10_000, 10_000_000),
            structure_book_rebuild_days: env_usize("QMD_HISTORY_STRUCTURE_BOOK_REBUILD_DAYS", 7)
                .clamp(2, 30),
            structure_database: env_string("QMD_HISTORY_STRUCTURE_DATABASE", "q_live"),
            structure_events_table: env_string(
                "QMD_HISTORY_STRUCTURE_EVENTS_TABLE",
                "qmd_structure_events_v2",
            ),
            table_prefix: env_string("QMD_HISTORY_TABLE_PREFIX", "events_"),
            watchlist_max_concurrent_materializations: env_usize(
                "QMD_HISTORY_WATCHLIST_MAX_CONCURRENT_MATERIALIZATIONS",
                1,
            )
            .clamp(1, 8),
            watchlist_request_max_bytes: env_usize(
                "QMD_HISTORY_WATCHLIST_REQUEST_MAX_BYTES",
                64 * 1024 * 1024,
            )
            .clamp(1024 * 1024, 256 * 1024 * 1024),
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
        if !valid_identifier(&self.recent_database) {
            return Err("QMD_HISTORY_RECENT_DATABASE must be a ClickHouse identifier".to_string());
        }
        if !valid_identifier(&self.recent_event_table) {
            return Err(
                "QMD_HISTORY_RECENT_EVENT_TABLE must be a ClickHouse identifier".to_string(),
            );
        }
        if !valid_identifier(&self.recent_event_coverage_table) {
            return Err(
                "QMD_HISTORY_RECENT_EVENT_COVERAGE_TABLE must be a ClickHouse identifier"
                    .to_string(),
            );
        }
        if !valid_identifier(&self.recent_focused_repair_table) {
            return Err(
                "QMD_HISTORY_RECENT_FOCUSED_REPAIR_TABLE must be a ClickHouse identifier"
                    .to_string(),
            );
        }
        if !(self.live_gateway_url.starts_with("http://")
            || self.live_gateway_url.starts_with("https://"))
        {
            return Err("QMD_HISTORY_LIVE_GATEWAY_URL must be an HTTP(S) URL".to_string());
        }
        if !valid_identifier(&self.daily_session_bars_table) {
            return Err(
                "QMD_HISTORY_DAILY_SESSION_BARS_TABLE must be a ClickHouse identifier".to_string(),
            );
        }
        if !valid_identifier(&self.intraday_base_bars_table) {
            return Err(
                "QMD_HISTORY_INTRADAY_BASE_BARS_TABLE must be a ClickHouse identifier".to_string(),
            );
        }
        if !valid_identifier(&self.structure_database) {
            return Err(
                "QMD_HISTORY_STRUCTURE_DATABASE must be a ClickHouse identifier".to_string(),
            );
        }
        if !valid_identifier(&self.structure_events_table) {
            return Err(
                "QMD_HISTORY_STRUCTURE_EVENTS_TABLE must be a ClickHouse identifier".to_string(),
            );
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
