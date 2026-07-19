use crate::cache::{
    CacheEvidence, CacheMetrics, ChartSnapshot, DerivedSnapshot, HistoricalDerivedCache,
    HISTORICAL_ENGINE_VERSION,
};
use crate::config::HistoricalGatewayConfig;
use crate::source::{
    EventCoverage, EventWindow, HistoricalCursor, HistoricalEventSource, LatestEventCoverage,
};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::get;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use futures_util::SinkExt;
use qmd_core::bars::is_supported_timeframe;
use qmd_core::compact_event::LiveCompactEvent;
use qmd_core::market_products::{
    parse_resolution_us, ConditionBarSnapshot, FamilyBarSnapshot, MacroBarSnapshot,
};
use qmd_core::microstructure_forecast::{forecast_compact_events, MicrostructureForecastSnapshot};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::sync::Arc;
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct AppState {
    pub cache: HistoricalDerivedCache,
    pub config: HistoricalGatewayConfig,
    pub source: HistoricalEventSource,
}

#[derive(Debug, Deserialize)]
struct HistoryQuery {
    end: String,
    limit: Option<usize>,
    start: String,
    tail: Option<bool>,
}

#[derive(Debug, Deserialize)]
struct LatestCoverageQuery {
    before: Option<String>,
}

#[derive(Debug, Deserialize)]
struct BarsQuery {
    end: String,
    event_limit: Option<usize>,
    limit: Option<usize>,
    start: String,
    timeframe: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ChartQuery {
    as_of: Option<String>,
    before: Option<String>,
    end: String,
    indicator_columns: Option<String>,
    limit: Option<usize>,
    start: String,
    timeframe: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ProductQuery {
    as_of: Option<String>,
    end: String,
    limit: Option<usize>,
    resolution: Option<String>,
    start: String,
    timeframe: Option<String>,
}

#[derive(Debug, Deserialize)]
struct StreamQuery {
    batch_size: Option<usize>,
    end: String,
    start: String,
    timeframe: Option<String>,
    tickers: Option<String>,
}

#[derive(Debug, Deserialize)]
struct DerivedStreamQuery {
    after_sequence: Option<u64>,
    as_of: Option<String>,
    end: String,
    emit: Option<String>,
    max_updates: Option<u64>,
    start: String,
    timeframe: Option<String>,
    updates_per_second: Option<f64>,
}

#[derive(Debug, Serialize)]
struct HealthPayload {
    cache: CacheMetrics,
    config: HistoricalGatewayConfig,
    host_role: &'static str,
    running: bool,
    service: &'static str,
    source: &'static str,
    status: &'static str,
}

type ApiError = (StatusCode, Json<Value>);

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/config", get(config))
        .route("/coverage", get(coverage))
        .route("/coverage/latest", get(latest_coverage))
        .route("/snapshot/cache", get(cache_snapshot))
        .route(
            "/snapshot/compact-events/{ticker}",
            get(compact_event_snapshot),
        )
        .route(
            "/snapshot/microstructure-forecast/{ticker}",
            get(microstructure_forecast_snapshot),
        )
        .route("/snapshot/bars/{ticker}", get(bar_snapshot))
        .route("/snapshot/chart-bars/{ticker}", get(chart_bar_snapshot))
        .route(
            "/snapshot/chart-macro-bars/{ticker}",
            get(chart_macro_bar_snapshot),
        )
        .route("/snapshot/family-bars/{ticker}", get(family_bar_snapshot))
        .route(
            "/snapshot/condition-bars/{ticker}",
            get(condition_bar_snapshot),
        )
        .route("/snapshot/macro-bars/{ticker}", get(macro_bar_snapshot))
        .route("/stream/compact-events", get(compact_event_stream))
        .route("/stream/events", get(event_stream))
        .route("/stream/bars/{ticker}", get(bar_stream))
        .route("/stream/indicators/{ticker}", get(indicator_stream))
        .route("/stream/derived/{ticker}", get(derived_stream))
        .layer(CorsLayer::permissive())
        .with_state(Arc::new(state))
}

async fn health(State(state): State<Arc<AppState>>) -> Result<Json<HealthPayload>, ApiError> {
    state.source.health().await.map_err(service_error)?;
    Ok(Json(HealthPayload {
        cache: state.cache.metrics().await,
        config: state.config.clone(),
        host_role: "historical",
        running: true,
        service: "qmd_history_gateway",
        source: "market_sip_compact.events_YYYY",
        status: "ready",
    }))
}

async fn cache_snapshot(State(state): State<Arc<AppState>>) -> Json<CacheMetrics> {
    Json(state.cache.metrics().await)
}

async fn config(State(state): State<Arc<AppState>>) -> Json<HistoricalGatewayConfig> {
    Json(state.config.clone())
}

async fn coverage(
    Query(query): Query<HistoryQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<EventCoverage>, ApiError> {
    let window = window(&query.start, &query.end, Vec::new())?;
    state
        .source
        .coverage(&window)
        .await
        .map(Json)
        .map_err(service_error)
}

async fn latest_coverage(
    Query(query): Query<LatestCoverageQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<LatestEventCoverage>, ApiError> {
    let before = query
        .before
        .as_deref()
        .map(|value| {
            chrono::NaiveDate::parse_from_str(value, "%Y-%m-%d")
                .map_err(|_| bad_request("before must be an ISO date"))
        })
        .transpose()?;
    state
        .source
        .latest_coverage_before(before)
        .await
        .map(Json)
        .map_err(service_error)
}

async fn compact_event_snapshot(
    Path(ticker): Path<String>,
    Query(query): Query<HistoryQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<Vec<LiveCompactEvent>>, ApiError> {
    let window = window(&query.start, &query.end, vec![ticker])?;
    let limit = query
        .limit
        .unwrap_or(state.config.batch_size)
        .clamp(1, 100_000);
    let events = if query.tail.unwrap_or(false) {
        state.source.fetch_latest(&window, limit).await
    } else {
        state
            .source
            .fetch_batch(&window, None, limit)
            .await
            .map(|(events, _)| events)
    }
    .map_err(service_error)?;
    Ok(Json(events))
}

async fn microstructure_forecast_snapshot(
    Path(ticker): Path<String>,
    Query(query): Query<HistoryQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<MicrostructureForecastSnapshot>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let window = window(&query.start, &query.end, vec![ticker])?;
    let events = state
        .source
        .fetch_latest(&window, query.limit.unwrap_or(5_000).clamp(500, 100_000))
        .await
        .map_err(service_error)?;
    Ok(Json(forecast_compact_events(
        &events,
        &state.source.decoder(),
        &state.source.trade_aggregation_rules(),
        "qmd-history-gateway",
    )))
}

async fn bar_snapshot(
    Path(ticker): Path<String>,
    Query(query): Query<BarsQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<DerivedSnapshot>, ApiError> {
    let window = window(&query.start, &query.end, vec![ticker.clone()])?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    validate_timeframe(&timeframe)?;
    let bar_limit = query.limit.unwrap_or(1_000).clamp(1, 100_000);
    let _legacy_event_limit = query.event_limit;
    state
        .cache
        .snapshot(window, ticker, timeframe, bar_limit)
        .await
        .map(Json)
        .map_err(service_error)
}

async fn chart_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ChartQuery>,
) -> Result<Json<Value>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    if !state
        .config
        .product_timeframes
        .iter()
        .any(|candidate| candidate.eq_ignore_ascii_case(&timeframe))
    {
        return Err(bad_request(format!(
            "unsupported chart timeframe {timeframe}; configured values are {}",
            state.config.product_timeframes.join(", ")
        )));
    }
    let product_query = ProductQuery {
        as_of: query.as_of,
        end: query.end,
        limit: query.limit,
        resolution: Some(timeframe.clone()),
        start: query.start,
        timeframe: None,
    };
    let (window, as_of) = causal_product_window(&product_query, &ticker)?;
    let before = query.before.as_deref().map(parse_timestamp).transpose()?;
    let indicator_columns = parse_indicator_projection(query.indicator_columns.as_deref())?;
    let snapshot = state
        .cache
        .chart_snapshot(
            window,
            ticker,
            timeframe,
            product_query.limit.unwrap_or(5_000).clamp(1, 50_000),
            as_of,
            before,
        )
        .await
        .map_err(service_error)?;
    project_chart_snapshot(snapshot, indicator_columns.as_ref()).map(Json)
}

fn parse_indicator_projection(raw: Option<&str>) -> Result<Option<BTreeSet<String>>, ApiError> {
    let Some(raw) = raw else {
        return Ok(None);
    };
    let mut columns = BTreeSet::from(["bar_start".to_string()]);
    for column in raw
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        if column.len() > 64
            || !column
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        {
            return Err(bad_request(format!("invalid indicator column {column}")));
        }
        columns.insert(column.to_string());
        if columns.len() > 128 {
            return Err(bad_request("too many projected indicator columns"));
        }
    }
    Ok(Some(columns))
}

fn project_chart_snapshot(
    snapshot: ChartSnapshot,
    columns: Option<&BTreeSet<String>>,
) -> Result<Value, ApiError> {
    let Some(columns) = columns else {
        return serde_json::to_value(snapshot).map_err(|error| {
            service_error(format!("failed to serialize chart snapshot: {error}"))
        });
    };
    let indicators = snapshot
        .indicators
        .into_iter()
        .map(|indicator| {
            let mut value = serde_json::to_value(indicator).map_err(|error| {
                service_error(format!("failed to serialize chart indicator: {error}"))
            })?;
            if let Some(object) = value.as_object_mut() {
                object.retain(|key, _| columns.contains(key));
            }
            Ok(value)
        })
        .collect::<Result<Vec<_>, ApiError>>()?;
    Ok(json!({
        "as_of": snapshot.as_of,
        "bars": snapshot.bars,
        "cache": snapshot.cache,
        "has_more": snapshot.has_more,
        "indicators": indicators,
        "indicators_available": snapshot.indicators_available,
        "next_before": snapshot.next_before,
        "ticker": snapshot.ticker,
        "timeframe": snapshot.timeframe,
    }))
}

async fn family_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Result<Json<FamilyBarSnapshot>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let (product_window, as_of) = causal_product_window(&query, &ticker)?;
    let resolution_us = product_resolution(&query)?;
    state
        .cache
        .family_snapshot(
            product_window,
            ticker,
            resolution_us,
            query
                .limit
                .unwrap_or(10_000)
                .min(state.config.product_cache_max_rows_per_entry),
            as_of,
        )
        .await
        .map(Json)
        .map_err(service_error)
}

async fn condition_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Result<Json<ConditionBarSnapshot>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let (product_window, as_of) = causal_product_window(&query, &ticker)?;
    let resolution_us = product_resolution(&query)?;
    state
        .cache
        .condition_snapshot(
            product_window,
            ticker,
            resolution_us,
            query
                .limit
                .unwrap_or(10_000)
                .min(state.config.product_cache_max_rows_per_entry),
            as_of,
        )
        .await
        .map(Json)
        .map_err(service_error)
}

async fn macro_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Result<Json<MacroBarSnapshot>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let (product_window, as_of) = causal_product_window(&query, &ticker)?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1d".to_string());
    if !matches!(timeframe.as_str(), "1d" | "1w" | "1mo" | "1y") {
        return Err(bad_request("macro timeframe must be 1d, 1w, 1mo, or 1y"));
    }
    state
        .cache
        .macro_snapshot(
            product_window,
            ticker,
            timeframe,
            query.limit.unwrap_or(1_000).min(10_000),
            as_of,
        )
        .await
        .map(Json)
        .map_err(service_error)
}

async fn chart_macro_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Result<Json<crate::source::HistoricalMacroChartSnapshot>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let (window, as_of) = causal_product_window(&query, &ticker)?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1d".to_string());
    if !matches!(timeframe.as_str(), "1d" | "1mo") {
        return Err(bad_request("chart macro timeframe must be 1d or 1mo"));
    }
    state
        .source
        .chart_macro_bars(&window, &ticker, &timeframe, as_of)
        .await
        .map(Json)
        .map_err(service_error)
}

fn causal_product_window(
    query: &ProductQuery,
    ticker: &str,
) -> Result<(EventWindow, DateTime<Utc>), ApiError> {
    let mut product_window = window(&query.start, &query.end, vec![ticker.to_string()])?;
    let as_of = query
        .as_of
        .as_deref()
        .map(parse_timestamp)
        .transpose()?
        .unwrap_or(product_window.end);
    if as_of <= product_window.start {
        return Err(bad_request("as_of must be after start"));
    }
    product_window.end = product_window.end.min(as_of);
    Ok((product_window, as_of))
}

fn product_resolution(query: &ProductQuery) -> Result<u64, ApiError> {
    match query.resolution.as_deref() {
        Some(value) => parse_resolution_us(value)
            .filter(|resolution| *resolution > 0)
            .ok_or_else(|| {
                bad_request("resolution must be a positive duration such as 100ms, 1s, or 1m")
            }),
        None => Ok(60_000_000),
    }
}

async fn compact_event_stream(
    websocket: WebSocketUpgrade,
    Query(query): Query<StreamQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, ApiError> {
    let window = stream_window(&query)?;
    let batch_size = query
        .batch_size
        .unwrap_or(state.config.batch_size)
        .clamp(1, 100_000);
    Ok(websocket
        .on_upgrade(move |socket| stream_compact(socket, state.source.clone(), window, batch_size)))
}

async fn event_stream(
    websocket: WebSocketUpgrade,
    Query(query): Query<StreamQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, ApiError> {
    let window = stream_window(&query)?;
    let batch_size = query
        .batch_size
        .unwrap_or(state.config.batch_size)
        .clamp(1, 100_000);
    Ok(websocket.on_upgrade(move |socket| {
        stream_market_events(socket, state.source.clone(), window, batch_size)
    }))
}

async fn bar_stream(
    Path(ticker): Path<String>,
    websocket: WebSocketUpgrade,
    Query(query): Query<StreamQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, ApiError> {
    let mut window = stream_window(&query)?;
    window.tickers = vec![ticker];
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    validate_timeframe(&timeframe)?;
    let cache = state.cache.clone();
    Ok(websocket.on_upgrade(move |socket| stream_cached_bars(socket, cache, window, timeframe)))
}

async fn derived_stream(
    Path(ticker): Path<String>,
    websocket: WebSocketUpgrade,
    Query(query): Query<DerivedStreamQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, ApiError> {
    let window = window(&query.start, &query.end, vec![ticker.clone()])?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    validate_timeframe(&timeframe)?;
    let emit = query.emit.unwrap_or_else(|| "updates".to_string());
    if !matches!(emit.as_str(), "full" | "updates" | "full_then_updates") {
        return Err(bad_request(
            "emit must be full, updates, or full_then_updates",
        ));
    }
    let as_of = query
        .as_of
        .as_deref()
        .map(parse_timestamp)
        .transpose()?
        .unwrap_or(window.start);
    if as_of < window.start || as_of > window.end {
        return Err(bad_request("as_of must be inside the requested window"));
    }
    let updates_per_second = query.updates_per_second.unwrap_or(0.0);
    if !updates_per_second.is_finite() || !(0.0..=10_000.0).contains(&updates_per_second) {
        return Err(bad_request(
            "updates_per_second must be between 0 and 10000; zero means unthrottled fast-forward",
        ));
    }
    if query.max_updates.is_some_and(|value| value == 0) {
        return Err(bad_request("max_updates must be greater than zero"));
    }
    let cache = state.cache.clone();
    Ok(websocket.on_upgrade(move |socket| {
        stream_derived(
            socket,
            cache,
            window,
            ticker,
            timeframe,
            emit,
            as_of,
            query.after_sequence.unwrap_or(0),
            query.max_updates,
            updates_per_second,
        )
    }))
}

async fn indicator_stream(
    Path(ticker): Path<String>,
    websocket: WebSocketUpgrade,
    Query(query): Query<StreamQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, ApiError> {
    let mut window = stream_window(&query)?;
    window.tickers = vec![ticker];
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    validate_timeframe(&timeframe)?;
    let cache = state.cache.clone();
    Ok(websocket
        .on_upgrade(move |socket| stream_cached_indicators(socket, cache, window, timeframe)))
}

async fn stream_compact(
    mut socket: WebSocket,
    source: HistoricalEventSource,
    window: EventWindow,
    batch_size: usize,
) {
    let mut cursor: Option<HistoricalCursor> = None;
    loop {
        let (events, next) = match source
            .fetch_batch(&window, cursor.as_ref(), batch_size)
            .await
        {
            Ok(result) => result,
            Err(error) => {
                send_stream_error(&mut socket, error).await;
                return;
            }
        };
        for event in &events {
            if send_json(&mut socket, event).await.is_err() {
                return;
            }
        }
        if events.len() < batch_size || next.is_none() {
            let _ = socket.close().await;
            return;
        }
        cursor = next;
    }
}

async fn stream_market_events(
    mut socket: WebSocket,
    source: HistoricalEventSource,
    window: EventWindow,
    batch_size: usize,
) {
    let mut cursor: Option<HistoricalCursor> = None;
    loop {
        let (events, next) = match source
            .fetch_batch(&window, cursor.as_ref(), batch_size)
            .await
        {
            Ok(result) => result,
            Err(error) => {
                send_stream_error(&mut socket, error).await;
                return;
            }
        };
        for event in &events {
            if send_json(&mut socket, &source.market_event(event))
                .await
                .is_err()
            {
                return;
            }
        }
        if events.len() < batch_size || next.is_none() {
            let _ = socket.close().await;
            return;
        }
        cursor = next;
    }
}

async fn stream_cached_bars(
    mut socket: WebSocket,
    cache: HistoricalDerivedCache,
    window: EventWindow,
    timeframe: String,
) {
    let ticker = window.tickers[0].clone();
    let lease = match cache
        .acquire_derived(window, ticker, timeframe.clone())
        .await
    {
        Ok(lease) => lease,
        Err(error) => {
            send_stream_error(&mut socket, error).await;
            return;
        }
    };
    let mut receiver = lease.entry.subscribe_bars();
    let mut last_sequence = 0;
    loop {
        let (frames, complete, error, _) = lease.entry.current_bars().await;
        if let Some(error) = error {
            send_stream_error(&mut socket, error).await;
            return;
        }
        for frame in &frames {
            if frame.sequence <= last_sequence {
                continue;
            }
            if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
                && send_json(&mut socket, &frame.bar).await.is_err()
            {
                return;
            }
            last_sequence = frame.sequence;
        }
        if complete {
            let _ = socket.close().await;
            return;
        }
        match receiver.recv().await {
            Ok(frame) if frame.sequence > last_sequence => {
                if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe) {
                    if send_json(&mut socket, &frame.bar).await.is_err() {
                        return;
                    }
                }
                last_sequence = frame.sequence;
            }
            Ok(_) | Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {}
            Err(tokio::sync::broadcast::error::RecvError::Closed) => return,
        }
    }
}

async fn stream_cached_indicators(
    mut socket: WebSocket,
    cache: HistoricalDerivedCache,
    window: EventWindow,
    timeframe: String,
) {
    let ticker = window.tickers[0].clone();
    let lease = match cache
        .acquire_derived(window, ticker, timeframe.clone())
        .await
    {
        Ok(lease) => lease,
        Err(error) => {
            send_stream_error(&mut socket, error).await;
            return;
        }
    };
    let mut receiver = lease.entry.subscribe();
    let mut last_sequence = 0;
    loop {
        let (frames, complete, error, _) = lease.entry.current().await;
        if let Some(error) = error {
            send_stream_error(&mut socket, error).await;
            return;
        }
        for frame in &frames {
            if frame.sequence <= last_sequence {
                continue;
            }
            if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
                && send_json(&mut socket, &frame.indicator).await.is_err()
            {
                return;
            }
            last_sequence = frame.sequence;
        }
        if complete {
            let _ = socket.close().await;
            return;
        }
        match receiver.recv().await {
            Ok(frame) if frame.sequence > last_sequence => {
                if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
                    && send_json(&mut socket, &frame.indicator).await.is_err()
                {
                    return;
                }
                last_sequence = frame.sequence;
            }
            Ok(_) | Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {}
            Err(tokio::sync::broadcast::error::RecvError::Closed) => return,
        }
    }
}

#[allow(clippy::too_many_arguments)]
async fn stream_derived(
    mut socket: WebSocket,
    cache: HistoricalDerivedCache,
    window: EventWindow,
    ticker: String,
    timeframe: String,
    emit: String,
    as_of: DateTime<Utc>,
    after_sequence: u64,
    max_updates: Option<u64>,
    updates_per_second: f64,
) {
    let lease = match cache
        .acquire_derived(window, ticker.clone(), timeframe.clone())
        .await
    {
        Ok(lease) => lease,
        Err(error) => {
            send_stream_error(&mut socket, error).await;
            return;
        }
    };

    if emit == "full" || emit == "full_then_updates" {
        let (frames, _, error, events_processed) = loop {
            let state = lease.entry.current().await;
            if state.1 {
                break state;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        };
        if let Some(error) = error {
            send_stream_error(&mut socket, error).await;
            return;
        }
        let visible = frames
            .iter()
            .filter(|frame| {
                frame.as_of <= as_of && frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
            })
            .cloned()
            .collect::<Vec<_>>();
        let full = FullDerivedEnvelope {
            as_of,
            bars: visible.iter().map(|frame| frame.bar.clone()).collect(),
            cache: CacheEvidence {
                engine_version: HISTORICAL_ENGINE_VERSION,
                event_count: events_processed,
                hit: lease.hit,
                source_revision: lease.source_revision.clone(),
            },
            indicators: visible
                .iter()
                .map(|frame| frame.indicator.clone())
                .collect(),
            next_sequence: visible.last().map_or(0, |frame| frame.sequence),
            ticker: ticker.clone(),
            timeframe: timeframe.clone(),
            update_type: "full",
        };
        if send_json(&mut socket, &full).await.is_err() {
            return;
        }
        if emit == "full" {
            let _ = socket.close().await;
            return;
        }
    }

    let mut receiver = lease.entry.subscribe();
    let mut last_sequence = after_sequence;
    let mut updates_sent = 0_u64;
    if emit == "full_then_updates" {
        let (frames, _, _, _) = lease.entry.current().await;
        last_sequence = last_sequence.max(
            frames
                .iter()
                .filter(|frame| frame.as_of <= as_of)
                .map(|frame| frame.sequence)
                .max()
                .unwrap_or(0),
        );
    }
    loop {
        let (frames, complete, error, _) = lease.entry.current().await;
        if let Some(error) = error {
            send_stream_error(&mut socket, error).await;
            return;
        }
        for frame in &frames {
            if frame.sequence <= last_sequence {
                continue;
            }
            if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe) {
                if send_json(&mut socket, frame).await.is_err() {
                    return;
                }
                updates_sent += 1;
            }
            last_sequence = frame.sequence;
            if max_updates.is_some_and(|limit| updates_sent >= limit) {
                let _ = socket.close().await;
                return;
            }
            throttle(updates_per_second).await;
        }
        if complete {
            let _ = socket.close().await;
            return;
        }
        match receiver.recv().await {
            Ok(frame) if frame.sequence > last_sequence => {
                if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe) {
                    if send_json(&mut socket, &frame).await.is_err() {
                        return;
                    }
                    updates_sent += 1;
                }
                last_sequence = frame.sequence;
                if max_updates.is_some_and(|limit| updates_sent >= limit) {
                    let _ = socket.close().await;
                    return;
                }
                throttle(updates_per_second).await;
            }
            Ok(_) | Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {}
            Err(tokio::sync::broadcast::error::RecvError::Closed) => return,
        }
    }
}

#[derive(Serialize)]
struct FullDerivedEnvelope {
    as_of: DateTime<Utc>,
    bars: Vec<qmd_core::bars::BarRow>,
    cache: CacheEvidence,
    indicators: Vec<qmd_core::indicators::IndicatorRow>,
    next_sequence: u64,
    ticker: String,
    timeframe: String,
    #[serde(rename = "type")]
    update_type: &'static str,
}

async fn throttle(updates_per_second: f64) {
    if updates_per_second > 0.0 {
        tokio::time::sleep(std::time::Duration::from_secs_f64(1.0 / updates_per_second)).await;
    }
}

fn window(start: &str, end: &str, tickers: Vec<String>) -> Result<EventWindow, ApiError> {
    let start = parse_timestamp(start)?;
    let end = parse_timestamp(end)?;
    if end <= start {
        return Err(bad_request("end must be later than start"));
    }
    Ok(EventWindow {
        end,
        start,
        tickers,
    })
}

fn stream_window(query: &StreamQuery) -> Result<EventWindow, ApiError> {
    let tickers = query
        .tickers
        .as_deref()
        .unwrap_or_default()
        .split(',')
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .collect();
    window(&query.start, &query.end, tickers)
}

fn parse_timestamp(value: &str) -> Result<DateTime<Utc>, ApiError> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Utc))
        .map_err(|_| bad_request(format!("timestamp must be RFC3339 with timezone: {value}")))
}

fn normalize_ticker(value: &str) -> Result<String, ApiError> {
    let ticker = value.trim().to_ascii_uppercase();
    if ticker.is_empty()
        || ticker.len() > 32
        || !ticker
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '-' | '/'))
    {
        return Err(bad_request("ticker is invalid"));
    }
    Ok(ticker)
}

fn validate_timeframe(value: &str) -> Result<(), ApiError> {
    if is_supported_timeframe(value) {
        Ok(())
    } else {
        Err(bad_request(format!(
            "unsupported timeframe {value}; expected one of 1s, 10s, 30s, 1m, 5m, 1h"
        )))
    }
}

async fn send_json(socket: &mut WebSocket, value: &impl Serialize) -> Result<(), ()> {
    let text = serde_json::to_string(value).map_err(|_| ())?;
    socket
        .send(Message::Text(text.into()))
        .await
        .map_err(|_| ())
}

async fn send_stream_error(socket: &mut WebSocket, message: String) {
    let payload = json!({
        "error": message,
        "source": "historical_clickhouse",
        "terminal": true,
    });
    let _ = send_json(socket, &payload).await;
    let _ = socket.close().await;
}

fn bad_request(message: impl Into<String>) -> ApiError {
    (
        StatusCode::BAD_REQUEST,
        Json(json!({"error": message.into()})),
    )
}

fn service_error(message: String) -> ApiError {
    (
        StatusCode::BAD_GATEWAY,
        Json(json!({"error": message, "source": "historical_clickhouse"})),
    )
}

#[cfg(test)]
mod tests {
    use super::{
        causal_product_window, parse_indicator_projection, parse_timestamp, product_resolution,
        validate_timeframe, ProductQuery,
    };

    #[test]
    fn timestamps_require_explicit_timezone() {
        assert!(parse_timestamp("2026-07-13T04:00:00-04:00").is_ok());
        assert!(parse_timestamp("2026-07-13 04:00:00").is_err());
    }

    #[test]
    fn timeframes_are_validated_by_the_shared_qmd_bar_contract() {
        assert!(validate_timeframe("100ms").is_ok());
        assert!(validate_timeframe("5s").is_ok());
        assert!(validate_timeframe("1m").is_ok());
        assert!(validate_timeframe("2m").is_err());
    }

    #[test]
    fn product_windows_never_build_past_as_of() {
        let query = ProductQuery {
            as_of: Some("2026-07-10T13:44:15Z".to_string()),
            end: "2026-07-10T13:44:30Z".to_string(),
            limit: None,
            resolution: Some("1s".to_string()),
            start: "2026-07-10T13:44:00Z".to_string(),
            timeframe: None,
        };
        let (window, as_of) = causal_product_window(&query, "AAPL").unwrap();
        assert_eq!(window.end, as_of);
        assert_eq!(product_resolution(&query).unwrap(), 1_000_000);
    }

    #[test]
    fn invalid_product_resolution_is_rejected() {
        let query = ProductQuery {
            as_of: None,
            end: "2026-07-10T13:44:30Z".to_string(),
            limit: None,
            resolution: Some("nonsense".to_string()),
            start: "2026-07-10T13:44:00Z".to_string(),
            timeframe: None,
        };
        assert!(product_resolution(&query).is_err());
    }

    #[test]
    fn chart_indicator_projection_is_bounded_and_keeps_the_time_key() {
        let columns = parse_indicator_projection(Some("ema_20,rsi_14,ema_20"))
            .unwrap()
            .unwrap();
        assert_eq!(columns.len(), 3);
        assert!(columns.contains("bar_start"));
        assert!(columns.contains("ema_20"));
        assert!(parse_indicator_projection(Some("ema-20")).is_err());
    }
}
