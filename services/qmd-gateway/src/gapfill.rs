use crate::compact_event::SharedCompactEventStore;
use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::flatfile::FlatfileDiscovery;
use crate::maintenance::SharedMaintenanceState;
use crate::market_calendar::MarketCalendarClient;
use crate::massive::{fanout_market_event, MarketEventFanout};
use crate::metrics::TimingTarget;
use crate::session::{is_streaming_phase, session_phase};
use crate::timefmt::clickhouse_datetime64;
use chrono::{
    DateTime, Datelike, Duration as ChronoDuration, NaiveDate, NaiveDateTime, TimeZone, Timelike,
    Utc,
};
use chrono_tz::America::New_York;
use futures_util::stream::{self, StreamExt};
use reqwest::Client;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::process::Command;
use tokio::time::{sleep, Duration};

pub async fn run_startup_maintenance(
    config: GatewayConfig,
    fanout: MarketEventFanout,
    maintenance: SharedMaintenanceState,
    live_compact_store: SharedCompactEventStore,
    calendar: MarketCalendarClient,
) {
    if !config.gap_fill_enabled || !config.qmd_startup_maintenance_enabled {
        return;
    }
    let filler = GapFillService::new(config, fanout, maintenance, live_compact_store, calendar);
    eprintln!("QMD startup maintenance: checking recent q_live event coverage.");
    if let Err(error) = filler.run_startup_maintenance().await {
        filler.fanout.metrics.inc_gap_fill_failure();
        filler
            .maintenance
            .finish("failed", &format!("Startup maintenance failed: {error}"))
            .await;
        eprintln!("QMD startup maintenance failed: {error}");
    }
}

pub async fn run_gap_fill_service(
    config: GatewayConfig,
    fanout: MarketEventFanout,
    maintenance: SharedMaintenanceState,
    live_compact_store: SharedCompactEventStore,
    calendar: MarketCalendarClient,
) {
    if !config.gap_fill_enabled {
        return;
    }
    let filler = GapFillService::new(config, fanout, maintenance, live_compact_store, calendar);
    let mut delay_ms = filler.next_gap_fill_delay_ms("startup");
    loop {
        sleep(Duration::from_millis(delay_ms)).await;
        let mode = if is_streaming_phase(Utc::now()) {
            if !should_run_session_catch_up(filler.config.gap_fill_mode.as_str()) {
                delay_ms = filler.next_gap_fill_delay_ms("skipped");
                continue;
            }
            "session_catch_up"
        } else {
            if !matches!(
                filler.config.gap_fill_mode.as_str(),
                "auto" | "after_hours" | "repair"
            ) {
                delay_ms = filler.next_gap_fill_delay_ms("skipped");
                continue;
            }
            "after_hours_repair"
        };
        if filler.maintenance.snapshot().await.active {
            delay_ms = filler.next_gap_fill_delay_ms("maintenance_active");
            continue;
        }
        if let Err(error) = filler.run_once(mode).await {
            filler.fanout.metrics.inc_gap_fill_failure();
            filler
                .maintenance
                .finish("failed", &format!("Gap fill cycle failed: {error}"))
                .await;
            eprintln!("Gap fill cycle failed: {error}");
            delay_ms = filler.next_gap_fill_delay_ms("failed");
            continue;
        }
        let status = filler.maintenance.snapshot().await.status;
        delay_ms = filler.next_gap_fill_delay_ms(&status);
    }
}

#[derive(Default)]
struct LiveEventAudit {
    duplicate_event_rows: u64,
    recent_rows: u64,
    ticker_count: u64,
}

#[derive(Default)]
struct RecentLiveRepair {
    errors: u64,
    intervals_checked: u64,
    intervals_repaired: u64,
    page_limited_symbols: u64,
    rows_written: u64,
    status: String,
    symbols_checked: u64,
    symbols_repaired: u64,
}

#[derive(Clone, Debug)]
struct CoverageInterval {
    end: DateTime<Utc>,
    start: DateTime<Utc>,
}

#[derive(Clone, Debug)]
struct CoverageRow {
    coverage_id: String,
    end: DateTime<Utc>,
    start: DateTime<Utc>,
    status: String,
}

#[derive(Clone, Debug)]
struct FlatfileCoverageState {
    historical_rows: u64,
    historical_status: String,
    remote_content_length: u64,
    remote_etag: String,
    remote_key: String,
    remote_last_modified: String,
    updated_at_utc: DateTime<Utc>,
}

#[derive(Clone, Debug)]
struct RepairInterval {
    end: DateTime<Utc>,
    reason: &'static str,
    start: DateTime<Utc>,
}

#[derive(Default)]
struct SymbolRepairOutcome {
    errors: u64,
    interval_records: Vec<IntervalRepairRecord>,
    intervals_attempted: u64,
    page_limit_hit: bool,
    rows: u64,
    symbol_repaired: bool,
}

#[derive(Clone, Debug, Default)]
struct IntervalRepairRecord {
    errors: u64,
    index: usize,
    page_limit_hit: bool,
    rows: u64,
    symbol_had_rows: bool,
}

#[derive(Clone, Debug, Default)]
struct IntervalRepairStats {
    bar_rows_after: u64,
    bar_rows_before: u64,
    errors: u64,
    page_limit_symbols: u64,
    rows: u64,
    symbols_attempted: u64,
    symbols_with_rows: u64,
}

#[derive(Default)]
struct IntervalFillOutcome {
    page_limit_hit: bool,
    rows: u64,
}

#[derive(Default)]
struct FetchEventsOutcome {
    events: Vec<MarketEvent>,
    page_limit_hit: bool,
}

#[derive(Clone)]
struct GapFillService {
    client: Client,
    config: GatewayConfig,
    fanout: MarketEventFanout,
    live_compact_store: SharedCompactEventStore,
    maintenance: SharedMaintenanceState,
    calendar: MarketCalendarClient,
    flatfiles: FlatfileDiscovery,
}

impl GapFillService {
    fn new(
        config: GatewayConfig,
        fanout: MarketEventFanout,
        maintenance: SharedMaintenanceState,
        live_compact_store: SharedCompactEventStore,
        calendar: MarketCalendarClient,
    ) -> Self {
        let flatfiles = FlatfileDiscovery::new(config.clone());
        Self {
            client: Client::new(),
            config,
            fanout,
            live_compact_store,
            maintenance,
            calendar,
            flatfiles,
        }
    }

    async fn run_startup_maintenance(&self) -> Result<(), String> {
        self.calendar.refresh(Utc::now()).await;
        self.initialize_tables().await?;
        let started_at = Utc::now();
        let phase = format!("{:?}", session_phase(started_at));
        let host_role = self.host_role();
        let (window_start, window_end, _) = self.recent_live_window();
        self.maintenance
            .start(
                "startup_maintenance",
                "startup_recent_repair",
                "Auditing q_live event structure and coverage before websocket ingest.",
                Some(window_start),
                Some(window_end),
            )
            .await;
        let audit = self.audit_recent_live_events().await?;
        eprintln!(
            "QMD startup q_live audit: rows={} tickers={} duplicate_canonical_events={}",
            audit.recent_rows, audit.ticker_count, audit.duplicate_event_rows,
        );
        let mut status = if audit.duplicate_event_rows == 0 {
            "ok"
        } else {
            "needs_manual_rebuild"
        };
        let mut rows_written = 0u64;
        let mut message = String::new();
        let mut repair = RecentLiveRepair::default();
        if status == "ok" {
            repair = self
                .repair_recent_live_coverage(started_at, "startup_recent_repair")
                .await
                .unwrap_or_else(|error| {
                    message = error;
                    RecentLiveRepair {
                        errors: 1,
                        status: "repair_failed".to_string(),
                        ..RecentLiveRepair::default()
                    }
                });
            rows_written = repair.rows_written;
            if !message.is_empty() {
                status = "repair_failed";
            } else if !repair.status.is_empty() {
                status = repair.status.as_str();
            }
        } else {
            message = "Recent q_live event table has duplicate canonical event identities after FINAL; a validated rebuild is required.".to_string();
        }
        self.record_coverage_run(
            started_at,
            "q_live_recent_events",
            status,
            window_start,
            window_end,
            "startup_maintenance",
            rows_written,
            &host_role,
            "",
            &json!({
                "phase": phase,
                "recent_rows": audit.recent_rows,
                "ticker_count": audit.ticker_count,
                "duplicate_event_rows": audit.duplicate_event_rows,
                "message": message,
                "symbols_checked": repair.symbols_checked,
                "symbols_repaired": repair.symbols_repaired,
                "intervals_checked": repair.intervals_checked,
                "intervals_repaired": repair.intervals_repaired,
                "page_limited_symbols": repair.page_limited_symbols,
                "repair_errors": repair.errors,
            }),
        )
        .await?;
        if self.config.historical_flatfile_update_enabled {
            self.maintenance
                .set_message(
                    "running",
                    "Recent q_live maintenance finished; checking historical flatfile coverage.",
                )
                .await;
            self.plan_historical_flatfile_update(started_at, "startup_historical_check", true)
                .await?;
        }
        self.enforce_live_retention(started_at).await?;
        self.maintenance
            .finish(
                status,
                &format!(
                    "Startup maintenance completed: status={} rows={} symbols={}/{} intervals={}/{}",
                    status,
                    repair.rows_written,
                    repair.symbols_repaired,
                    repair.symbols_checked,
                    repair.intervals_repaired,
                    repair.intervals_checked,
                ),
            )
            .await;
        Ok(())
    }

    fn next_gap_fill_delay_ms(&self, status: &str) -> u64 {
        if is_streaming_phase(Utc::now())
            && matches!(
                status,
                "startup" | "awaiting_live_symbols" | "maintenance_active" | "failed"
            )
        {
            return self.config.gap_fill_awaiting_symbols_retry_ms.max(1_000);
        }
        self.config.gap_fill_interval_ms.max(1_000)
    }

    async fn run_once(&self, mode: &str) -> Result<u64, String> {
        self.calendar.refresh(Utc::now()).await;
        let _timing = self.fanout.metrics.timing(TimingTarget::GapFillRun);
        self.fanout.metrics.inc_gap_fill_run();
        self.initialize_tables().await?;
        let started_at = Utc::now();
        let phase = format!("{:?}", session_phase(started_at));
        let host_role = self.host_role();
        let (window_start, window_end, _) = self.recent_live_window();
        self.maintenance
            .start(
                "scheduled_gap_fill",
                mode,
                "Checking q_live recent coverage gaps.",
                Some(window_start),
                Some(window_end),
            )
            .await;
        if self.config.massive_api_key.is_empty() {
            self.record_run(
                started_at,
                mode,
                &phase,
                "",
                "skipped",
                0,
                "MASSIVE_API_KEY is not configured",
            )
            .await?;
            self.record_coverage_run(
                started_at,
                "q_live_recent_events",
                "skipped",
                window_start,
                window_end,
                mode,
                0,
                &host_role,
                "",
                &json!({
                    "phase": phase,
                    "message": "MASSIVE_API_KEY is not configured",
                }),
            )
            .await?;
            self.maintenance
                .finish("skipped", "MASSIVE_API_KEY is not configured")
                .await;
            return Ok(0);
        }
        let repair = self.repair_recent_live_coverage(started_at, mode).await?;
        self.record_coverage_run(
            started_at,
            "q_live_recent_events",
            &repair.status,
            window_start,
            window_end,
            mode,
            repair.rows_written,
            &host_role,
            "",
            &json!({
                "phase": phase,
                "symbols_checked": repair.symbols_checked,
                "symbols_repaired": repair.symbols_repaired,
                "intervals_checked": repair.intervals_checked,
                "intervals_repaired": repair.intervals_repaired,
                "page_limited_symbols": repair.page_limited_symbols,
                "repair_errors": repair.errors,
            }),
        )
        .await?;
        if self.config.historical_flatfile_update_enabled {
            self.maintenance
                .set_message(
                    "running",
                    "Recent q_live repair finished; checking historical flatfile coverage.",
                )
                .await;
            self.plan_historical_flatfile_update(started_at, mode, false)
                .await?;
        }
        self.enforce_live_retention(started_at).await?;
        self.maintenance
            .finish(
                &repair.status,
                &format!(
                    "Gap fill completed: status={} rows={} symbols={}/{} intervals={}/{}",
                    repair.status,
                    repair.rows_written,
                    repair.symbols_repaired,
                    repair.symbols_checked,
                    repair.intervals_repaired,
                    repair.intervals_checked,
                ),
            )
            .await;
        Ok(repair.rows_written)
    }

    async fn fill_symbol_interval(
        &self,
        symbol: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> Result<IntervalFillOutcome, String> {
        let trades = self.fetch_events(symbol, "trades", start, end).await?;
        let quotes = self.fetch_events(symbol, "quotes", start, end).await?;
        let mut events = Vec::new();
        events.extend(trades.events);
        events.extend(quotes.events);
        events.sort_by_key(|event| {
            let tie_breaker = match event {
                MarketEvent::Quote(_) => 0u8,
                MarketEvent::Trade(_) => 1u8,
            };
            (event.ts(), tie_breaker)
        });

        let mut count = 0u64;
        for event in events {
            fanout_market_event(event, &self.fanout).await;
            count += 1;
        }
        Ok(IntervalFillOutcome {
            page_limit_hit: trades.page_limit_hit || quotes.page_limit_hit,
            rows: count,
        })
    }

    async fn fetch_events(
        &self,
        symbol: &str,
        kind: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> Result<FetchEventsOutcome, String> {
        let mut next_url = Some(self.rest_url(symbol, kind, start, end));
        let mut pages = 0usize;
        let mut out = Vec::new();
        let mut page_limit_hit = false;
        while let Some(url) = next_url.take() {
            if pages >= self.config.recent_live_max_pages_per_interval {
                page_limit_hit = true;
                break;
            }
            pages += 1;
            let payload: Value = self
                .client
                .get(url)
                .send()
                .await
                .map_err(|error| redact_sensitive(&error.to_string()))?
                .json()
                .await
                .map_err(|error| redact_sensitive(&error.to_string()))?;
            let rows = payload
                .get("results")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            if rows.is_empty() {
                break;
            }
            for row in rows {
                let event = if kind == "trades" {
                    rest_trade_event(symbol, row)
                } else {
                    rest_quote_event(symbol, row)
                };
                if let Some(event) = event {
                    out.push(event);
                }
            }
            next_url = payload
                .get("next_url")
                .and_then(Value::as_str)
                .map(|url| append_api_key(url, &self.config.massive_api_key));
        }
        Ok(FetchEventsOutcome {
            events: out,
            page_limit_hit,
        })
    }

    async fn repair_recent_live_coverage(
        &self,
        started_at: DateTime<Utc>,
        mode: &str,
    ) -> Result<RecentLiveRepair, String> {
        let phase = format!("{:?}", session_phase(started_at));
        let (window_start, window_end, market_dates) = self.recent_live_window();
        let coverage_intervals = self
            .live_event_coverage_intervals(window_start, window_end)
            .await?;
        let coverage_gaps = self.coverage_gaps_for_sessions(&market_dates, &coverage_intervals);
        let symbols = self.recent_live_symbols(window_start).await?;
        let mut repair = RecentLiveRepair {
            status: "up_to_date".to_string(),
            symbols_checked: symbols.len() as u64,
            ..RecentLiveRepair::default()
        };
        repair.intervals_checked = coverage_gaps.len() as u64;
        if coverage_gaps.is_empty() {
            self.maintenance
                .configure_totals(symbols.len() as u64, 0)
                .await;
            self.maintenance
                .set_message("up_to_date", "No q_live recent coverage gaps were found.")
                .await;
            return Ok(repair);
        }
        if symbols.is_empty() {
            repair.status = if is_streaming_phase(Utc::now()) {
                "awaiting_live_symbols".to_string()
            } else {
                "no_symbols_available".to_string()
            };
            self.maintenance
                .configure_totals(0, coverage_gaps.len() as u64)
                .await;
            self.maintenance
                .set_message(
                    &repair.status,
                    if is_streaming_phase(Utc::now()) {
                        "Coverage gaps exist. QMD is waiting for websocket compact events to discover tickers for REST repair."
                    } else {
                        "Coverage gaps exist, but no symbols were found in q_live or the latest historical compact event day."
                    },
                )
                .await;
            self.record_run(
                started_at,
                mode,
                &phase,
                "",
                &repair.status,
                0,
                if is_streaming_phase(Utc::now()) {
                    "Recent q_live gaps exist; waiting for websocket-discovered tickers before REST repair."
                } else {
                    "Recent q_live gaps exist, but no symbols were found in q_live or latest historical compact events."
                },
            )
            .await?;
            return Ok(repair);
        }
        self.maintenance
            .configure_totals(symbols.len() as u64, coverage_gaps.len() as u64)
            .await;
        self.maintenance
            .set_message(
                "running",
                &format!(
                    "Repairing {} q_live gap interval(s) across {} symbol(s).",
                    coverage_gaps.len(),
                    symbols.len()
                ),
            )
            .await;
        let mut interval_stats = Vec::with_capacity(coverage_gaps.len());
        for interval in &coverage_gaps {
            interval_stats.push(IntervalRepairStats {
                bar_rows_before: self
                    .count_bar_rows(interval.start, interval.end)
                    .await
                    .unwrap_or(0),
                ..IntervalRepairStats::default()
            });
        }
        let repair_jobs = symbols
            .into_iter()
            .map(|symbol| (symbol, coverage_gaps.clone()))
            .collect::<Vec<_>>();
        let concurrency = self.config.recent_live_repair_concurrency.max(1);
        let mut outcomes = stream::iter(repair_jobs)
            .map(|(symbol, intervals)| {
                let phase = phase.clone();
                async move {
                    let outcome = self
                        .repair_symbol_intervals(
                            started_at,
                            mode,
                            &phase,
                            symbol.clone(),
                            intervals,
                        )
                        .await;
                    (symbol, outcome)
                }
            })
            .buffer_unordered(concurrency);
        while let Some((symbol, outcome)) = outcomes.next().await {
            match outcome {
                Ok(outcome) => {
                    repair.rows_written += outcome.rows;
                    repair.intervals_repaired += outcome.intervals_attempted;
                    repair.errors += outcome.errors;
                    if outcome.symbol_repaired {
                        repair.symbols_repaired += 1;
                    }
                    if outcome.page_limit_hit {
                        repair.page_limited_symbols += 1;
                    }
                    for record in outcome.interval_records {
                        if let Some(stats) = interval_stats.get_mut(record.index) {
                            stats.rows += record.rows;
                            stats.errors += record.errors;
                            stats.symbols_attempted += 1;
                            if record.symbol_had_rows {
                                stats.symbols_with_rows += 1;
                            }
                            if record.page_limit_hit {
                                stats.page_limit_symbols += 1;
                            }
                        }
                    }
                }
                Err(error) => {
                    repair.errors += 1;
                    let error = redact_sensitive(&error);
                    eprintln!("QMD recent q_live repair failed for {symbol}: {error}");
                    if is_clickhouse_memory_limit(&error) {
                        return Err(format!(
                            "recent q_live repair stopped after ClickHouse memory exhaustion: {error}"
                        ));
                    }
                }
            }
        }
        repair.status = if repair.errors > 0 {
            "partial_failed".to_string()
        } else if repair.page_limited_symbols > 0 {
            "partial_page_limit".to_string()
        } else if repair.intervals_repaired > 0 {
            let wait_ms = self.config.flush_interval_ms.saturating_mul(2).max(1_000);
            tokio::time::sleep(Duration::from_millis(wait_ms)).await;
            for (index, interval) in coverage_gaps.iter().enumerate() {
                if let Some(stats) = interval_stats.get_mut(index) {
                    stats.bar_rows_after = self
                        .count_bar_rows(interval.start, interval.end)
                        .await
                        .unwrap_or(stats.bar_rows_before);
                }
            }
            self.record_completed_live_repair_coverage(
                started_at,
                mode,
                &coverage_gaps,
                &interval_stats,
            )
            .await?;
            if interval_stats.iter().any(|stats| {
                stats.errors > 0
                    || (stats.rows > 0 && stats.bar_rows_after <= stats.bar_rows_before)
            }) {
                "partial_failed".to_string()
            } else {
                "repair_completed".to_string()
            }
        } else {
            "up_to_date".to_string()
        };
        Ok(repair)
    }

    async fn repair_symbol_intervals(
        &self,
        started_at: DateTime<Utc>,
        mode: &str,
        phase: &str,
        symbol: String,
        intervals: Vec<RepairInterval>,
    ) -> Result<SymbolRepairOutcome, String> {
        let window_start = intervals.iter().map(|interval| interval.start).min();
        let window_end = intervals.iter().map(|interval| interval.end).max();
        self.update_gap_fill_symbol_status(
            &symbol,
            "in_progress",
            mode,
            Some(started_at),
            None,
            window_start,
            window_end,
            0,
            0,
            json!({
                "phase": phase,
                "interval_count": intervals.len(),
                "message": "qmd REST gap fill started for symbol",
            }),
        )
        .await?;
        let mut symbol_rows = 0u64;
        let mut symbol_errors = 0u64;
        let mut symbol_partial = false;
        let mut intervals_attempted = 0u64;
        let mut interval_records = Vec::with_capacity(intervals.len());
        for (index, interval) in intervals.into_iter().enumerate() {
            self.maintenance
                .start_interval(&symbol, interval.reason, interval.start, interval.end)
                .await;
            eprintln!(
                "QMD recent q_live repair: symbol={} reason={} start={} end={}",
                symbol,
                interval.reason,
                interval.start.to_rfc3339(),
                interval.end.to_rfc3339()
            );
            match self
                .fill_symbol_interval(&symbol, interval.start, interval.end)
                .await
            {
                Ok(outcome) => {
                    symbol_rows += outcome.rows;
                    intervals_attempted += 1;
                    self.fanout.metrics.inc_gap_fill_rows(outcome.rows);
                    self.maintenance
                        .complete_interval(outcome.rows, false, outcome.page_limit_hit)
                        .await;
                    if outcome.page_limit_hit {
                        symbol_partial = true;
                    }
                    interval_records.push(IntervalRepairRecord {
                        errors: 0,
                        index,
                        page_limit_hit: outcome.page_limit_hit,
                        rows: outcome.rows,
                        symbol_had_rows: outcome.rows > 0,
                    });
                }
                Err(error) => {
                    let error = redact_sensitive(&error);
                    symbol_errors += 1;
                    self.maintenance.complete_interval(0, true, false).await;
                    eprintln!("QMD recent q_live repair failed for {symbol}: {error}");
                    interval_records.push(IntervalRepairRecord {
                        errors: 1,
                        index,
                        page_limit_hit: false,
                        rows: 0,
                        symbol_had_rows: false,
                    });
                    if is_clickhouse_memory_limit(&error) {
                        return Err(format!(
                            "symbol repair stopped after ClickHouse memory exhaustion: {error}"
                        ));
                    }
                }
            }
        }
        let status = if symbol_errors > 0 {
            "failed"
        } else if symbol_partial {
            "partial_page_limit"
        } else {
            "completed"
        };
        self.record_run(
            started_at,
            mode,
            phase,
            &symbol,
            status,
            symbol_rows,
            if symbol_partial {
                "Massive REST page limit was reached for at least one interval"
            } else if symbol_errors > 0 {
                "At least one interval failed for this symbol"
            } else {
                ""
            },
        )
        .await?;
        self.update_gap_fill_symbol_status(
            &symbol,
            status,
            mode,
            Some(started_at),
            Some(Utc::now()),
            window_start,
            window_end,
            symbol_rows,
            symbol_errors,
            json!({
                "phase": phase,
                "intervals_attempted": intervals_attempted,
                "page_limit_hit": symbol_partial,
                "message": if symbol_partial {
                    "Massive REST page limit was reached for at least one interval"
                } else if symbol_errors > 0 {
                    "At least one interval failed for this symbol"
                } else {
                    "qmd REST gap fill completed for symbol"
                },
            }),
        )
        .await?;
        self.maintenance.complete_symbol(&symbol).await;
        Ok(SymbolRepairOutcome {
            errors: symbol_errors,
            interval_records,
            intervals_attempted,
            page_limit_hit: symbol_partial,
            rows: symbol_rows,
            symbol_repaired: intervals_attempted > 0,
        })
    }

    async fn live_event_coverage_intervals(
        &self,
        window_start: DateTime<Utc>,
        window_end: DateTime<Utc>,
    ) -> Result<Vec<CoverageInterval>, String> {
        let sql = format!(
            r#"
            SELECT
                coverage_id,
                status,
                coverage_start_utc,
                coverage_end_utc
            FROM {table} FINAL
            WHERE coverage_kind = 'q_live_events'
              AND status IN ('repair_completed', 'coverage_bootstrap', 'compact_persisted', 'bars_persisted')
              AND coverage_end_utc > toDateTime64('{start}', 3, 'UTC')
              AND coverage_start_utc < toDateTime64('{end}', 3, 'UTC')
            ORDER BY coverage_start_utc, coverage_end_utc
            FORMAT JSONEachRow
            "#,
            table = self.config.qmd_live_event_coverage_table,
            start = clickhouse_datetime64(&window_start),
            end = clickhouse_datetime64(&window_end),
        );
        let text = self.query(&sql, true).await?;
        let mut rows = Vec::new();
        for line in text.lines().filter(|line| !line.trim().is_empty()) {
            let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
            let coverage_id = value
                .get("coverage_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let status = value
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let Some(start) = value
                .get("coverage_start_utc")
                .and_then(Value::as_str)
                .and_then(parse_clickhouse_datetime64)
            else {
                continue;
            };
            let Some(end) = value
                .get("coverage_end_utc")
                .and_then(Value::as_str)
                .and_then(parse_clickhouse_datetime64)
            else {
                continue;
            };
            if end <= start {
                continue;
            }
            rows.push(CoverageRow {
                coverage_id,
                end,
                start,
                status,
            });
        }
        let out = materialize_confirmed_live_coverage(&rows);
        Ok(out)
    }

    fn coverage_gaps_for_sessions(
        &self,
        market_dates: &[NaiveDate],
        intervals: &[CoverageInterval],
    ) -> Vec<RepairInterval> {
        let now = Utc::now();
        let min_gap = ChronoDuration::seconds(self.config.gap_fill_min_gap_seconds.max(1));
        let mut gaps = Vec::new();
        for date in market_dates {
            let Some((session_start, session_end)) =
                self.calendar.collection_window_utc(*date, now)
            else {
                continue;
            };
            if session_end <= session_start {
                continue;
            }
            let mut clipped = intervals
                .iter()
                .filter_map(|interval| {
                    let start = interval.start.max(session_start);
                    let end = interval.end.min(session_end);
                    if end > start {
                        Some((start, end))
                    } else {
                        None
                    }
                })
                .collect::<Vec<_>>();
            clipped.sort_by_key(|(start, end)| (*start, *end));
            let mut cursor = session_start;
            for (start, end) in clipped {
                if start > cursor {
                    let gap = start - cursor;
                    if gap >= min_gap {
                        gaps.push(RepairInterval {
                            start: cursor,
                            end: start,
                            reason: "coverage_gap",
                        });
                    }
                }
                if end > cursor {
                    cursor = end;
                }
            }
            if session_end > cursor {
                let gap = session_end - cursor;
                if gap >= min_gap {
                    gaps.push(RepairInterval {
                        start: cursor,
                        end: session_end,
                        reason: "coverage_gap",
                    });
                }
            }
        }
        gaps
    }

    async fn recent_live_symbols(
        &self,
        window_start: DateTime<Utc>,
    ) -> Result<Vec<String>, String> {
        self.bootstrap_gap_fill_symbol_universe_if_empty().await?;
        if is_streaming_phase(Utc::now()) {
            let live_symbols = self.live_compact_symbols().await;
            if !live_symbols.is_empty() {
                self.insert_missing_gap_fill_symbols(&live_symbols, "websocket")
                    .await?;
            }
            let mut symbols = BTreeSet::new();
            symbols.extend(self.gap_fill_universe_symbols().await?);
            if self.config.compact_events_enabled {
                symbols.extend(self.recent_q_live_symbols(window_start).await?);
            }
            return Ok(symbols.into_iter().collect());
        }
        let mut symbols = BTreeSet::new();
        symbols.extend(self.gap_fill_universe_symbols().await?);
        if !symbols.is_empty() {
            return Ok(symbols.into_iter().collect());
        }
        if self.config.compact_events_enabled {
            symbols.extend(self.recent_q_live_symbols(window_start).await?);
        }
        if !symbols.is_empty() {
            return Ok(symbols.into_iter().collect());
        }
        symbols.extend(self.latest_q_live_symbols().await?);
        if !symbols.is_empty() {
            return Ok(symbols.into_iter().collect());
        }
        symbols.extend(self.latest_historical_symbols().await?);
        Ok(symbols.into_iter().collect())
    }

    async fn bootstrap_gap_fill_symbol_universe_if_empty(&self) -> Result<(), String> {
        let sql = format!(
            "SELECT count() FROM {} FINAL FORMAT TSV",
            self.config.qmd_gap_fill_symbol_universe_table
        );
        let count = self
            .query(&sql, true)
            .await?
            .trim()
            .parse::<u64>()
            .unwrap_or(0);
        if count > 0 {
            return Ok(());
        }
        let symbols = self
            .latest_historical_symbols_for_market_days(
                self.config.qmd_gap_fill_universe_market_days,
            )
            .await?;
        if symbols.is_empty() {
            return Ok(());
        }
        self.insert_missing_gap_fill_symbols(&symbols, "historical_flatfile_recent")
            .await
    }

    async fn gap_fill_universe_symbols(&self) -> Result<Vec<String>, String> {
        let sql = format!(
            r#"
            SELECT symbol
            FROM {table} FINAL
            WHERE symbol != ''
            ORDER BY symbol
            FORMAT TSV
            "#,
            table = self.config.qmd_gap_fill_symbol_universe_table
        );
        self.symbols_from_sql(&sql).await
    }

    async fn insert_missing_gap_fill_symbols(
        &self,
        symbols: &[String],
        source: &str,
    ) -> Result<(), String> {
        let existing = self
            .gap_fill_universe_symbols()
            .await?
            .into_iter()
            .collect::<BTreeSet<_>>();
        let mut missing = symbols
            .iter()
            .map(|symbol| symbol.to_ascii_uppercase())
            .filter(|symbol| !symbol.is_empty() && !existing.contains(symbol))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        missing.sort();
        if missing.is_empty() {
            return Ok(());
        }
        for chunk in missing.chunks(1_000) {
            let now = Utc::now();
            let rows = chunk
                .iter()
                .map(|symbol| {
                    json!({
                        "symbol": symbol,
                        "status": "not_gap_filled",
                        "source": source,
                        "observed_at_utc": clickhouse_datetime64(&now),
                        "updated_at_utc": clickhouse_datetime64(&now),
                        "last_gap_fill_started_at_utc": null,
                        "last_gap_fill_completed_at_utc": null,
                        "last_window_start_utc": null,
                        "last_window_end_utc": null,
                        "rows_written": 0,
                        "error_count": 0,
                        "metadata_json": json!({
                            "universe_market_days": self.config.qmd_gap_fill_universe_market_days,
                            "message": "symbol discovered for qmd REST gap fill universe",
                        }).to_string(),
                    })
                    .to_string()
                })
                .collect::<Vec<_>>()
                .join("\n");
            self.query(
                &format!(
                    "INSERT INTO {} FORMAT JSONEachRow\n{}",
                    self.config.qmd_gap_fill_symbol_universe_table, rows
                ),
                true,
            )
            .await
            .map(|_| ())?;
        }
        Ok(())
    }

    async fn update_gap_fill_symbol_status(
        &self,
        symbol: &str,
        status: &str,
        source: &str,
        started_at: Option<DateTime<Utc>>,
        completed_at: Option<DateTime<Utc>>,
        window_start: Option<DateTime<Utc>>,
        window_end: Option<DateTime<Utc>>,
        rows_written: u64,
        error_count: u64,
        metadata: Value,
    ) -> Result<(), String> {
        let now = Utc::now();
        let row = json!({
            "symbol": symbol.to_ascii_uppercase(),
            "status": status,
            "source": source,
            "observed_at_utc": clickhouse_datetime64(&now),
            "updated_at_utc": clickhouse_datetime64(&now),
            "last_gap_fill_started_at_utc": started_at.map(|value| clickhouse_datetime64(&value)),
            "last_gap_fill_completed_at_utc": completed_at.map(|value| clickhouse_datetime64(&value)),
            "last_window_start_utc": window_start.map(|value| clickhouse_datetime64(&value)),
            "last_window_end_utc": window_end.map(|value| clickhouse_datetime64(&value)),
            "rows_written": rows_written,
            "error_count": error_count,
            "metadata_json": metadata.to_string(),
        });
        self.query(
            &format!(
                "INSERT INTO {} FORMAT JSONEachRow\n{}",
                self.config.qmd_gap_fill_symbol_universe_table, row
            ),
            true,
        )
        .await
        .map(|_| ())
    }

    async fn live_compact_symbols(&self) -> Vec<String> {
        self.live_compact_store
            .tickers()
            .await
            .into_iter()
            .map(|symbol| symbol.to_ascii_uppercase())
            .filter(|symbol| !symbol.is_empty())
            .collect()
    }

    async fn recent_q_live_symbols(
        &self,
        window_start: DateTime<Utc>,
    ) -> Result<Vec<String>, String> {
        let table = live_event_table_expr(
            &self.config,
            window_start.date_naive(),
            Utc::now().date_naive(),
        );
        let sql = format!(
            r#"
            SELECT DISTINCT ticker
            FROM {table}
            WHERE event_date >= toDate('{start_date}')
              AND sip_timestamp_us >= {start_us}
              AND ticker != ''
            ORDER BY ticker
            FORMAT TSV
            "#,
            table = table,
            start_date = window_start.date_naive(),
            start_us = window_start.timestamp_micros(),
        );
        self.symbols_from_sql(&sql).await
    }

    async fn latest_q_live_symbols(&self) -> Result<Vec<String>, String> {
        let now = Utc::now().date_naive();
        let table = live_event_table_expr(&self.config, now - ChronoDuration::days(3), now);
        let sql = format!(
            r#"
            WITH latest AS (SELECT max(event_date) AS event_date FROM {table})
            SELECT DISTINCT ticker
            FROM {table}
            WHERE event_date = (SELECT event_date FROM latest)
              AND ticker != ''
            ORDER BY ticker
            FORMAT TSV
            "#,
            table = table,
        );
        self.symbols_from_sql(&sql).await
    }

    async fn latest_historical_symbols(&self) -> Result<Vec<String>, String> {
        let latest = self.latest_historical_event_date().await?;
        let db = self.config.historical_clickhouse_database.replace('`', "");
        let sql = format!(
            r#"
            SELECT DISTINCT ticker
            FROM {db}.events_ticker_day_index
            WHERE source_date = toDate('{latest}')
              AND ticker != ''
            ORDER BY ticker
            FORMAT TSV
            "#,
            db = db,
            latest = escape_sql_string(&latest),
        );
        let symbols = self.symbols_from_historical_sql(&sql).await?;
        if !symbols.is_empty() {
            return Ok(symbols);
        }
        Ok(symbols)
    }

    async fn latest_historical_symbols_for_market_days(
        &self,
        market_days: usize,
    ) -> Result<Vec<String>, String> {
        let db = self.config.historical_clickhouse_database.replace('`', "");
        let days = market_days.max(1);
        let sql = format!(
            r#"
            WITH recent_dates AS
            (
                SELECT source_date
                FROM {db}.events_ordinal_continuity
                WHERE source_date >= toDate('2019-01-01')
                GROUP BY source_date
                ORDER BY source_date DESC
                LIMIT {days}
            )
            SELECT DISTINCT ticker
            FROM {db}.events_ticker_day_index
            WHERE source_date IN (SELECT source_date FROM recent_dates)
              AND ticker != ''
            ORDER BY ticker
            FORMAT TSV
            "#,
            db = db,
            days = days,
        );
        let symbols = self.symbols_from_historical_sql(&sql).await?;
        if !symbols.is_empty() {
            return Ok(symbols);
        }
        Ok(symbols)
    }

    async fn symbols_from_sql(&self, sql: &str) -> Result<Vec<String>, String> {
        let text = self.query(sql, true).await?;
        Ok(parse_symbol_lines(&text))
    }

    async fn symbols_from_historical_sql(&self, sql: &str) -> Result<Vec<String>, String> {
        let text = self.query_historical(sql).await.unwrap_or_default();
        Ok(parse_symbol_lines(&text))
    }

    async fn record_completed_live_repair_coverage(
        &self,
        started_at: DateTime<Utc>,
        mode: &str,
        intervals: &[RepairInterval],
        interval_stats: &[IntervalRepairStats],
    ) -> Result<(), String> {
        for (index, interval) in intervals.iter().enumerate() {
            let stats = interval_stats.get(index).cloned().unwrap_or_default();
            let bar_rows_added = stats.bar_rows_after.saturating_sub(stats.bar_rows_before);
            let status = if stats.errors > 0 || (stats.rows > 0 && bar_rows_added == 0) {
                "partial_failed"
            } else {
                "repair_completed"
            };
            self.record_event_coverage_snapshot(
                &self.config.qmd_live_event_coverage_table,
                "q_live_events",
                &format!(
                    "repair_{}_{}_{}",
                    self.config.qmd_run_id,
                    started_at.timestamp_millis(),
                    index
                ),
                "massive_rest_gap_repair",
                status,
                interval.start,
                interval.end,
                stats.rows,
                stats.rows,
                bar_rows_added,
                if status == "partial_failed" { 1 } else { 0 },
                started_at,
                Some(Utc::now()),
                &json!({
                    "mode": mode,
                    "reason": interval.reason,
                    "persistence_contract": [
                        self.config.compact_event_table.as_str(),
                        "q_live.live_market_bars"
                    ],
                    "symbols_attempted": stats.symbols_attempted,
                    "symbols_with_rows": stats.symbols_with_rows,
                    "page_limit_symbols": stats.page_limit_symbols,
                    "repair_errors": stats.errors,
                    "bar_rows_before": stats.bar_rows_before,
                    "bar_rows_after": stats.bar_rows_after,
                    "excluded_tables": [
                        "q_live.live_massive_trades",
                        "q_live.live_massive_quotes",
                        "q_live.live_market_indicators"
                    ],
                }),
            )
            .await?;
        }
        Ok(())
    }

    async fn count_bar_rows(
        &self,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> Result<u64, String> {
        let sql = format!(
            r#"
            SELECT count()
            FROM live_market_bars
            WHERE bar_end > toDateTime64('{start}', 3, 'UTC')
              AND bar_start < toDateTime64('{end}', 3, 'UTC')
            FORMAT TSV
            "#,
            start = clickhouse_datetime64(&start),
            end = clickhouse_datetime64(&end),
        );
        Ok(self
            .query(&sql, true)
            .await?
            .trim()
            .parse::<u64>()
            .unwrap_or(0))
    }

    fn recent_live_window(&self) -> (DateTime<Utc>, DateTime<Utc>, Vec<NaiveDate>) {
        let now = Utc::now();
        let today = now.with_timezone(&New_York).date_naive();
        let target_count = self
            .config
            .recent_live_prior_market_days
            .max(0)
            .saturating_add(1) as usize;
        let dates = self.calendar.prior_sessions(today, target_count);
        let start = dates
            .first()
            .copied()
            .and_then(|date| {
                self.calendar
                    .collection_window_utc(date, now)
                    .map(|(start, _)| start)
            })
            .unwrap_or_else(Utc::now);
        (start, now, dates)
    }

    async fn audit_recent_live_events(&self) -> Result<LiveEventAudit, String> {
        if !self.config.compact_events_enabled {
            return Ok(LiveEventAudit::default());
        }
        let (window_start, _, _) = self.recent_live_window();
        let start_date = window_start.date_naive();
        let start_us = window_start.timestamp_micros();
        let sql = format!(
            r#"
            WITH recent AS
            (
                SELECT
                    ticker,
                    sip_timestamp_us,
                    source_sequence,
                    bitAnd(event_meta, 1) AS event_type,
                    event_meta,
                    price_primary_int,
                    price_secondary_int,
                    size_primary,
                    size_secondary,
                    exchange_primary,
                    exchange_secondary,
                    condition_token_1,
                    condition_token_2,
                    condition_token_3,
                    condition_token_4,
                    condition_token_5
                FROM {table} FINAL
                WHERE event_date >= toDate('{start_date}')
                  AND sip_timestamp_us >= {start_us}
                  AND ticker != ''
            )
            SELECT
                (SELECT count() FROM recent) AS recent_rows,
                (SELECT uniqExact(ticker) FROM recent) AS ticker_count,
                (SELECT count() - uniqExact(tuple(
                    ticker,
                    sip_timestamp_us,
                    source_sequence,
                    event_type,
                    event_meta,
                    price_primary_int,
                    price_secondary_int,
                    size_primary,
                    size_secondary,
                    exchange_primary,
                    exchange_secondary,
                    condition_token_1,
                    condition_token_2,
                    condition_token_3,
                    condition_token_4,
                    condition_token_5
                )) FROM recent) AS duplicate_event_rows
            FORMAT JSONEachRow
            "#,
            table = self.config.compact_event_table,
            start_date = start_date,
            start_us = start_us,
        );
        let text = self.query(&sql, true).await?;
        let Some(line) = text.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(LiveEventAudit::default());
        };
        let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
        Ok(LiveEventAudit {
            duplicate_event_rows: value
                .get("duplicate_event_rows")
                .and_then(json_u64)
                .unwrap_or(0),
            recent_rows: value.get("recent_rows").and_then(json_u64).unwrap_or(0),
            ticker_count: value.get("ticker_count").and_then(json_u64).unwrap_or(0),
        })
    }

    async fn plan_historical_flatfile_update(
        &self,
        started_at: DateTime<Utc>,
        mode: &str,
        _record_up_to_date: bool,
    ) -> Result<(), String> {
        let now = Utc::now();
        let local = now.with_timezone(&New_York);
        if local.hour() < 8 {
            return Ok(());
        }
        let snapshot = self.calendar.snapshot(now);
        let latest = self.latest_historical_event_date().await?;
        self.confirm_flatfile_coverage(&latest).await?;
        let latest_date =
            NaiveDate::parse_from_str(&latest, "%Y-%m-%d").map_err(|error| error.to_string())?;
        let local_date = local.date_naive();
        let target_date = if self.calendar.is_session_date(local_date) {
            self.calendar
                .prior_sessions(local_date, 3)
                .first()
                .copied()
                .ok_or("could not determine the T-2 historical target session")?
        } else {
            self.calendar
                .prior_sessions(local_date, 1)
                .last()
                .copied()
                .ok_or("could not determine the latest closed historical target session")?
        };

        let mut candidates = BTreeSet::new();
        let Some(mut cursor) = next_date(&latest) else {
            return Ok(());
        };
        while cursor <= target_date {
            if self.calendar.is_session_date(cursor) {
                candidates.insert(cursor);
            }
            cursor += ChronoDuration::days(1);
        }

        // Recheck a bounded set of indexed objects twice daily. A changed
        // object identity is a historical rebuild trigger even when the
        // session was already confirmed.
        let recheck = self
            .query(
                &format!(
                    "SELECT DISTINCT toString(session_date) FROM {} FINAL WHERE session_date <= toDate('{}') AND updated_at_utc <= now() - INTERVAL 12 HOUR ORDER BY session_date LIMIT 16 FORMAT TSV",
                    self.config.qmd_flatfile_coverage_table, target_date
                ),
                true,
            )
            .await?;
        for value in recheck.lines().filter(|value| !value.trim().is_empty()) {
            if let Ok(date) = NaiveDate::parse_from_str(value.trim(), "%Y-%m-%d") {
                candidates.insert(date);
            }
        }

        if candidates.is_empty() {
            return Ok(());
        }

        let host_role = self.host_role();
        let mut ready_dates = BTreeSet::new();
        let mut ready_objects = BTreeMap::new();
        for date in candidates {
            let mut both_ready = true;
            let mut needs_update = date > latest_date;
            for kind in ["quote", "trade"] {
                let prior = self.flatfile_coverage_state(date, kind).await?;
                match self.flatfiles.discover(date, kind).await {
                    Ok(Some(object)) => {
                        let changed = prior
                            .as_ref()
                            .map(|value| remote_object_changed(value, &object))
                            .unwrap_or(false);
                        let historical_status = if changed {
                            "remote_changed"
                        } else {
                            prior
                                .as_ref()
                                .map(|value| value.historical_status.as_str())
                                .unwrap_or("not_confirmed")
                        };
                        let historical_rows = prior
                            .as_ref()
                            .map(|value| value.historical_rows)
                            .unwrap_or(0);
                        needs_update |= changed || historical_status != "confirmed";
                        let pending_request = !changed
                            && matches!(
                                historical_status,
                                "launched" | "launch_in_progress" | "manual_action_required"
                            );
                        if !pending_request {
                            self.record_flatfile_coverage(
                                date,
                                kind,
                                "remote_ready",
                                Some(&object),
                                latest.as_str(),
                                historical_status,
                                historical_rows,
                                &host_role,
                                "",
                                "",
                            )
                            .await?;
                        }
                        ready_objects.insert((date, kind.to_string()), object);
                    }
                    Ok(None) => {
                        both_ready = false;
                        self.record_flatfile_coverage(
                            date,
                            kind,
                            "remote_missing",
                            None,
                            latest.as_str(),
                            prior
                                .as_ref()
                                .map(|value| value.historical_status.as_str())
                                .unwrap_or("not_confirmed"),
                            prior
                                .as_ref()
                                .map(|value| value.historical_rows)
                                .unwrap_or(0),
                            &host_role,
                            "",
                            "",
                        )
                        .await?;
                    }
                    Err(error) => {
                        both_ready = false;
                        self.record_flatfile_coverage(
                            date,
                            kind,
                            "discovery_failed",
                            None,
                            latest.as_str(),
                            prior
                                .as_ref()
                                .map(|value| value.historical_status.as_str())
                                .unwrap_or("not_confirmed"),
                            prior
                                .as_ref()
                                .map(|value| value.historical_rows)
                                .unwrap_or(0),
                            &host_role,
                            "",
                            &error,
                        )
                        .await?;
                    }
                }
            }
            if both_ready && needs_update {
                ready_dates.insert(date);
            }
        }

        let Some(start_date) = ready_dates.first().copied() else {
            return Ok(());
        };
        let target_end = *ready_dates.last().expect("ready dates is non-empty");
        let command =
            self.historical_update_command(&start_date.to_string(), &target_end.to_string());
        eprintln!(
            "Historical flatfile update needed: {start_date} to {target_end}; command: {command}"
        );
        let mut status = if snapshot.active_collection_window {
            "waiting_for_market_close"
        } else {
            "manual_action_required"
        };
        let recent_request = self.query(&format!(
            "SELECT count() FROM {} FINAL WHERE session_date >= toDate('{}') AND session_date <= toDate('{}') AND historical_status IN ('launched', 'launch_in_progress', 'manual_action_required') AND updated_at_utc >= now() - INTERVAL 12 HOUR FORMAT TSV",
            self.config.qmd_flatfile_coverage_table, start_date, target_end,
        ), true).await?.trim().parse::<u64>().unwrap_or(0) > 0;
        if host_role == "workstation"
            && self.config.historical_flatfile_autorun
            && !snapshot.active_collection_window
            && snapshot.market_closed
        {
            if recent_request {
                status = "launch_in_progress";
            } else {
                match spawn_command(&command) {
                    Ok(()) => {
                        status = "launched";
                        eprintln!("Historical flatfile update launched asynchronously.");
                    }
                    Err(error) => {
                        status = "launch_failed";
                        eprintln!("Historical flatfile update launch failed: {error}");
                    }
                }
            }
        } else if recent_request && !snapshot.active_collection_window {
            status = "manual_action_required";
        }

        // Keep the original request timestamp stable so confirmation can prove
        // that historical continuity advanced after the request.
        if !recent_request || snapshot.active_collection_window {
            for date in &ready_dates {
                for kind in ["quote", "trade"] {
                    if let Some(object) = ready_objects.get(&(*date, kind.to_string())) {
                        self.record_flatfile_coverage(
                            *date,
                            kind,
                            "remote_ready",
                            Some(&object),
                            latest.as_str(),
                            status,
                            0,
                            &host_role,
                            &command,
                            "",
                        )
                        .await?;
                    }
                }
            }
        }
        self.record_coverage_run(
            started_at,
            "historical_flatfile_events",
            status,
            date_start_utc(start_date),
            date_start_utc(target_end) + ChronoDuration::days(1),
            mode,
            0,
            &host_role,
            &command,
            &json!({
                "latest_historical_event_date": latest,
                "target_end_date": target_end.to_string(),
                "autorun": self.config.historical_flatfile_autorun,
                "calendar_source": snapshot.source,
                "calendar_reason": snapshot.reason,
            }),
        )
        .await
    }

    async fn enforce_live_retention(&self, started_at: DateTime<Utc>) -> Result<(), String> {
        if !self.config.compact_events_enabled {
            return Ok(());
        }
        let today = Utc::now().with_timezone(&New_York).date_naive();
        let sessions = self.calendar.prior_sessions(
            today,
            self.config.recent_live_prior_market_days.max(0) as usize + 1,
        );
        let Some(cutoff) = sessions.first().copied() else {
            return Ok(());
        };
        let latest = self.latest_historical_event_date().await?;
        let old_sessions = self
            .query(
                &format!(
                    "SELECT toString(toDate(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'America/New_York'))) AS session_date, count() FROM {} FINAL WHERE session_date < toDate('{}') GROUP BY session_date ORDER BY session_date FORMAT TSV",
                    self.config.compact_event_table, cutoff,
                ),
                true,
            )
            .await?;
        let session_rows = old_sessions
            .lines()
            .filter_map(|line| {
                let (date, count) = line.split_once('\t')?;
                Some((
                    NaiveDate::parse_from_str(date, "%Y-%m-%d").ok()?,
                    count.parse::<u64>().ok()?,
                ))
            })
            .collect::<Vec<_>>();
        let older_rows = session_rows.iter().map(|(_, count)| count).sum::<u64>();
        if older_rows == 0 {
            return Ok(());
        }
        let mut blocked_rows = 0u64;
        let mut blocked_sessions = Vec::new();
        for (date, count) in &session_rows {
            let continuity_rows = self
                .query_historical(&format!(
                    "SELECT count() FROM {}.events_ordinal_continuity FINAL WHERE source_date = toDate('{}') FORMAT TSV",
                    self.config.historical_clickhouse_database.replace('`', ""),
                    date,
                ))
                .await?
                .trim()
                .parse::<u64>()
                .unwrap_or(0);
            if continuity_rows == 0 {
                blocked_rows = blocked_rows.saturating_add(*count);
                blocked_sessions.push(date.to_string());
            }
        }
        if blocked_rows > 0 {
            self.record_coverage_run(started_at, "q_live_retention", "retention_blocked_historical_gap", date_start_utc(cutoff), Utc::now(), "retention", 0, &self.host_role(), "", &json!({"cutoff": cutoff.to_string(), "latest_historical": latest, "blocked_rows": blocked_rows, "blocked_sessions": blocked_sessions})).await?;
            return Ok(());
        }
        self.query(
            &format!(
                "ALTER TABLE {} DELETE WHERE toDate(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'America/New_York')) < toDate('{}')",
                self.config.compact_event_table, cutoff
            ),
            true,
        )
        .await?;
        self.record_coverage_run(started_at, "q_live_retention", "retention_applied", date_start_utc(cutoff), Utc::now(), "retention", 0, &self.host_role(), "", &json!({"retained_sessions": sessions.iter().map(ToString::to_string).collect::<Vec<_>>(), "historical_confirmed_through": latest})).await
    }

    async fn confirm_flatfile_coverage(&self, latest: &str) -> Result<(), String> {
        let dates = self.query(&format!(
            "SELECT DISTINCT toString(session_date) FROM {} FINAL WHERE session_date <= toDate('{}') AND historical_status != 'confirmed' ORDER BY session_date FORMAT TSV",
            self.config.qmd_flatfile_coverage_table, escape_sql_string(latest),
        ), true).await?;
        for value in dates.lines().filter(|value| !value.trim().is_empty()) {
            let date = NaiveDate::parse_from_str(value.trim(), "%Y-%m-%d")
                .map_err(|error| error.to_string())?;
            let historical_table = format!(
                "{}.events_{}",
                self.config.historical_clickhouse_database.replace('`', ""),
                date.year()
            );
            for (kind, event_type) in [("quote", 0), ("trade", 1)] {
                let Some(state) = self.flatfile_coverage_state(date, kind).await? else {
                    continue;
                };
                let continuity_updated = self
                    .query_historical(&format!(
                        "SELECT max(updated_at) FROM {}.events_ordinal_continuity WHERE source_date = toDate('{}') FORMAT TSV",
                        self.config.historical_clickhouse_database.replace('`', ""),
                        date,
                    ))
                    .await?;
                let Some(continuity_updated) =
                    parse_clickhouse_datetime64(continuity_updated.trim())
                else {
                    continue;
                };
                if continuity_updated <= state.updated_at_utc {
                    continue;
                }
                let count = self.query_historical(&format!(
                    "SELECT count() FROM {} WHERE toDate(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'America/New_York')) = toDate('{}') AND bitAnd(event_meta, 1) = {} FORMAT TSV",
                    historical_table, date, event_type,
                )).await?.trim().parse::<u64>().unwrap_or(0);
                self.query(
                    &format!(
                        r#"
                    INSERT INTO {table}
                    SELECT session_date, source_kind, remote_status, remote_key, remote_etag,
                        remote_last_modified, remote_content_length, 'confirmed', {count},
                        toDate('{latest}'), host_role, command, error, now64(3)
                    FROM {table} FINAL
                    WHERE session_date = toDate('{date}') AND source_kind = '{kind}'
                    ORDER BY updated_at_utc DESC LIMIT 1
                "#,
                        table = self.config.qmd_flatfile_coverage_table,
                        count = count,
                        latest = escape_sql_string(latest),
                        date = date,
                        kind = kind
                    ),
                    true,
                )
                .await?;
            }
        }
        Ok(())
    }

    async fn flatfile_coverage_state(
        &self,
        date: NaiveDate,
        kind: &str,
    ) -> Result<Option<FlatfileCoverageState>, String> {
        let body = self
            .query(
                &format!(
                    r#"
                    SELECT remote_key, remote_etag, remote_last_modified,
                        remote_content_length, historical_status, historical_rows,
                        updated_at_utc
                    FROM {} FINAL
                    WHERE session_date = toDate('{}') AND source_kind = '{}'
                    LIMIT 1
                    FORMAT JSONEachRow
                    "#,
                    self.config.qmd_flatfile_coverage_table,
                    date,
                    escape_sql_string(kind),
                ),
                true,
            )
            .await?;
        let Some(line) = body.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(None);
        };
        let row: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
        let updated_at_utc = row
            .get("updated_at_utc")
            .and_then(Value::as_str)
            .and_then(parse_clickhouse_datetime64)
            .ok_or_else(|| format!("invalid flatfile coverage timestamp for {date} {kind}"))?;
        Ok(Some(FlatfileCoverageState {
            historical_rows: row.get("historical_rows").and_then(json_u64).unwrap_or(0),
            historical_status: row
                .get("historical_status")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            remote_content_length: row
                .get("remote_content_length")
                .and_then(json_u64)
                .unwrap_or(0),
            remote_etag: row
                .get("remote_etag")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            remote_key: row
                .get("remote_key")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            remote_last_modified: row
                .get("remote_last_modified")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            updated_at_utc,
        }))
    }

    fn rest_url(
        &self,
        symbol: &str,
        kind: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> String {
        let endpoint = if kind == "trades" { "trades" } else { "quotes" };
        format!(
            "https://api.massive.com/v3/{endpoint}/{symbol}?timestamp.gte={}&timestamp.lt={}&order=asc&sort=timestamp&limit=50000&apiKey={}",
            start.timestamp_nanos_opt().unwrap_or_default(),
            end.timestamp_nanos_opt().unwrap_or_default(),
            urlencoding::encode(&self.config.massive_api_key),
        )
    }

    async fn initialize_tables(&self) -> Result<(), String> {
        self.query(
            &format!(
                "CREATE DATABASE IF NOT EXISTS `{}`",
                self.config.clickhouse_database
            ),
            false,
        )
        .await?;
        self.query(
            r#"
            CREATE TABLE IF NOT EXISTS qmd_gap_fill_runs
            (
                started_at DateTime64(3, 'UTC'),
                mode LowCardinality(String),
                phase LowCardinality(String),
                symbol LowCardinality(String),
                status LowCardinality(String),
                rows_written UInt64,
                message String
            )
            ENGINE = MergeTree
            ORDER BY (started_at, symbol)
            "#,
            true,
        )
        .await
        .map(|_| ())?;
        self.query(
            &format!(
                r#"
                CREATE TABLE IF NOT EXISTS {table}
                (
                    started_at DateTime64(3, 'UTC'),
                    finished_at DateTime64(3, 'UTC'),
                    coverage_kind LowCardinality(String),
                    status LowCardinality(String),
                    start_ts_utc DateTime64(3, 'UTC'),
                    end_ts_utc DateTime64(3, 'UTC'),
                    action LowCardinality(String),
                    rows_written UInt64,
                    host_role LowCardinality(String),
                    command String,
                    summary_json String
                )
                ENGINE = MergeTree
                ORDER BY (coverage_kind, started_at)
                "#,
                table = self.config.qmd_coverage_table
            ),
            true,
        )
        .await
        .map(|_| ())?;
        self.query(
            &self.create_event_coverage_table_sql(&self.config.qmd_live_event_coverage_table),
            true,
        )
        .await
        .map(|_| ())?;
        self.query(&self.create_flatfile_coverage_table_sql(), true)
            .await?;
        self.query("DROP TABLE IF EXISTS qmd_flatfile_event_coverage_v1", true)
            .await?;
        self.query(&self.create_gap_fill_symbol_universe_table_sql(), true)
            .await
            .map(|_| ())?;
        self.ensure_current_live_coverage_open(Utc::now()).await
    }

    fn create_gap_fill_symbol_universe_table_sql(&self) -> String {
        format!(
            r#"
            CREATE TABLE IF NOT EXISTS {table}
            (
                symbol LowCardinality(String),
                status LowCardinality(String),
                source LowCardinality(String),
                observed_at_utc DateTime64(3, 'UTC'),
                updated_at_utc DateTime64(3, 'UTC'),
                last_gap_fill_started_at_utc Nullable(DateTime64(3, 'UTC')),
                last_gap_fill_completed_at_utc Nullable(DateTime64(3, 'UTC')),
                last_window_start_utc Nullable(DateTime64(3, 'UTC')),
                last_window_end_utc Nullable(DateTime64(3, 'UTC')),
                rows_written UInt64,
                error_count UInt64,
                metadata_json String
            )
            ENGINE = ReplacingMergeTree(updated_at_utc)
            ORDER BY symbol
            {settings}
            "#,
            table = self.config.qmd_gap_fill_symbol_universe_table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        )
    }

    fn create_event_coverage_table_sql(&self, table: &str) -> String {
        format!(
            r#"
            CREATE TABLE IF NOT EXISTS {table}
            (
                coverage_kind LowCardinality(String),
                coverage_id String,
                source LowCardinality(String),
                status LowCardinality(String),
                coverage_start_utc DateTime64(3, 'UTC'),
                coverage_end_utc DateTime64(3, 'UTC'),
                rows_written UInt64,
                event_rows UInt64,
                bar_rows UInt64,
                error_count UInt64,
                started_at_utc DateTime64(3, 'UTC'),
                updated_at_utc DateTime64(3, 'UTC'),
                completed_at_utc Nullable(DateTime64(3, 'UTC')),
                metadata_json String
            )
            ENGINE = ReplacingMergeTree(updated_at_utc)
            PARTITION BY toYYYYMM(coverage_start_utc)
            ORDER BY (coverage_kind, coverage_id)
            {settings}
            "#,
            table = table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        )
    }

    fn create_flatfile_coverage_table_sql(&self) -> String {
        format!(
            r#"
            CREATE TABLE IF NOT EXISTS {table}
            (
                session_date Date,
                source_kind LowCardinality(String),
                remote_status LowCardinality(String),
                remote_key String,
                remote_etag String,
                remote_last_modified String,
                remote_content_length UInt64,
                historical_status LowCardinality(String),
                historical_rows UInt64,
                historical_confirmed_through Nullable(Date),
                host_role LowCardinality(String),
                command String,
                error String,
                updated_at_utc DateTime64(3, 'UTC')
            )
            ENGINE = ReplacingMergeTree(updated_at_utc)
            PARTITION BY toYYYYMM(session_date)
            ORDER BY (session_date, source_kind)
            {settings}
        "#,
            table = self.config.qmd_flatfile_coverage_table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy)
        )
    }

    async fn record_flatfile_coverage(
        &self,
        session_date: NaiveDate,
        source_kind: &str,
        remote_status: &str,
        object: Option<&crate::flatfile::RemoteFlatfile>,
        historical_confirmed_through: &str,
        historical_status: &str,
        historical_rows: u64,
        host_role: &str,
        command: &str,
        error: &str,
    ) -> Result<(), String> {
        let row = json!({
            "session_date": session_date.to_string(),
            "source_kind": source_kind,
            "remote_status": remote_status,
            "remote_key": object.map(|value| value.key.as_str()).unwrap_or(""),
            "remote_etag": object.map(|value| value.etag.as_str()).unwrap_or(""),
            "remote_last_modified": object.map(|value| value.last_modified.as_str()).unwrap_or(""),
            "remote_content_length": object.map(|value| value.content_length).unwrap_or(0),
            "historical_status": historical_status,
            "historical_rows": historical_rows,
            "historical_confirmed_through": historical_confirmed_through,
            "host_role": host_role,
            "command": command,
            "error": error,
            "updated_at_utc": clickhouse_datetime64(&Utc::now()),
        });
        self.query(
            &format!(
                "INSERT INTO {} FORMAT JSONEachRow\n{}",
                self.config.qmd_flatfile_coverage_table, row
            ),
            true,
        )
        .await
        .map(|_| ())
    }

    async fn record_run(
        &self,
        started_at: DateTime<Utc>,
        mode: &str,
        phase: &str,
        symbol: &str,
        status: &str,
        rows_written: u64,
        message: &str,
    ) -> Result<(), String> {
        let row = json!({
            "started_at": clickhouse_datetime64(&started_at),
            "mode": mode,
            "phase": phase,
            "symbol": symbol,
            "status": status,
            "rows_written": rows_written,
            "message": message,
        });
        self.query(
            &format!("INSERT INTO qmd_gap_fill_runs FORMAT JSONEachRow\n{}", row),
            true,
        )
        .await
        .map(|_| ())
    }

    async fn record_coverage_run(
        &self,
        started_at: DateTime<Utc>,
        coverage_kind: &str,
        status: &str,
        start_ts_utc: DateTime<Utc>,
        end_ts_utc: DateTime<Utc>,
        action: &str,
        rows_written: u64,
        host_role: &str,
        command: &str,
        summary: &Value,
    ) -> Result<(), String> {
        let row = json!({
            "started_at": clickhouse_datetime64(&started_at),
            "finished_at": clickhouse_datetime64(&Utc::now()),
            "coverage_kind": coverage_kind,
            "status": status,
            "start_ts_utc": clickhouse_datetime64(&start_ts_utc),
            "end_ts_utc": clickhouse_datetime64(&end_ts_utc),
            "action": action,
            "rows_written": rows_written,
            "host_role": host_role,
            "command": command,
            "summary_json": summary.to_string(),
        });
        self.query(
            &format!(
                "INSERT INTO {} FORMAT JSONEachRow\n{}",
                self.config.qmd_coverage_table, row
            ),
            true,
        )
        .await
        .map(|_| ())
    }

    async fn ensure_current_live_coverage_open(
        &self,
        fallback_start: DateTime<Utc>,
    ) -> Result<(), String> {
        let coverage_id = format!("live_{}", self.config.qmd_run_id);
        let sql = format!(
            "SELECT count() FROM {} FINAL WHERE coverage_kind = 'q_live_events' AND coverage_id = '{}' FORMAT TSV",
            self.config.qmd_live_event_coverage_table,
            escape_sql_string(&coverage_id),
        );
        let count = self
            .query(&sql, true)
            .await
            .ok()
            .and_then(|text| text.trim().parse::<u64>().ok())
            .unwrap_or(0);
        if count > 0 {
            return Ok(());
        }
        let started_at = self.config.qmd_run_started_at().unwrap_or(fallback_start);
        self.record_event_coverage_snapshot(
            &self.config.qmd_live_event_coverage_table,
            "q_live_events",
            &coverage_id,
            "qmd_gateway_run",
            "running",
            started_at,
            started_at,
            0,
            0,
            0,
            0,
            started_at,
            None,
            &json!({
                "message": "current qmd run coverage opened before live ingest",
                "raw_trade_quote_tables": "not_in_persistence_contract",
            }),
        )
        .await
    }

    async fn record_event_coverage_snapshot(
        &self,
        table: &str,
        coverage_kind: &str,
        coverage_id: &str,
        source: &str,
        status: &str,
        coverage_start: DateTime<Utc>,
        coverage_end: DateTime<Utc>,
        rows_written: u64,
        event_rows: u64,
        bar_rows: u64,
        error_count: u64,
        started_at: DateTime<Utc>,
        completed_at: Option<DateTime<Utc>>,
        metadata: &Value,
    ) -> Result<(), String> {
        let now = Utc::now();
        let row = json!({
            "coverage_kind": coverage_kind,
            "coverage_id": coverage_id,
            "source": source,
            "status": status,
            "coverage_start_utc": clickhouse_datetime64(&coverage_start),
            "coverage_end_utc": clickhouse_datetime64(&coverage_end),
            "rows_written": rows_written,
            "event_rows": event_rows,
            "bar_rows": bar_rows,
            "error_count": error_count,
            "started_at_utc": clickhouse_datetime64(&started_at),
            "updated_at_utc": clickhouse_datetime64(&now),
            "completed_at_utc": completed_at.map(|value| clickhouse_datetime64(&value)),
            "metadata_json": metadata.to_string(),
        });
        self.query(
            &format!("INSERT INTO {table} FORMAT JSONEachRow\n{row}"),
            true,
        )
        .await
        .map(|_| ())
    }

    async fn latest_historical_event_date(&self) -> Result<String, String> {
        let sql = format!(
            "SELECT max(source_date) FROM {}.events_ordinal_continuity FINAL FORMAT TSV",
            self.config.historical_clickhouse_database.replace('`', "")
        );
        let value = self.query_historical(&sql).await?.trim().to_string();
        if value.is_empty() || value == "0000-00-00" {
            Err(
                "market_sip_compact.events_ordinal_continuity has no confirmed source_date"
                    .to_string(),
            )
        } else {
            Ok(value)
        }
    }

    fn historical_update_command(&self, start_date: &str, end_date: &str) -> String {
        let script = format!(
            "{}\\pipelines\\market_sip\\flatfiles\\download_update_events.py",
            self.config.historical_pipeline_code_root
        );
        format!(
            "python {} --database {} --events-table events --macro-bars-table macro_bars_by_time_symbol --bar-timeframes 1d --start-date {} --end-date {}",
            shell_arg(&script),
            shell_arg(&self.config.historical_clickhouse_database),
            start_date,
            end_date,
        )
    }

    fn host_role(&self) -> String {
        self.config.resolved_host_role()
    }

    async fn query(&self, body: &str, use_database: bool) -> Result<String, String> {
        let url = if use_database {
            format!(
                "{}/?database={}",
                self.config.clickhouse_url,
                urlencoding::encode(&self.config.clickhouse_database)
            )
        } else {
            format!("{}/", self.config.clickhouse_url)
        };
        let mut request = self
            .client
            .post(url)
            .header("Content-Type", "text/plain; charset=utf-8")
            .header("X-ClickHouse-User", &self.config.clickhouse_user)
            .body(body.to_string());
        let password = self.config.clickhouse_password();
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

    async fn query_historical(&self, body: &str) -> Result<String, String> {
        let mut request = self
            .client
            .post(format!("{}/", self.config.historical_clickhouse_url))
            .header("Content-Type", "text/plain; charset=utf-8")
            .header("X-ClickHouse-User", &self.config.historical_clickhouse_user)
            .body(body.to_string());
        let password = self.config.historical_clickhouse_password();
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
}

fn should_run_session_catch_up(mode: &str) -> bool {
    matches!(mode, "auto" | "session" | "session_catch_up")
}

fn remote_object_changed(
    prior: &FlatfileCoverageState,
    current: &crate::flatfile::RemoteFlatfile,
) -> bool {
    prior.remote_key != current.key
        || prior.remote_etag != current.etag
        || prior.remote_last_modified != current.last_modified
        || prior.remote_content_length != current.content_length
}

fn next_date(value: &str) -> Option<NaiveDate> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .ok()
        .map(|date| date + ChronoDuration::days(1))
}

fn date_start_utc(date: NaiveDate) -> DateTime<Utc> {
    date.and_hms_opt(0, 0, 0)
        .expect("midnight is valid for every NaiveDate")
        .and_utc()
}

fn parse_clickhouse_datetime64(value: &str) -> Option<DateTime<Utc>> {
    NaiveDateTime::parse_from_str(value, "%Y-%m-%d %H:%M:%S%.f")
        .ok()
        .map(|value| Utc.from_utc_datetime(&value))
        .or_else(|| {
            DateTime::parse_from_rfc3339(value)
                .ok()
                .map(|value| value.with_timezone(&Utc))
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::flatfile::RemoteFlatfile;

    fn coverage_state() -> FlatfileCoverageState {
        FlatfileCoverageState {
            historical_rows: 10,
            historical_status: "confirmed".to_string(),
            remote_content_length: 100,
            remote_etag: "etag-a".to_string(),
            remote_key: "quotes.csv.gz".to_string(),
            remote_last_modified: "yesterday".to_string(),
            updated_at_utc: Utc::now(),
        }
    }

    #[test]
    fn remote_identity_change_reopens_flatfile_coverage() {
        let mut current = RemoteFlatfile {
            content_length: 100,
            etag: "etag-a".to_string(),
            key: "quotes.csv.gz".to_string(),
            last_modified: "yesterday".to_string(),
        };
        assert!(!remote_object_changed(&coverage_state(), &current));
        current.etag = "etag-b".to_string();
        assert!(remote_object_changed(&coverage_state(), &current));
    }
}

fn live_event_table_expr(
    config: &GatewayConfig,
    _start_date: NaiveDate,
    _end_date: NaiveDate,
) -> String {
    config.compact_event_table.clone()
}

fn escape_sql_string(value: &str) -> String {
    value.replace('\\', "\\\\").replace('\'', "\\'")
}

fn merge_tree_settings(storage_policy: &str) -> String {
    if storage_policy.trim().is_empty() {
        "SETTINGS index_granularity = 8192".to_string()
    } else {
        format!(
            "SETTINGS index_granularity = 8192, storage_policy = '{}'",
            storage_policy.trim().replace('\'', "\\'")
        )
    }
}

fn materialize_confirmed_live_coverage(rows: &[CoverageRow]) -> Vec<CoverageInterval> {
    let mut direct = Vec::new();
    let mut compact_by_run: BTreeMap<String, Vec<&CoverageRow>> = BTreeMap::new();
    let mut bars_by_run: BTreeMap<String, Vec<&CoverageRow>> = BTreeMap::new();
    for row in rows {
        match row.status.as_str() {
            "repair_completed" | "coverage_bootstrap" => direct.push(CoverageInterval {
                end: row.end,
                start: row.start,
            }),
            "compact_persisted" => {
                compact_by_run
                    .entry(run_suffix(&row.coverage_id, "compact_"))
                    .or_default()
                    .push(row);
            }
            "bars_persisted" => {
                bars_by_run
                    .entry(run_suffix(&row.coverage_id, "bars_"))
                    .or_default()
                    .push(row);
            }
            _ => {}
        }
    }
    let mut out = direct;
    for (run_id, compact_rows) in compact_by_run {
        let Some(bar_rows) = bars_by_run.get(&run_id) else {
            continue;
        };
        for compact in compact_rows {
            for bars in bar_rows {
                let start = compact.start.max(bars.start);
                let end = compact.end.min(bars.end);
                if end > start {
                    out.push(CoverageInterval { end, start });
                }
            }
        }
    }
    out.sort_by_key(|interval| (interval.start, interval.end));
    out
}

fn run_suffix(coverage_id: &str, prefix: &str) -> String {
    coverage_id
        .strip_prefix(prefix)
        .unwrap_or(coverage_id)
        .to_string()
}

fn parse_symbol_lines(text: &str) -> Vec<String> {
    text.lines()
        .map(|line| {
            line.split('\t')
                .next()
                .unwrap_or_default()
                .trim()
                .to_ascii_uppercase()
        })
        .filter(|symbol| !symbol.is_empty())
        .collect()
}

fn spawn_command(command: &str) -> Result<(), String> {
    let status = if cfg!(windows) {
        Command::new("cmd")
            .args(["/C", "start", "", "cmd", "/C", command])
            .spawn()
    } else {
        Command::new("sh").arg("-lc").arg(command).spawn()
    };
    status.map(|_| ()).map_err(|error| error.to_string())
}

fn shell_arg(value: &str) -> String {
    if value.chars().all(|ch| {
        ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.' | ':' | '\\' | '/' | ',')
    }) {
        value.to_string()
    } else {
        format!("\"{}\"", value.replace('"', "\\\""))
    }
}

fn append_api_key(url: &str, api_key: &str) -> String {
    if url.contains("apiKey=") {
        url.to_string()
    } else if url.contains('?') {
        format!("{url}&apiKey={}", urlencoding::encode(api_key))
    } else {
        format!("{url}?apiKey={}", urlencoding::encode(api_key))
    }
}

fn is_clickhouse_memory_limit(error: &str) -> bool {
    error.contains("MEMORY_LIMIT_EXCEEDED") || error.contains("memory limit exceeded")
}

fn redact_sensitive(text: &str) -> String {
    redact_query_param(text, "apiKey")
}

fn redact_query_param(text: &str, key: &str) -> String {
    let needle = format!("{key}=");
    let mut output = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(index) = rest.find(&needle) {
        let (prefix, after_prefix) = rest.split_at(index + needle.len());
        output.push_str(prefix);
        output.push_str("<redacted>");
        let value_end = after_prefix
            .find(|ch: char| matches!(ch, '&' | ')' | ' ' | '\n' | '\r' | '\t'))
            .unwrap_or(after_prefix.len());
        rest = &after_prefix[value_end..];
    }
    output.push_str(rest);
    output
}

fn rest_trade_event(symbol: &str, row: Value) -> Option<MarketEvent> {
    Some(MarketEvent::Trade(TradeEvent {
        conditions: u16_array_field(&row, "conditions"),
        exchange: u16_field(&row, "exchange"),
        ingest_ts: Utc::now(),
        participant_ts: optional_ns_field(&row, "participant_timestamp"),
        price: f64_field(&row, "price"),
        raw: row.clone(),
        sequence: u64_field(&row, "sequence_number"),
        size: f64_field(&row, "size"),
        tape: u8_field(&row, "tape"),
        ticker: symbol.to_ascii_uppercase(),
        trade_id: string_or_number_field(&row, "id"),
        trf_id: u16_field(&row, "trf_id"),
        trf_ts: optional_ns_field(&row, "trf_timestamp"),
        ts: optional_ns_field(&row, "sip_timestamp")?,
    }))
}

fn rest_quote_event(symbol: &str, row: Value) -> Option<MarketEvent> {
    Some(MarketEvent::Quote(QuoteEvent {
        ask_exchange: u16_field(&row, "ask_exchange"),
        ask_price: f64_field(&row, "ask_price"),
        ask_size: u32_field(&row, "ask_size"),
        bid_exchange: u16_field(&row, "bid_exchange"),
        bid_price: f64_field(&row, "bid_price"),
        bid_size: u32_field(&row, "bid_size"),
        conditions: u16_array_field(&row, "conditions"),
        indicators: u16_array_field(&row, "indicators"),
        ingest_ts: Utc::now(),
        raw: row.clone(),
        sequence: u64_field(&row, "sequence_number"),
        tape: u8_field(&row, "tape"),
        ticker: symbol.to_ascii_uppercase(),
        ts: optional_ns_field(&row, "sip_timestamp")?,
    }))
}

fn optional_ns_field(item: &Value, key: &str) -> Option<DateTime<Utc>> {
    let ns = item.get(key).and_then(json_i64)?;
    if ns <= 0 {
        return None;
    }
    ns_to_datetime(ns)
}

fn ns_to_datetime(ns: i64) -> Option<DateTime<Utc>> {
    let seconds = ns.div_euclid(1_000_000_000);
    let nanos = ns.rem_euclid(1_000_000_000) as u32;
    DateTime::<Utc>::from_timestamp(seconds, nanos)
}

fn string_or_number_field(item: &Value, key: &str) -> String {
    match item.get(key) {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Number(value)) => value.to_string(),
        _ => String::new(),
    }
}

fn f64_field(item: &Value, key: &str) -> f64 {
    item.get(key).and_then(json_f64).unwrap_or_default()
}

fn u64_field(item: &Value, key: &str) -> u64 {
    item.get(key).and_then(json_u64).unwrap_or_default()
}

fn u32_field(item: &Value, key: &str) -> u32 {
    u64_field(item, key).min(u32::MAX as u64) as u32
}

fn u16_field(item: &Value, key: &str) -> u16 {
    u64_field(item, key).min(u16::MAX as u64) as u16
}

fn u8_field(item: &Value, key: &str) -> u8 {
    u64_field(item, key).min(u8::MAX as u64) as u8
}

fn u16_array_field(item: &Value, key: &str) -> Vec<u16> {
    item.get(key)
        .and_then(Value::as_array)
        .map(|values| {
            values
                .iter()
                .filter_map(json_u64)
                .map(|value| value.min(u16::MAX as u64) as u16)
                .collect()
        })
        .unwrap_or_default()
}

fn json_f64(value: &Value) -> Option<f64> {
    match value {
        Value::Number(number) => number.as_f64(),
        Value::String(text) => text.parse::<f64>().ok(),
        _ => None,
    }
}

fn json_i64(value: &Value) -> Option<i64> {
    match value {
        Value::Number(number) => number
            .as_i64()
            .or_else(|| number.as_u64().and_then(|value| i64::try_from(value).ok())),
        Value::String(text) => text.parse::<i64>().ok(),
        _ => None,
    }
}

fn json_u64(value: &Value) -> Option<u64> {
    match value {
        Value::Number(number) => number
            .as_u64()
            .or_else(|| number.as_i64().and_then(|value| u64::try_from(value).ok())),
        Value::String(text) => text.parse::<u64>().ok(),
        _ => None,
    }
}
