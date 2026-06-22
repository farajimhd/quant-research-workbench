use crate::compact_event::SharedCompactEventStore;
use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::maintenance::SharedMaintenanceState;
use crate::massive::{fanout_market_event, MarketEventFanout};
use crate::metrics::TimingTarget;
use crate::session::{is_streaming_phase, session_phase};
use crate::timefmt::clickhouse_datetime64;
use chrono::{
    DateTime, Datelike, Duration as ChronoDuration, NaiveDate, NaiveDateTime, TimeZone, Utc,
    Weekday,
};
use chrono_tz::America::New_York;
use futures_util::stream::{self, StreamExt};
use reqwest::Client;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path as FsPath;
use std::process::Command;
use tokio::time::{sleep, Duration};

pub async fn run_startup_maintenance(
    config: GatewayConfig,
    fanout: MarketEventFanout,
    maintenance: SharedMaintenanceState,
    live_compact_store: SharedCompactEventStore,
) {
    if !config.gap_fill_enabled || !config.qmd_startup_maintenance_enabled {
        return;
    }
    let filler = GapFillService::new(config, fanout, maintenance, live_compact_store);
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
) {
    if !config.gap_fill_enabled {
        return;
    }
    let filler = GapFillService::new(config, fanout, maintenance, live_compact_store);
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
    duplicate_ticker_ordinal_rows: u64,
    hole_ticker_count: u64,
    out_of_order_ticker_count: u64,
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
}

impl GapFillService {
    fn new(
        config: GatewayConfig,
        fanout: MarketEventFanout,
        maintenance: SharedMaintenanceState,
        live_compact_store: SharedCompactEventStore,
    ) -> Self {
        Self {
            client: Client::new(),
            config,
            fanout,
            live_compact_store,
            maintenance,
        }
    }

    async fn run_startup_maintenance(&self) -> Result<(), String> {
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
            "QMD startup q_live audit: rows={} tickers={} duplicate_ordinals={} ordinal_hole_tickers={} out_of_order_tickers={}",
            audit.recent_rows,
            audit.ticker_count,
            audit.duplicate_ticker_ordinal_rows,
            audit.hole_ticker_count,
            audit.out_of_order_ticker_count
        );
        let mut status = if audit.duplicate_ticker_ordinal_rows == 0 {
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
            message = "Recent q_live event table has duplicate ticker ordinals. Not rewriting committed ordinals automatically.".to_string();
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
                "duplicate_ticker_ordinal_rows": audit.duplicate_ticker_ordinal_rows,
                "hole_ticker_count": audit.hole_ticker_count,
                "out_of_order_ticker_count": audit.out_of_order_ticker_count,
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
        if !is_streaming_phase(Utc::now()) && self.config.historical_flatfile_update_enabled {
            self.maintenance
                .set_message(
                    "running",
                    "Recent q_live repair finished; checking historical flatfile coverage.",
                )
                .await;
            self.plan_historical_flatfile_update(started_at, mode, false)
                .await?;
        }
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
                .map_err(|error| error.to_string())?
                .json()
                .await
                .map_err(|error| error.to_string())?;
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
                    eprintln!("QMD recent q_live repair failed for {symbol}: {error}");
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
            let Some((session_start, session_end)) = market_session_window_utc(*date, now) else {
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
        let mut symbols = BTreeSet::new();
        if is_streaming_phase(Utc::now()) {
            symbols.extend(self.live_compact_symbols().await);
            if self.config.compact_events_enabled {
                symbols.extend(self.recent_q_live_symbols(window_start).await?);
            }
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
            table = self.config.compact_event_table,
            start_date = window_start.date_naive(),
            start_us = window_start.timestamp_micros(),
        );
        self.symbols_from_sql(&sql).await
    }

    async fn latest_q_live_symbols(&self) -> Result<Vec<String>, String> {
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
            table = self.config.compact_event_table,
        );
        self.symbols_from_sql(&sql).await
    }

    async fn latest_historical_symbols(&self) -> Result<Vec<String>, String> {
        let latest = self.latest_historical_event_date().await?;
        let db = self.config.historical_clickhouse_database.replace('`', "");
        let sql = format!(
            r#"
            SELECT DISTINCT ticker
            FROM {db}.events
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
        let sql = format!(
            r#"
            SELECT DISTINCT sym
            FROM {db}.events
            WHERE source_date = toDate('{latest}')
              AND sym != ''
            ORDER BY sym
            FORMAT TSV
            "#,
            db = db,
            latest = escape_sql_string(&latest),
        );
        self.symbols_from_historical_sql(&sql).await
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
                        "q_live.live_market_events_v1",
                        "q_live.live_event_ordinal_continuity",
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
        let mut cursor = today;
        let mut dates = Vec::new();
        while dates.len() < target_count {
            if is_market_session_date(cursor) {
                dates.push(cursor);
            }
            cursor -= ChronoDuration::days(1);
        }
        dates.reverse();
        let start = dates
            .first()
            .copied()
            .and_then(|date| market_session_window_utc(date, now).map(|(start, _)| start))
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
        let table = &self.config.compact_event_table;
        let sql = format!(
            r#"
            WITH recent AS
            (
                SELECT
                    ticker,
                    ordinal,
                    sip_timestamp_us,
                    source_sequence,
                    event_type,
                    arrival_sequence
                FROM {table}
                WHERE event_date >= toDate('{start_date}')
                  AND sip_timestamp_us >= {start_us}
                  AND ticker != ''
            ),
            ticker_ranges AS
            (
                SELECT
                    ticker,
                    count() AS rows,
                    min(ordinal) AS min_ordinal,
                    max(ordinal) AS max_ordinal
                FROM recent
                GROUP BY ticker
            ),
            order_checks AS
            (
                SELECT
                    ticker,
                    countIf(
                        has_prev = 1
                        AND tuple(sip_timestamp_us, source_sequence, event_type, arrival_sequence)
                            < tuple(prev_sip_timestamp_us, prev_source_sequence, prev_event_type, prev_arrival_sequence)
                    ) AS order_errors
                FROM
                (
                    SELECT
                        ticker,
                        ordinal,
                        sip_timestamp_us,
                        source_sequence,
                        event_type,
                        arrival_sequence,
                        lagInFrame(sip_timestamp_us, 1, sip_timestamp_us) OVER (PARTITION BY ticker ORDER BY ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_sip_timestamp_us,
                        lagInFrame(source_sequence, 1, source_sequence) OVER (PARTITION BY ticker ORDER BY ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_source_sequence,
                        lagInFrame(event_type, 1, event_type) OVER (PARTITION BY ticker ORDER BY ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_event_type,
                        lagInFrame(arrival_sequence, 1, arrival_sequence) OVER (PARTITION BY ticker ORDER BY ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_arrival_sequence,
                        row_number() OVER (PARTITION BY ticker ORDER BY ordinal) > 1 AS has_prev
                    FROM recent
                )
                GROUP BY ticker
            )
            SELECT
                (SELECT count() FROM recent) AS recent_rows,
                (SELECT uniqExact(ticker) FROM recent) AS ticker_count,
                (SELECT count() - uniqExact(ticker, ordinal) FROM recent) AS duplicate_ticker_ordinal_rows,
                (SELECT count() FROM ticker_ranges WHERE rows != max_ordinal - min_ordinal + 1) AS hole_ticker_count,
                (SELECT count() FROM order_checks WHERE order_errors > 0) AS out_of_order_ticker_count
            FORMAT JSONEachRow
            "#,
            table = table,
            start_date = start_date,
            start_us = start_us,
        );
        let text = self.query(&sql, true).await?;
        let Some(line) = text.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(LiveEventAudit::default());
        };
        let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
        Ok(LiveEventAudit {
            duplicate_ticker_ordinal_rows: value
                .get("duplicate_ticker_ordinal_rows")
                .and_then(json_u64)
                .unwrap_or(0),
            hole_ticker_count: value
                .get("hole_ticker_count")
                .and_then(json_u64)
                .unwrap_or(0),
            out_of_order_ticker_count: value
                .get("out_of_order_ticker_count")
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
        record_up_to_date: bool,
    ) -> Result<(), String> {
        let Some(target_end) = self.historical_safe_target_date() else {
            return Ok(());
        };
        let latest = self
            .latest_historical_event_date()
            .await
            .unwrap_or_else(|error| {
                eprintln!("Historical flatfile coverage query failed: {error}");
                self.config.historical_known_coverage_end_date.clone()
            });
        if latest >= target_end.to_string() {
            eprintln!(
                "Historical flatfile coverage is up to date: latest={} target={}",
                latest, target_end
            );
            if record_up_to_date {
                let host_role = self.host_role();
                self.record_coverage_run(
                    started_at,
                    "historical_flatfile_events",
                    "up_to_date",
                    date_start_utc(target_end),
                    date_start_utc(target_end) + ChronoDuration::days(1),
                    mode,
                    0,
                    &host_role,
                    "",
                    &json!({
                        "latest_historical_event_date": latest,
                        "target_end_date": target_end.to_string(),
                        "autorun": self.config.historical_flatfile_autorun,
                        "message": "historical flatfile coverage is current through the safe target date",
                    }),
                )
                .await?;
            }
            return Ok(());
        }
        let Some(start_date) = next_date(&latest) else {
            return Ok(());
        };
        let command =
            self.historical_update_command(&start_date.to_string(), &target_end.to_string());
        eprintln!("Historical flatfile update needed: {start_date} to {target_end}");
        eprintln!("Run command: {command}");
        let mut status = "planned";
        let host_role = self.host_role();
        if host_role == "workstation" && self.config.historical_flatfile_autorun {
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
            }),
        )
        .await
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
        self.query(
            &self.create_event_coverage_table_sql(&self.config.qmd_flatfile_event_coverage_table),
            true,
        )
        .await
        .map(|_| ())?;
        self.ensure_current_live_coverage_open(Utc::now()).await?;
        self.bootstrap_flatfile_event_coverage().await
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

    async fn bootstrap_flatfile_event_coverage(&self) -> Result<(), String> {
        let sql = format!(
            "SELECT count() FROM {} FINAL WHERE coverage_kind = 'flatfile_events' FORMAT TSV",
            self.config.qmd_flatfile_event_coverage_table
        );
        let existing = self
            .query(&sql, true)
            .await
            .ok()
            .and_then(|text| text.trim().parse::<u64>().ok())
            .unwrap_or(0);
        if existing > 0 {
            return Ok(());
        }
        let latest = self.latest_historical_event_date().await?;
        let Some(latest_date) = NaiveDate::parse_from_str(&latest, "%Y-%m-%d").ok() else {
            return Err(format!(
                "could not parse latest historical event source_date '{latest}'"
            ));
        };
        let Some(start_date) = NaiveDate::from_ymd_opt(2019, 1, 1) else {
            return Err("could not construct 2019-01-01 flatfile coverage start".to_string());
        };
        if latest_date < start_date {
            return Ok(());
        }
        let coverage_start = date_start_utc(start_date);
        let coverage_end = date_start_utc(latest_date) + ChronoDuration::days(1);
        let now = Utc::now();
        self.record_event_coverage_snapshot(
            &self.config.qmd_flatfile_event_coverage_table,
            "flatfile_events",
            "flatfile_bootstrap_2019_forward",
            "market_sip_compact",
            "coverage_bootstrap",
            coverage_start,
            coverage_end,
            0,
            0,
            0,
            0,
            now,
            Some(now),
            &json!({
                "historical_database": self.config.historical_clickhouse_database,
                "latest_historical_event_date": latest,
                "coverage_rule": "source-of-truth bootstrap from market_sip_compact, no lookback before 2019",
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
            "SELECT max(source_date) FROM {}.events_ordinal_continuity FORMAT TSV",
            self.config.historical_clickhouse_database.replace('`', "")
        );
        let value = self.query_historical(&sql).await?.trim().to_string();
        if value.is_empty() || value == "0000-00-00" {
            Ok(self.config.historical_known_coverage_end_date.clone())
        } else {
            Ok(value)
        }
    }

    fn historical_update_command(&self, start_date: &str, end_date: &str) -> String {
        format!(
            "python {}\\pipelines\\market_sip\\flatfiles\\download_update_events.py --database {} --start-date {} --end-date {}",
            self.config.historical_pipeline_code_root,
            self.config.historical_clickhouse_database,
            start_date,
            end_date,
        )
    }

    fn historical_safe_target_date(&self) -> Option<NaiveDate> {
        let today = Utc::now().date_naive();
        let lag = self.config.historical_flatfile_safe_lag_days.max(1);
        Some(previous_market_session(today - ChronoDuration::days(lag)))
    }

    fn host_role(&self) -> String {
        if self.config.qmd_host_role != "auto" {
            return self.config.qmd_host_role.clone();
        }
        let computer = std::env::var("COMPUTERNAME")
            .or_else(|_| std::env::var("HOSTNAME"))
            .unwrap_or_default()
            .to_ascii_uppercase();
        let pipeline_root_exists = FsPath::new(&self.config.historical_pipeline_code_root).exists();
        if computer.contains("DESKTOP-SAAI85T")
            || (pipeline_root_exists
                && self
                    .config
                    .historical_pipeline_code_root
                    .to_ascii_lowercase()
                    .starts_with("d:\\tradingml"))
        {
            "workstation".to_string()
        } else {
            "laptop".to_string()
        }
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

fn previous_market_session(mut date: NaiveDate) -> NaiveDate {
    while !is_market_session_date(date) {
        date -= ChronoDuration::days(1);
    }
    date
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

fn market_session_window_utc(
    market_date: NaiveDate,
    now: DateTime<Utc>,
) -> Option<(DateTime<Utc>, DateTime<Utc>)> {
    if !is_market_session_date(market_date) {
        return None;
    }
    let start = New_York
        .with_ymd_and_hms(
            market_date.year(),
            market_date.month(),
            market_date.day(),
            4,
            0,
            0,
        )
        .single()?
        .with_timezone(&Utc);
    let scheduled_end = New_York
        .with_ymd_and_hms(
            market_date.year(),
            market_date.month(),
            market_date.day(),
            20,
            0,
            0,
        )
        .single()?
        .with_timezone(&Utc);
    if now <= start {
        return None;
    }
    let end = if now < scheduled_end {
        now
    } else {
        scheduled_end
    };
    if end <= start {
        None
    } else {
        Some((start, end))
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

fn is_market_session_date(date: NaiveDate) -> bool {
    date.weekday().number_from_monday() <= 5 && !is_us_market_holiday(date)
}

fn is_us_market_holiday(date: NaiveDate) -> bool {
    let year = date.year();
    fixed_holiday_observed(date, year, 1, 1)
        || fixed_holiday_observed(date, year + 1, 1, 1)
        || nth_weekday(year, 1, Weekday::Mon, 3) == Some(date)
        || nth_weekday(year, 2, Weekday::Mon, 3) == Some(date)
        || Some(date) == easter_sunday(year).map(|day| day - ChronoDuration::days(2))
        || last_weekday(year, 5, Weekday::Mon) == Some(date)
        || (year >= 2022 && fixed_holiday_observed(date, year, 6, 19))
        || fixed_holiday_observed(date, year, 7, 4)
        || nth_weekday(year, 9, Weekday::Mon, 1) == Some(date)
        || nth_weekday(year, 11, Weekday::Thu, 4) == Some(date)
        || fixed_holiday_observed(date, year, 12, 25)
}

fn fixed_holiday_observed(date: NaiveDate, year: i32, month: u32, day: u32) -> bool {
    let Some(actual) = NaiveDate::from_ymd_opt(year, month, day) else {
        return false;
    };
    let observed = match actual.weekday() {
        Weekday::Sat => actual - ChronoDuration::days(1),
        Weekday::Sun => actual + ChronoDuration::days(1),
        _ => actual,
    };
    date == observed
}

fn nth_weekday(year: i32, month: u32, weekday: Weekday, nth: u32) -> Option<NaiveDate> {
    let mut date = NaiveDate::from_ymd_opt(year, month, 1)?;
    while date.weekday() != weekday {
        date += ChronoDuration::days(1);
    }
    Some(date + ChronoDuration::days((nth.saturating_sub(1) * 7) as i64))
        .filter(|value| value.month() == month)
}

fn last_weekday(year: i32, month: u32, weekday: Weekday) -> Option<NaiveDate> {
    let next_month = if month == 12 {
        NaiveDate::from_ymd_opt(year + 1, 1, 1)?
    } else {
        NaiveDate::from_ymd_opt(year, month + 1, 1)?
    };
    let mut date = next_month - ChronoDuration::days(1);
    while date.weekday() != weekday {
        date -= ChronoDuration::days(1);
    }
    Some(date)
}

fn easter_sunday(year: i32) -> Option<NaiveDate> {
    let a = year % 19;
    let b = year / 100;
    let c = year % 100;
    let d = b / 4;
    let e = b % 4;
    let f = (b + 8) / 25;
    let g = (b - f + 1) / 3;
    let h = (19 * a + b - d - g + 15) % 30;
    let i = c / 4;
    let k = c % 4;
    let l = (32 + 2 * e + 2 * i - h - k) % 7;
    let m = (a + 11 * h + 22 * l) / 451;
    let month = (h + l - 7 * m + 114) / 31;
    let day = ((h + l - 7 * m + 114) % 31) + 1;
    NaiveDate::from_ymd_opt(year, month as u32, day as u32)
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

fn append_api_key(url: &str, api_key: &str) -> String {
    if url.contains("apiKey=") {
        url.to_string()
    } else if url.contains('?') {
        format!("{url}&apiKey={}", urlencoding::encode(api_key))
    } else {
        format!("{url}?apiKey={}", urlencoding::encode(api_key))
    }
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
