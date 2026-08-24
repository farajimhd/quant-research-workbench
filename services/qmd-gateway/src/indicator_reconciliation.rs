use crate::bars::SharedBarStore;
use crate::bars::TradeAggregationRules;
use crate::compact_event::{CompactEventDecoder, LiveCompactEvent};
use crate::computation_targets::ComputationTargetLease;
use crate::config::GatewayConfig;
use crate::indicators::{IndicatorClickHouseWriter, IndicatorRow, SharedIndicatorStore};
use crate::market_calendar::MarketCalendarClient;
use chrono::Utc;
use chrono_tz::America::New_York;
use futures_util::StreamExt;
use reqwest::Client;
use serde::Serialize;
use std::collections::BTreeSet;
use std::sync::Arc;
use tokio::sync::Mutex;

#[derive(Clone)]
pub struct IndicatorReconciler {
    calendar: MarketCalendarClient,
    client: Client,
    config: GatewayConfig,
    decoder: CompactEventDecoder,
    indicators: SharedIndicatorStore,
    lock: Arc<Mutex<()>>,
    trade_rules: TradeAggregationRules,
    writer: IndicatorClickHouseWriter,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct IndicatorReconciliationSummary {
    pub events_replayed: u64,
    pub rows_persisted: u64,
    pub skipped: bool,
    pub ticker: String,
    pub timeframes: Vec<String>,
}

impl IndicatorReconciler {
    pub fn new(
        config: GatewayConfig,
        calendar: MarketCalendarClient,
        decoder: CompactEventDecoder,
        indicators: SharedIndicatorStore,
        trade_rules: TradeAggregationRules,
        writer: IndicatorClickHouseWriter,
    ) -> Self {
        Self {
            calendar,
            client: Client::new(),
            config,
            decoder,
            indicators,
            lock: Arc::new(Mutex::new(())),
            trade_rules,
            writer,
        }
    }

    pub async fn reconcile_lease(
        &self,
        lease: &ComputationTargetLease,
    ) -> Result<Vec<IndicatorReconciliationSummary>, String> {
        if !self.config.derived_reconciliation_enabled || !self.config.persist_indicators {
            return Ok(Vec::new());
        }
        let _guard = self.lock.lock().await;
        let mut summaries = Vec::new();
        for ticker in &lease.tickers {
            let missing = lease
                .timeframes
                .iter()
                .filter(|timeframe| {
                    // The actual async readiness check is performed below; keep
                    // the normalized lease order deterministic here.
                    !timeframe.trim().is_empty()
                })
                .cloned()
                .collect::<Vec<_>>();
            let mut needed = Vec::new();
            for timeframe in missing {
                if self.indicators.needs_warm(ticker, &timeframe).await {
                    needed.push(timeframe);
                }
            }
            if needed.is_empty() {
                summaries.push(IndicatorReconciliationSummary {
                    skipped: true,
                    ticker: ticker.clone(),
                    timeframes: lease.timeframes.clone(),
                    ..IndicatorReconciliationSummary::default()
                });
                continue;
            }
            summaries.push(self.reconcile_ticker(ticker, &needed).await?);
        }
        Ok(summaries)
    }

    async fn reconcile_ticker(
        &self,
        ticker: &str,
        requested_timeframes: &[String],
    ) -> Result<IndicatorReconciliationSummary, String> {
        let now = Utc::now();
        let today = now.with_timezone(&New_York).date_naive();
        let dates = self.calendar.prior_sessions(
            today,
            self.config
                .recent_live_prior_market_days
                .max(0)
                .saturating_add(1) as usize,
        );
        let Some(first_date) = dates.first().copied() else {
            return Ok(IndicatorReconciliationSummary {
                skipped: true,
                ticker: ticker.to_string(),
                timeframes: requested_timeframes.to_vec(),
                ..IndicatorReconciliationSummary::default()
            });
        };
        let Some((start, _)) = self.calendar.collection_window_utc(first_date, now) else {
            return Err(format!(
                "could not resolve retained indicator window for {ticker}"
            ));
        };
        let replay_timeframes = normalized_replay_timeframes(requested_timeframes);
        let history_limit = self
            .config
            .indicator_history_by_timeframe
            .values()
            .copied()
            .max()
            .unwrap_or(self.config.indicator_history_limit)
            .max(self.config.indicator_history_limit)
            .max(1_000);
        let bars = SharedBarStore::new(
            replay_timeframes,
            history_limit,
            1,
            self.trade_rules.clone(),
        );
        let ticker_sql = escape_sql_string(&ticker.to_ascii_uppercase());
        let sql = format!(
            "SELECT event_date, schema_version, formatDateTime(ingest_ts, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS ingest_ts, arrival_sequence, ticker, event_meta, sip_timestamp_us, price_primary_int, price_secondary_int, size_primary, size_secondary, exchange_primary, exchange_secondary, condition_token_1, condition_token_2, condition_token_3, condition_token_4, condition_token_5, source_sequence, issue_flags FROM {} FINAL WHERE ticker = '{}' AND sip_timestamp_us >= {} AND sip_timestamp_us < {} ORDER BY sip_timestamp_us, source_sequence, bitAnd(event_meta, 1), event_meta, price_primary_int, price_secondary_int, size_primary, size_secondary, exchange_primary, exchange_secondary, condition_token_1, condition_token_2, condition_token_3, condition_token_4, condition_token_5 FORMAT JSONEachRow",
            self.config.compact_event_table,
            ticker_sql,
            start.timestamp_micros().max(0),
            now.timestamp_micros().max(0),
        );
        let mut request = self
            .client
            .post(format!(
                "{}/?database={}",
                self.config.clickhouse_url,
                urlencoding::encode(&self.config.clickhouse_database)
            ))
            .header("Content-Type", "text/plain; charset=utf-8")
            .header("X-ClickHouse-User", &self.config.clickhouse_user)
            .body(sql);
        let password = self.config.clickhouse_password();
        if !password.is_empty() {
            request = request.header("X-ClickHouse-Key", password);
        }
        let response = request.send().await.map_err(|error| error.to_string())?;
        let status = response.status();
        if !status.is_success() {
            return Err(format!(
                "indicator reconciliation ClickHouse HTTP {status}: {}",
                response.text().await.unwrap_or_default()
            ));
        }
        let requested = requested_timeframes
            .iter()
            .map(|value| value.to_ascii_lowercase())
            .collect::<BTreeSet<_>>();
        let mut stream = response.bytes_stream();
        let mut pending = Vec::<u8>::new();
        let mut output = Vec::<IndicatorRow>::with_capacity(self.config.max_clickhouse_batch);
        let mut events_replayed = 0_u64;
        let mut rows_persisted = 0_u64;
        while let Some(chunk) = stream.next().await {
            pending.extend_from_slice(&chunk.map_err(|error| error.to_string())?);
            while let Some(index) = pending.iter().position(|byte| *byte == b'\n') {
                let line = pending.drain(..=index).collect::<Vec<_>>();
                let line = &line[..line.len().saturating_sub(1)];
                if line.is_empty() {
                    continue;
                }
                let compact: LiveCompactEvent =
                    serde_json::from_slice(line).map_err(|error| error.to_string())?;
                let event = self.decoder.decode(&compact);
                self.indicators.apply_reconciliation_event(&event).await;
                for bar in bars.apply_event(&event).await {
                    let timeframe = bar.timeframe.to_ascii_lowercase();
                    let row = self.indicators.apply_reconciliation_bar(bar).await;
                    if requested.contains(&timeframe) && durable_indicator_row(&row) {
                        output.push(row);
                    }
                }
                events_replayed = events_replayed.saturating_add(1);
                if output.len() >= self.config.max_clickhouse_batch {
                    self.writer.persist_reconciled_rows(&output).await?;
                    rows_persisted = rows_persisted.saturating_add(output.len() as u64);
                    output.clear();
                }
            }
        }
        if !pending.is_empty() {
            let compact: LiveCompactEvent =
                serde_json::from_slice(&pending).map_err(|error| error.to_string())?;
            let event = self.decoder.decode(&compact);
            self.indicators.apply_reconciliation_event(&event).await;
            for bar in bars.apply_event(&event).await {
                let timeframe = bar.timeframe.to_ascii_lowercase();
                let row = self.indicators.apply_reconciliation_bar(bar).await;
                if requested.contains(&timeframe) && durable_indicator_row(&row) {
                    output.push(row);
                }
            }
            events_replayed = events_replayed.saturating_add(1);
        }
        for bar in bars.finalize_due(now).await {
            let timeframe = bar.timeframe.to_ascii_lowercase();
            let row = self.indicators.apply_reconciliation_bar(bar).await;
            if requested.contains(&timeframe) && durable_indicator_row(&row) {
                output.push(row);
            }
        }
        if !output.is_empty() {
            self.writer.persist_reconciled_rows(&output).await?;
            rows_persisted = rows_persisted.saturating_add(output.len() as u64);
        }
        self.record_coverage(
            ticker,
            requested_timeframes,
            first_date,
            events_replayed,
            rows_persisted,
        )
        .await?;
        Ok(IndicatorReconciliationSummary {
            events_replayed,
            rows_persisted,
            skipped: false,
            ticker: ticker.to_string(),
            timeframes: requested_timeframes.to_vec(),
        })
    }

    async fn record_coverage(
        &self,
        ticker: &str,
        timeframes: &[String],
        local_date: chrono::NaiveDate,
        source_rows: u64,
        output_rows: u64,
    ) -> Result<(), String> {
        let scope = format!("{}:{}", ticker, timeframes.join(","));
        let sql = format!(
            "INSERT INTO {} (product, local_date, scope_id, calculation_revision, source_revision, source_row_count, output_row_count, status, detail, updated_at_utc) VALUES ('indicators', toDate('{}'), '{}', '{}', '{}', {}, {}, 'complete', '{}', now64(3))",
            self.config.derived_coverage_table,
            local_date,
            escape_sql_string(&scope),
            crate::indicators::INDICATOR_CALCULATION_REVISION,
            escape_sql_string(&self.config.qmd_run_id),
            source_rows,
            output_rows,
            escape_sql_string(&format!("retained_sessions={}", self.config.recent_live_prior_market_days.saturating_add(1))),
        );
        let mut request = self
            .client
            .post(format!(
                "{}/?database={}",
                self.config.clickhouse_url,
                urlencoding::encode(&self.config.clickhouse_database)
            ))
            .header("Content-Type", "text/plain; charset=utf-8")
            .header("X-ClickHouse-User", &self.config.clickhouse_user)
            .body(sql);
        let password = self.config.clickhouse_password();
        if !password.is_empty() {
            request = request.header("X-ClickHouse-Key", password);
        }
        let response = request.send().await.map_err(|error| error.to_string())?;
        let status = response.status();
        let text = response.text().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!("derived indicator coverage HTTP {status}: {text}"));
        }
        Ok(())
    }
}

fn escape_sql_string(value: &str) -> String {
    value.replace('\\', "\\\\").replace('\'', "\\'")
}

fn normalized_replay_timeframes(requested: &[String]) -> Vec<String> {
    let mut timeframes = requested
        .iter()
        .map(|value| value.trim().to_ascii_lowercase())
        .filter(|value| !value.is_empty())
        .collect::<BTreeSet<_>>();
    // Every scoped replay needs the canonical microstructure base even when
    // the consumer requests only a parent timeframe.
    timeframes.insert("100ms".to_string());
    timeframes.into_iter().collect()
}

fn durable_indicator_row(row: &IndicatorRow) -> bool {
    row.close.is_finite() && row.close > 0.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scoped_indicator_replay_always_includes_one_canonical_base() {
        assert_eq!(
            normalized_replay_timeframes(&[
                "1m".to_string(),
                "100MS".to_string(),
                "1m".to_string(),
            ]),
            vec!["100ms".to_string(), "1m".to_string()]
        );
    }
}
