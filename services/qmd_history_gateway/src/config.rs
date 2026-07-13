use qmd_core::config::HistoricalClickHouseConnection;
use serde::Serialize;
use std::env;

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalGatewayConfig {
    pub batch_size: usize,
    pub bind: String,
    pub clickhouse_database: String,
    #[serde(skip_serializing)]
    pub clickhouse_password: String,
    pub clickhouse_password_present: bool,
    pub clickhouse_url: String,
    pub clickhouse_user: String,
    pub max_events_per_request: usize,
    pub table_prefix: String,
}

impl HistoricalGatewayConfig {
    pub fn from_env() -> Self {
        let source = HistoricalClickHouseConnection::from_env();
        Self {
            batch_size: env_usize("QMD_HISTORY_BATCH_SIZE", 25_000).clamp(1, 100_000),
            bind: env_string("QMD_HISTORY_BIND", "127.0.0.1:8801"),
            clickhouse_database: source.database,
            clickhouse_password_present: !source.password.is_empty(),
            clickhouse_password: source.password,
            clickhouse_url: source.url,
            clickhouse_user: source.user,
            max_events_per_request: env_usize("QMD_HISTORY_MAX_EVENTS_PER_REQUEST", 2_000_000)
                .max(1),
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
