use crate::config::GatewayConfig;
use crate::metrics::SharedMetrics;
use reqwest::Client;
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tokio::time::{interval, Duration};

#[derive(Clone, Debug)]
pub struct ReferenceTradabilityStatus {
    pub is_tradable: bool,
}

#[derive(Clone)]
pub struct SharedReferenceTradabilityStore {
    fail_closed: bool,
    inner: Arc<RwLock<ReferenceTradabilityStore>>,
}

#[derive(Clone, Debug)]
struct ReferenceTradabilityStore {
    enabled: bool,
    loaded: bool,
    latest_universe_date: Option<String>,
    symbols: HashMap<String, ReferenceTradabilityStatus>,
}

#[derive(Debug, Deserialize)]
struct ReferenceTradabilityRow {
    ticker: String,
    is_tradable: u8,
    universe_date: String,
}

impl SharedReferenceTradabilityStore {
    pub fn new(enabled: bool, fail_closed: bool) -> Self {
        Self {
            fail_closed,
            inner: Arc::new(RwLock::new(ReferenceTradabilityStore {
                enabled,
                loaded: !enabled,
                latest_universe_date: None,
                symbols: HashMap::new(),
            })),
        }
    }

    pub async fn replace(
        &self,
        universe_date: Option<String>,
        symbols: HashMap<String, ReferenceTradabilityStatus>,
    ) {
        let mut guard = self.inner.write().await;
        guard.loaded = true;
        guard.latest_universe_date = universe_date;
        guard.symbols = symbols;
    }

    pub async fn is_emit_allowed(&self, ticker: &str) -> bool {
        let normalized = ticker.to_ascii_uppercase();
        let guard = self.inner.read().await;
        if !guard.enabled {
            return true;
        }
        if !guard.loaded {
            return !self.fail_closed;
        }
        guard
            .symbols
            .get(&normalized)
            .map(|status| status.is_tradable)
            .unwrap_or(!self.fail_closed)
    }

    pub async fn summary(&self) -> ReferenceTradabilitySummary {
        let guard = self.inner.read().await;
        let blocked = guard
            .symbols
            .values()
            .filter(|status| !status.is_tradable)
            .count();
        ReferenceTradabilitySummary {
            enabled: guard.enabled,
            loaded: guard.loaded,
            latest_universe_date: guard.latest_universe_date.clone(),
            symbols: guard.symbols.len(),
            blocked,
            fail_closed: self.fail_closed,
        }
    }
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct ReferenceTradabilitySummary {
    pub enabled: bool,
    pub loaded: bool,
    pub latest_universe_date: Option<String>,
    pub symbols: usize,
    pub blocked: usize,
    pub fail_closed: bool,
}

pub async fn refresh_reference_tradability_once(
    config: &GatewayConfig,
    store: &SharedReferenceTradabilityStore,
    metrics: &SharedMetrics,
) -> Result<ReferenceTradabilitySummary, String> {
    if !config.reference_tradability_enabled {
        return Ok(store.summary().await);
    }
    let rows = query_reference_tradability(config).await?;
    let latest_universe_date = rows.first().map(|row| row.universe_date.clone());
    let mut symbols = HashMap::with_capacity(rows.len());
    for row in rows {
        if row.ticker.trim().is_empty() {
            continue;
        }
        symbols.insert(
            row.ticker.to_ascii_uppercase(),
            ReferenceTradabilityStatus {
                is_tradable: row.is_tradable == 1,
            },
        );
    }
    let blocked = symbols
        .values()
        .filter(|status| !status.is_tradable)
        .count() as u64;
    let loaded = symbols.len() as u64;
    store.replace(latest_universe_date, symbols).await;
    metrics.set_reference_tradability_counts(loaded, blocked);
    Ok(store.summary().await)
}

pub fn spawn_reference_tradability_refresh(
    config: GatewayConfig,
    store: SharedReferenceTradabilityStore,
    metrics: SharedMetrics,
) {
    tokio::spawn(async move {
        let mut timer = interval(Duration::from_millis(
            config.reference_tradability_refresh_ms.max(5_000),
        ));
        loop {
            timer.tick().await;
            match refresh_reference_tradability_once(&config, &store, &metrics).await {
                Ok(summary) => {
                    eprintln!(
                        "QMD reference tradability refreshed: enabled={} loaded={} symbols={} blocked={} universe_date={}",
                        summary.enabled,
                        summary.loaded,
                        summary.symbols,
                        summary.blocked,
                        summary.latest_universe_date.as_deref().unwrap_or("-")
                    );
                }
                Err(error) => {
                    metrics.inc_reference_tradability_refresh_failure();
                    eprintln!("QMD reference tradability refresh failed: {error}");
                }
            }
        }
    });
}

async fn query_reference_tradability(
    config: &GatewayConfig,
) -> Result<Vec<ReferenceTradabilityRow>, String> {
    let sql = format!(
        r#"
        SELECT
            upper(coalesce(nullIf(massive_ticker, ''), ticker)) AS ticker,
            is_tradable,
            toString(universe_date) AS universe_date
        FROM {table} FINAL
        WHERE universe_date = (SELECT max(universe_date) FROM {table})
        FORMAT JSONEachRow
        "#,
        table = config.reference_tradability_table
    );
    let text = clickhouse_query(config, &sql).await?;
    let mut rows = Vec::new();
    for line in text.lines().filter(|line| !line.trim().is_empty()) {
        rows.push(
            serde_json::from_str::<ReferenceTradabilityRow>(line).map_err(|error| {
                format!("could not parse reference tradability row: {error}; row={line}")
            })?,
        );
    }
    Ok(rows)
}

async fn clickhouse_query(config: &GatewayConfig, body: &str) -> Result<String, String> {
    let url = format!(
        "{}/?database={}",
        config.clickhouse_url,
        urlencoding::encode(&config.clickhouse_database)
    );
    let mut request = Client::new()
        .post(url)
        .header("Content-Type", "text/plain; charset=utf-8")
        .header("X-ClickHouse-User", &config.clickhouse_user)
        .body(body.to_string());
    let password = config.clickhouse_password();
    if !password.is_empty() {
        request = request.header("X-ClickHouse-Key", password);
    }
    let response = request.send().await.map_err(|error| error.to_string())?;
    let status = response.status();
    let text = response.text().await.map_err(|error| error.to_string())?;
    if !status.is_success() {
        return Err(format!("ClickHouse HTTP {status}: {text}"));
    }
    Ok(text)
}
