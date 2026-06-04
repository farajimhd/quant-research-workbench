use serde::Serialize;
use std::env;

#[derive(Clone, Debug, Serialize)]
pub struct GatewayConfig {
    pub api_key_present: bool,
    pub bind: String,
    pub clickhouse_database: String,
    pub clickhouse_password_present: bool,
    pub clickhouse_url: String,
    pub clickhouse_user: String,
    pub event_channel_capacity: usize,
    pub flush_interval_ms: u64,
    pub max_clickhouse_batch: usize,
    #[serde(skip_serializing)]
    pub massive_api_key: String,
    pub massive_ws_url: String,
    pub scanner_broadcast_ms: u64,
    pub subscribe_all_symbols: bool,
    pub subscribe_quotes: bool,
    pub subscribe_trades: bool,
    pub ticker_broadcast_ms: u64,
}

impl GatewayConfig {
    pub fn from_env() -> Self {
        let massive_api_key = env_string("MASSIVE_API_KEY", "");
        let clickhouse_password = env_string("QMD_CLICKHOUSE_PASSWORD", "");
        Self {
            api_key_present: !massive_api_key.is_empty(),
            bind: env_string("QMD_GATEWAY_BIND", "127.0.0.1:8795"),
            clickhouse_database: env_string("QMD_CLICKHOUSE_DATABASE", "q_live"),
            clickhouse_password_present: !clickhouse_password.is_empty(),
            clickhouse_url: env_string("QMD_CLICKHOUSE_URL", "http://localhost:8123").trim_end_matches('/').to_string(),
            clickhouse_user: env_string("QMD_CLICKHOUSE_USER", "default"),
            event_channel_capacity: env_usize("QMD_EVENT_CHANNEL_CAPACITY", 250_000),
            flush_interval_ms: env_u64("QMD_CLICKHOUSE_FLUSH_INTERVAL_MS", 1_000),
            max_clickhouse_batch: env_usize("QMD_CLICKHOUSE_MAX_BATCH", 10_000),
            massive_api_key,
            massive_ws_url: env_string("QMD_MASSIVE_WS_URL", "wss://socket.massive.com/stocks"),
            scanner_broadcast_ms: env_u64("QMD_SCANNER_BROADCAST_MS", 1_000),
            subscribe_all_symbols: env_bool("QMD_SUBSCRIBE_ALL_SYMBOLS", true),
            subscribe_quotes: env_bool("QMD_SUBSCRIBE_QUOTES", true),
            subscribe_trades: env_bool("QMD_SUBSCRIBE_TRADES", true),
            ticker_broadcast_ms: env_u64("QMD_TICKER_BROADCAST_MS", 250),
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
        env_string("QMD_CLICKHOUSE_PASSWORD", "")
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
