use chrono::{DateTime, Utc};
use serde::Serialize;
use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Serialize)]
pub struct GatewayConfig {
    pub api_key_present: bool,
    pub bar_channel_capacity: usize,
    pub bar_history_limit: usize,
    pub bar_shard_count: usize,
    pub bar_timeframes: Vec<String>,
    pub bind: String,
    pub clickhouse_database: String,
    pub clickhouse_password_present: bool,
    pub clickhouse_storage_policy: String,
    pub clickhouse_url: String,
    pub clickhouse_user: String,
    pub compact_event_channel_capacity: usize,
    pub compact_event_continuity_table: String,
    pub compact_event_live_buffer_events_per_ticker: usize,
    pub compact_event_reorder_force_flush_ms: u64,
    pub compact_event_reorder_lag_ms: u64,
    pub compact_event_reorder_max_events_per_ticker: usize,
    pub compact_event_table: String,
    pub compact_events_enabled: bool,
    pub event_channel_capacity: usize,
    pub flush_interval_ms: u64,
    pub gap_fill_enabled: bool,
    pub gap_fill_interval_ms: u64,
    pub gap_fill_awaiting_symbols_retry_ms: u64,
    pub gap_fill_mode: String,
    pub gap_fill_lookback_minutes: i64,
    pub gap_fill_max_lookback_days: i64,
    pub gap_fill_max_pages_per_symbol: usize,
    pub gap_fill_min_gap_seconds: i64,
    pub recent_live_max_pages_per_interval: usize,
    pub recent_live_prior_market_days: i64,
    pub recent_live_repair_concurrency: usize,
    pub historical_clickhouse_database: String,
    pub historical_clickhouse_password_present: bool,
    pub historical_clickhouse_url: String,
    pub historical_clickhouse_user: String,
    pub historical_flatfile_autorun: bool,
    pub historical_flatfile_safe_lag_days: i64,
    pub historical_flatfile_update_enabled: bool,
    pub historical_known_coverage_end_date: String,
    pub historical_pipeline_code_root: String,
    pub indicator_bar_channel_capacity: usize,
    pub indicator_channel_capacity: usize,
    pub indicator_history_by_timeframe: HashMap<String, usize>,
    pub indicator_history_limit: usize,
    pub persist_indicators: bool,
    pub persist_compact_events: bool,
    pub persist_raw_events: bool,
    pub indicator_shard_count: usize,
    pub max_clickhouse_batch: usize,
    #[serde(skip_serializing)]
    pub massive_api_key: String,
    pub massive_ws_url: String,
    pub qmd_coverage_table: String,
    pub qmd_flatfile_event_coverage_table: String,
    pub qmd_gap_fill_symbol_universe_table: String,
    pub qmd_gap_fill_universe_market_days: usize,
    pub qmd_host_role: String,
    pub qmd_live_event_coverage_table: String,
    pub qmd_run_id: String,
    pub qmd_run_started_at_utc: String,
    pub qmd_startup_maintenance_enabled: bool,
    pub replay_enabled: bool,
    pub replay_date: String,
    pub replay_max_rows: usize,
    pub replay_symbols: Vec<String>,
    pub reference_dir: String,
    pub scanner_primitive_channel_capacity: usize,
    pub scanner_primitive_history_limit: usize,
    pub scanner_broadcast_ms: u64,
    pub subscribe_all_symbols: bool,
    pub subscribe_quotes: bool,
    pub subscribe_trades: bool,
    pub ticker_broadcast_ms: u64,
    pub tick_indicator_window_seconds: i64,
}

impl GatewayConfig {
    pub fn from_env() -> Self {
        let massive_api_key = env_string("MASSIVE_API_KEY", "");
        let clickhouse_password = env_string_any(
            &[
                "QMD_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "CLICKHOUSE_WORKSTATION_PASSWORD",
                "CLICKHOUSE_PASSWORD",
            ],
            "",
        );
        let historical_clickhouse_password = env_string_any(
            &[
                "QMD_HISTORICAL_CLICKHOUSE_PASSWORD",
                "CLICKHOUSE_WORKSTATION_PASSWORD",
                "QMD_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "CLICKHOUSE_PASSWORD",
            ],
            "",
        );
        Self {
            api_key_present: !massive_api_key.is_empty(),
            bar_channel_capacity: env_usize("QMD_BAR_CHANNEL_CAPACITY", 250_000),
            bar_history_limit: env_usize("QMD_BAR_HISTORY_LIMIT", 1_000),
            bar_shard_count: env_usize("QMD_BAR_SHARD_COUNT", 8),
            bar_timeframes: env_list_with_default(
                "QMD_BAR_TIMEFRAMES",
                &["1s", "10s", "30s", "1m", "5m", "1h"],
            ),
            bind: env_string("QMD_GATEWAY_BIND", "127.0.0.1:8795"),
            clickhouse_database: env_string_any(
                &[
                    "QMD_CLICKHOUSE_DATABASE",
                    "REAL_LIVE_CLICKHOUSE_WRITE_DATABASE",
                ],
                "q_live",
            ),
            clickhouse_password_present: !clickhouse_password.is_empty(),
            clickhouse_storage_policy: env_string(
                "QMD_CLICKHOUSE_STORAGE_POLICY",
                &env_string("CLICKHOUSE_LIVE_STORAGE_POLICY", ""),
            ),
            clickhouse_url: env_string_any(
                &[
                    "QMD_CLICKHOUSE_URL",
                    "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                    "CLICKHOUSE_URL",
                    "CLICKHOUSE_ENDPOINT",
                ],
                "http://localhost:8123",
            )
            .trim_end_matches('/')
            .to_string(),
            clickhouse_user: env_string_any(
                &[
                    "QMD_CLICKHOUSE_USER",
                    "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                    "CLICKHOUSE_WORKSTATION_USER",
                    "CLICKHOUSE_USER",
                ],
                "default",
            ),
            compact_event_channel_capacity: env_usize(
                "QMD_COMPACT_EVENT_CHANNEL_CAPACITY",
                250_000,
            ),
            compact_event_continuity_table: env_string(
                "QMD_COMPACT_EVENT_CONTINUITY_TABLE",
                "live_event_ordinal_continuity",
            ),
            compact_event_live_buffer_events_per_ticker: env_usize(
                "QMD_COMPACT_EVENT_LIVE_BUFFER_EVENTS_PER_TICKER",
                512,
            ),
            compact_event_reorder_force_flush_ms: env_u64(
                "QMD_COMPACT_EVENT_REORDER_FORCE_FLUSH_MS",
                2_000,
            ),
            compact_event_reorder_lag_ms: env_u64("QMD_COMPACT_EVENT_REORDER_LAG_MS", 500),
            compact_event_reorder_max_events_per_ticker: env_usize(
                "QMD_COMPACT_EVENT_REORDER_MAX_EVENTS_PER_TICKER",
                4_096,
            ),
            compact_event_table: env_string("QMD_COMPACT_EVENT_TABLE", "live_market_events_v2"),
            compact_events_enabled: env_bool("QMD_COMPACT_EVENTS_ENABLED", true),
            event_channel_capacity: env_usize("QMD_EVENT_CHANNEL_CAPACITY", 250_000),
            flush_interval_ms: env_u64("QMD_CLICKHOUSE_FLUSH_INTERVAL_MS", 5_000),
            gap_fill_enabled: env_bool("QMD_GAP_FILL_ENABLED", true),
            gap_fill_interval_ms: env_u64("QMD_GAP_FILL_INTERVAL_MS", 300_000),
            gap_fill_awaiting_symbols_retry_ms: env_u64(
                "QMD_GAP_FILL_AWAITING_SYMBOLS_RETRY_MS",
                10_000,
            ),
            gap_fill_mode: env_string("QMD_GAP_FILL_MODE", "auto").to_ascii_lowercase(),
            gap_fill_lookback_minutes: env_i64("QMD_GAP_FILL_LOOKBACK_MINUTES", 120),
            gap_fill_max_lookback_days: env_i64("QMD_GAP_FILL_MAX_LOOKBACK_DAYS", 3),
            gap_fill_max_pages_per_symbol: env_usize("QMD_GAP_FILL_MAX_PAGES_PER_SYMBOL", 5),
            gap_fill_min_gap_seconds: env_i64("QMD_GAP_FILL_MIN_GAP_SECONDS", 1),
            recent_live_max_pages_per_interval: env_usize(
                "QMD_RECENT_LIVE_MAX_PAGES_PER_INTERVAL",
                1_000,
            ),
            recent_live_prior_market_days: env_i64("QMD_RECENT_LIVE_PRIOR_MARKET_DAYS", 3),
            recent_live_repair_concurrency: env_usize("QMD_RECENT_LIVE_REPAIR_CONCURRENCY", 8),
            historical_clickhouse_database: env_string(
                "QMD_HISTORICAL_CLICKHOUSE_DATABASE",
                "market_sip_compact",
            ),
            historical_clickhouse_password_present: !historical_clickhouse_password.is_empty(),
            historical_clickhouse_url: env_string_any(
                &[
                    "QMD_HISTORICAL_CLICKHOUSE_URL",
                    "HISTORICAL_CLICKHOUSE_URL",
                    "QMD_CLICKHOUSE_URL",
                    "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                    "CLICKHOUSE_URL",
                    "CLICKHOUSE_ENDPOINT",
                ],
                "http://localhost:8123",
            )
            .trim_end_matches('/')
            .to_string(),
            historical_clickhouse_user: env_string_any(
                &[
                    "QMD_HISTORICAL_CLICKHOUSE_USER",
                    "CLICKHOUSE_WORKSTATION_USER",
                    "QMD_CLICKHOUSE_USER",
                    "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                    "CLICKHOUSE_USER",
                ],
                "default",
            ),
            historical_flatfile_autorun: env_bool("QMD_HISTORICAL_FLATFILE_AUTORUN", false),
            historical_flatfile_safe_lag_days: env_i64("QMD_HISTORICAL_FLATFILE_SAFE_LAG_DAYS", 1),
            historical_flatfile_update_enabled: env_bool(
                "QMD_HISTORICAL_FLATFILE_UPDATE_ENABLED",
                true,
            ),
            historical_known_coverage_end_date: env_string(
                "QMD_HISTORICAL_KNOWN_COVERAGE_END_DATE",
                "2026-06-05",
            ),
            historical_pipeline_code_root: env_string(
                "QMD_HISTORICAL_PIPELINE_CODE_ROOT",
                "D:\\TradingML\\codes\\quant_research_workbench_pipelines",
            ),
            indicator_bar_channel_capacity: env_usize(
                "QMD_INDICATOR_BAR_CHANNEL_CAPACITY",
                250_000,
            ),
            indicator_channel_capacity: env_usize("QMD_INDICATOR_CHANNEL_CAPACITY", 250_000),
            indicator_history_by_timeframe: env_timeframe_limit_map(
                "QMD_INDICATOR_HISTORY_BY_TIMEFRAME",
                &[
                    ("1s", 900),
                    ("10s", 360),
                    ("30s", 480),
                    ("1m", 960),
                    ("5m", 192),
                    ("1h", 32),
                ],
            ),
            indicator_history_limit: env_usize("QMD_INDICATOR_HISTORY_LIMIT", 1_000),
            persist_indicators: env_bool("QMD_PERSIST_INDICATORS", false),
            persist_compact_events: env_bool("QMD_PERSIST_COMPACT_EVENTS", true),
            persist_raw_events: env_bool("QMD_PERSIST_RAW_EVENTS", false),
            indicator_shard_count: env_usize("QMD_INDICATOR_SHARD_COUNT", 8),
            max_clickhouse_batch: env_usize("QMD_CLICKHOUSE_MAX_BATCH", 10_000),
            massive_api_key,
            massive_ws_url: env_string("QMD_MASSIVE_WS_URL", "wss://socket.massive.com/stocks"),
            qmd_coverage_table: env_string("QMD_COVERAGE_TABLE", "qmd_market_coverage_manifest_v1"),
            qmd_flatfile_event_coverage_table: env_string(
                "QMD_FLATFILE_EVENT_COVERAGE_TABLE",
                "qmd_flatfile_event_coverage_v1",
            ),
            qmd_gap_fill_symbol_universe_table: env_string(
                "QMD_GAP_FILL_SYMBOL_UNIVERSE_TABLE",
                "qmd_gap_fill_symbol_universe_v1",
            ),
            qmd_gap_fill_universe_market_days: env_usize("QMD_GAP_FILL_UNIVERSE_MARKET_DAYS", 5),
            qmd_host_role: env_string("QMD_HOST_ROLE", "auto").to_ascii_lowercase(),
            qmd_live_event_coverage_table: env_string(
                "QMD_LIVE_EVENT_COVERAGE_TABLE",
                "qmd_live_event_coverage_v1",
            ),
            qmd_run_id: env_string("QMD_RUN_ID", &default_qmd_run_id()),
            qmd_run_started_at_utc: env_string(
                "QMD_RUN_STARTED_AT_UTC",
                &Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            ),
            qmd_startup_maintenance_enabled: env_bool("QMD_STARTUP_MAINTENANCE_ENABLED", true),
            replay_enabled: env_bool("QMD_REPLAY_ENABLED", false),
            replay_date: env_string("QMD_REPLAY_DATE", ""),
            replay_max_rows: env_usize("QMD_REPLAY_MAX_ROWS", 1_000_000),
            replay_symbols: env_list("QMD_REPLAY_SYMBOLS"),
            reference_dir: env_string("QMD_REFERENCE_DIR", &default_reference_dir()),
            scanner_primitive_channel_capacity: env_usize(
                "QMD_SCANNER_PRIMITIVE_CHANNEL_CAPACITY",
                250_000,
            ),
            scanner_primitive_history_limit: env_usize(
                "QMD_SCANNER_PRIMITIVE_HISTORY_LIMIT",
                10_000,
            ),
            scanner_broadcast_ms: env_u64("QMD_SCANNER_BROADCAST_MS", 1_000),
            subscribe_all_symbols: env_bool("QMD_SUBSCRIBE_ALL_SYMBOLS", true),
            subscribe_quotes: env_bool("QMD_SUBSCRIBE_QUOTES", true),
            subscribe_trades: env_bool("QMD_SUBSCRIBE_TRADES", true),
            ticker_broadcast_ms: env_u64("QMD_TICKER_BROADCAST_MS", 250),
            tick_indicator_window_seconds: env_i64("QMD_TICK_INDICATOR_WINDOW_SECONDS", 300),
        }
    }

    pub fn subscription_channels(&self) -> Vec<String> {
        if self.subscribe_all_symbols {
            let mut channels = Vec::new();
            if self.subscribe_trades {
                channels.push("T.*".to_string());
            }
            if self.subscribe_quotes {
                channels.push("Q.*".to_string());
            }
            return channels;
        }
        Vec::new()
    }

    pub fn clickhouse_password(&self) -> String {
        env_string_any(
            &[
                "QMD_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "CLICKHOUSE_WORKSTATION_PASSWORD",
                "CLICKHOUSE_PASSWORD",
            ],
            "",
        )
    }

    pub fn historical_clickhouse_password(&self) -> String {
        env_string_any(
            &[
                "QMD_HISTORICAL_CLICKHOUSE_PASSWORD",
                "CLICKHOUSE_WORKSTATION_PASSWORD",
                "QMD_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "CLICKHOUSE_PASSWORD",
            ],
            "",
        )
    }

    pub fn qmd_run_started_at(&self) -> Option<DateTime<Utc>> {
        DateTime::parse_from_rfc3339(&self.qmd_run_started_at_utc)
            .ok()
            .map(|value| value.with_timezone(&Utc))
    }
}

pub fn load_env_files() -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Ok(raw) = env::var("DOTENV_PATHS") {
        paths.extend(env::split_paths(&raw));
    }
    if let Ok(cwd) = env::current_dir() {
        push_ancestor_env_files(&mut paths, &cwd);
    }
    push_ancestor_env_files(&mut paths, Path::new(env!("CARGO_MANIFEST_DIR")));

    let mut loaded = Vec::new();
    let mut seen = Vec::<PathBuf>::new();
    for path in paths {
        let key = path.canonicalize().unwrap_or(path.clone());
        if seen.iter().any(|existing| existing == &key) {
            continue;
        }
        seen.push(key);
        if path.exists() && load_env_file(&path).is_ok() {
            loaded.push(path);
        }
    }
    loaded
}

fn push_ancestor_env_files(paths: &mut Vec<PathBuf>, start: &Path) {
    let mut current = Some(start);
    while let Some(path) = current {
        paths.push(path.join(".env"));
        current = path.parent();
    }
}

fn load_env_file(path: &Path) -> Result<(), std::io::Error> {
    let text = fs::read_to_string(path)?;
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let Some((key, value)) = trimmed.split_once('=') else {
            continue;
        };
        let key = key.trim();
        if key.is_empty() || env::var_os(key).is_some() {
            continue;
        }
        let value = value.trim().trim_matches('"').trim_matches('\'');
        env::set_var(key, value);
    }
    Ok(())
}

fn env_string(name: &str, default: &str) -> String {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| default.to_string())
}

fn default_qmd_run_id() -> String {
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or_default();
    format!("qmd_{}_{}", millis, std::process::id())
}

fn env_string_any(names: &[&str], default: &str) -> String {
    names
        .iter()
        .find_map(|name| {
            env::var(name)
                .ok()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty())
        })
        .unwrap_or_else(|| default.to_string())
}

fn env_bool(name: &str, default: bool) -> bool {
    match env::var(name)
        .ok()
        .map(|value| value.trim().to_ascii_lowercase())
    {
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
        .filter(|value| *value > 0)
        .unwrap_or(default)
}

fn env_list(name: &str) -> Vec<String> {
    env::var(name)
        .ok()
        .unwrap_or_default()
        .split(',')
        .filter_map(|value| value.split('#').next())
        .map(|value| value.trim().to_ascii_uppercase())
        .filter(|value| !value.is_empty())
        .collect()
}

fn env_list_with_default(name: &str, default: &[&str]) -> Vec<String> {
    let values = env_list(name);
    if values.is_empty() {
        default.iter().map(|value| value.to_string()).collect()
    } else {
        values
            .into_iter()
            .map(|value| value.to_ascii_lowercase())
            .collect()
    }
}

fn default_reference_dir() -> String {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("research")
        .join("market_references")
        .join("massive")
        .to_string_lossy()
        .to_string()
}

fn env_timeframe_limit_map(name: &str, default: &[(&str, usize)]) -> HashMap<String, usize> {
    let mut values = default
        .iter()
        .map(|(timeframe, limit)| ((*timeframe).to_string(), *limit))
        .collect::<HashMap<_, _>>();
    if let Ok(raw) = env::var(name) {
        for item in raw.split(',') {
            let Some((timeframe, limit)) = item.split_once(':') else {
                continue;
            };
            let timeframe = timeframe.trim().to_ascii_lowercase();
            if timeframe.is_empty() {
                continue;
            }
            if let Ok(limit) = limit.trim().parse::<usize>() {
                if limit > 0 {
                    values.insert(timeframe, limit);
                }
            }
        }
    }
    values
}
