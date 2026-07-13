use crate::compact_event::LiveCompactEvent;
use crate::config::GatewayConfig;
use crate::metrics::SharedMetrics;
use chrono::{Datelike, Timelike, Utc};
use chrono_tz::America::New_York;
use reqwest::Client;
use serde::Serialize;
use serde_json::json;
use std::collections::HashMap;
use tokio::sync::{broadcast, mpsc};
use tokio::task::JoinHandle;
use tokio::time::{interval, sleep, timeout, Duration};

const SESSION_START_US: i64 = 4 * 60 * 60 * 1_000_000;
const SESSION_END_US: i64 = 20 * 60 * 60 * 1_000_000;

#[derive(Clone)]
pub struct ModelBarRouter {
    senders: Vec<mpsc::Sender<LiveCompactEvent>>,
}

pub struct ModelBarService {
    pub router: ModelBarRouter,
    pub rows: broadcast::Sender<ModelBarRow>,
    tasks: Vec<JoinHandle<()>>,
}

impl ModelBarService {
    pub fn into_tasks(self) -> Vec<JoinHandle<()>> {
        self.tasks
    }
}

impl ModelBarRouter {
    pub async fn send(&self, event: LiveCompactEvent) -> Result<(), ()> {
        let index = stable_hash(&event.ticker) as usize % self.senders.len();
        self.senders[index].send(event).await.map_err(|_| ())
    }
}

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
struct SortKey(u64, u64, u8, u64);

struct EventPoint {
    family: &'static str,
    price: f32,
    size: f32,
}

#[derive(Clone, Serialize)]
pub struct ModelBarRow {
    ticker: String,
    local_date: String,
    label_resolution_us: i64,
    bucket_index: i64,
    bar_family: &'static str,
    open: f32,
    close: f32,
    high: f32,
    low: f32,
    size_sum: f32,
    size_open: f32,
    size_close: f32,
    size_high: f32,
    size_low: f32,
    event_count: u32,
    first_event_timestamp_us: i64,
    last_event_timestamp_us: i64,
    bar_start_session_us: i64,
    bar_end_session_us: i64,
    #[serde(skip)]
    first_key: SortKey,
    #[serde(skip)]
    last_key: SortKey,
}

impl ModelBarRow {
    fn new(
        event: &LiveCompactEvent,
        point: &EventPoint,
        resolution_us: i64,
        bucket: i64,
        local_date: String,
    ) -> Self {
        let key = sort_key(event);
        Self {
            ticker: event.ticker.clone(),
            local_date,
            label_resolution_us: resolution_us,
            bucket_index: bucket,
            bar_family: point.family,
            open: point.price,
            close: point.price,
            high: point.price,
            low: point.price,
            size_sum: point.size,
            size_open: point.size,
            size_close: point.size,
            size_high: point.size,
            size_low: point.size,
            event_count: 1,
            first_event_timestamp_us: event.sip_timestamp_us as i64,
            last_event_timestamp_us: event.sip_timestamp_us as i64,
            bar_start_session_us: bucket * resolution_us,
            bar_end_session_us: (bucket + 1) * resolution_us,
            first_key: key,
            last_key: key,
        }
    }

    fn update(&mut self, event: &LiveCompactEvent, point: &EventPoint) {
        let key = sort_key(event);
        if key < self.first_key {
            self.first_key = key;
            self.open = point.price;
            self.size_open = point.size;
            self.first_event_timestamp_us = event.sip_timestamp_us as i64;
        }
        if key >= self.last_key {
            self.last_key = key;
            self.close = point.price;
            self.size_close = point.size;
            self.last_event_timestamp_us = event.sip_timestamp_us as i64;
        }
        self.high = self.high.max(point.price);
        self.low = self.low.min(point.price);
        self.size_sum += point.size;
        self.size_high = self.size_high.max(point.size);
        self.size_low = self.size_low.min(point.size);
        self.event_count = self.event_count.saturating_add(1);
    }
}

pub async fn spawn_model_bar_service(
    config: GatewayConfig,
    metrics: SharedMetrics,
) -> Result<Option<ModelBarService>, String> {
    if !config.model_streaming_bars_enabled {
        return Ok(None);
    }
    let resolutions = config
        .model_streaming_bar_timeframes
        .iter()
        .map(|value| parse_resolution_us(value))
        .collect::<Result<Vec<_>, _>>()?;
    if resolutions.is_empty() {
        return Err("QMD model streaming bars require at least one timeframe".into());
    }
    let (row_sender, row_receiver) = mpsc::channel(config.model_streaming_bar_channel_capacity);
    let (broadcast_sender, _) = broadcast::channel(10_000);
    let writer = ModelBarWriter::new(config.clone(), metrics);
    if config.model_streaming_bars_persist {
        writer.initialize().await?;
        writer.metrics.set_lane_state(
            "model_microbars",
            "healthy",
            "Model microbar writer initialized; awaiting rows.",
        );
    }
    let mut tasks = vec![tokio::spawn(writer.run(row_receiver))];
    let mut senders = Vec::new();
    let lateness_us = config.compact_event_reorder_lag_ms.saturating_mul(1_000) as i64;
    for _ in 0..config.model_streaming_bar_shard_count.max(1) {
        let (sender, mut receiver) =
            mpsc::channel::<LiveCompactEvent>(config.model_streaming_bar_channel_capacity);
        let output = row_sender.clone();
        let live_rows = broadcast_sender.clone();
        let shard_resolutions = resolutions.clone();
        let shard_lateness_us = lateness_us;
        tasks.push(tokio::spawn(async move {
            let mut bars: HashMap<(String, String, i64, i64, &'static str), ModelBarRow> =
                HashMap::new();
            let mut max_seen: HashMap<(String, String, i64, &'static str), i64> = HashMap::new();
            loop {
                let event = match timeout(Duration::from_millis(100), receiver.recv()).await {
                    Ok(Some(event)) => event,
                    Ok(None) => break,
                    Err(_) => {
                        if flush_wall_ready(&mut bars, &live_rows, &output, shard_lateness_us).await
                        {
                            continue;
                        }
                        return;
                    }
                };
                let Some((local_date, local_session_us)) =
                    local_coordinates(event.sip_timestamp_us)
                else {
                    continue;
                };
                if !(SESSION_START_US..SESSION_END_US).contains(&local_session_us) {
                    continue;
                }
                for point in event_points(&event) {
                    for resolution_us in &shard_resolutions {
                        let bucket = local_session_us.div_euclid(*resolution_us);
                        let series = (
                            event.ticker.clone(),
                            local_date.clone(),
                            *resolution_us,
                            point.family,
                        );
                        max_seen
                            .entry(series.clone())
                            .and_modify(|value| *value = (*value).max(local_session_us))
                            .or_insert(local_session_us);
                        let key = (
                            series.0.clone(),
                            series.1.clone(),
                            series.2,
                            bucket,
                            series.3,
                        );
                        bars.entry(key)
                            .and_modify(|bar| bar.update(&event, &point))
                            .or_insert_with(|| {
                                ModelBarRow::new(
                                    &event,
                                    &point,
                                    *resolution_us,
                                    bucket,
                                    local_date.clone(),
                                )
                            });
                        let watermark = max_seen[&series].saturating_sub(shard_lateness_us);
                        let ready = bars
                            .keys()
                            .filter(|key| {
                                key.0 == series.0
                                    && key.1 == series.1
                                    && key.2 == series.2
                                    && key.4 == series.3
                                    && (key.3 + 1) * *resolution_us <= watermark
                            })
                            .cloned()
                            .collect::<Vec<_>>();
                        for key in ready {
                            if let Some(row) = bars.remove(&key) {
                                let _ = live_rows.send(row.clone());
                                if output.send(row).await.is_err() {
                                    return;
                                }
                            }
                        }
                    }
                }
            }
            let mut remaining = bars.into_values().collect::<Vec<_>>();
            remaining.sort_by_key(|bar| {
                (
                    bar.ticker.clone(),
                    bar.local_date.clone(),
                    bar.label_resolution_us,
                    bar.bucket_index,
                    bar.bar_family,
                )
            });
            for row in remaining {
                let _ = live_rows.send(row.clone());
                if output.send(row).await.is_err() {
                    return;
                }
            }
        }));
        senders.push(sender);
    }
    drop(row_sender);
    Ok(Some(ModelBarService {
        router: ModelBarRouter { senders },
        rows: broadcast_sender,
        tasks,
    }))
}

async fn flush_wall_ready(
    bars: &mut HashMap<(String, String, i64, i64, &'static str), ModelBarRow>,
    live_rows: &broadcast::Sender<ModelBarRow>,
    output: &mpsc::Sender<ModelBarRow>,
    lateness_us: i64,
) -> bool {
    let now_us = Utc::now().timestamp_micros().max(0) as u64;
    let Some((local_date, local_session_us)) = local_coordinates(now_us) else {
        return true;
    };
    let watermark = local_session_us.saturating_sub(lateness_us);
    let mut ready = bars
        .keys()
        .filter(|key| {
            key.1 < local_date || (key.1 == local_date && (key.3 + 1) * key.2 <= watermark)
        })
        .cloned()
        .collect::<Vec<_>>();
    ready.sort();
    for key in ready {
        if let Some(row) = bars.remove(&key) {
            let _ = live_rows.send(row.clone());
            if output.send(row).await.is_err() {
                return false;
            }
        }
    }
    true
}

struct ModelBarWriter {
    client: Client,
    config: GatewayConfig,
    metrics: SharedMetrics,
}

impl ModelBarWriter {
    fn new(config: GatewayConfig, metrics: SharedMetrics) -> Self {
        Self {
            client: Client::new(),
            config,
            metrics,
        }
    }
    async fn initialize(&self) -> Result<(), String> {
        self.query(r#"CREATE TABLE IF NOT EXISTS live_model_microbars
        (
            ticker LowCardinality(String), local_date Date, label_resolution_us Int64,
            bucket_index Int64, bar_family LowCardinality(String),
            open Float32, close Float32, high Float32, low Float32,
            size_sum Float32, size_open Float32, size_close Float32, size_high Float32, size_low Float32,
            event_count UInt32, first_event_timestamp_us Int64, last_event_timestamp_us Int64,
            bar_start_session_us Int64, bar_end_session_us Int64, updated_at_utc DateTime64(3, 'UTC') DEFAULT now64(3)
        ) ENGINE = ReplacingMergeTree(updated_at_utc)
        PARTITION BY local_date ORDER BY (ticker, local_date, label_resolution_us, bucket_index, bar_family)"#).await.map(|_| ())
    }
    async fn run(self, mut receiver: mpsc::Receiver<ModelBarRow>) {
        let mut batch = Vec::with_capacity(self.config.max_clickhouse_batch);
        let mut tick = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                row = receiver.recv() => match row {
                    Some(row) => if self.config.model_streaming_bars_persist { batch.push(row); },
                    None => {
                        while !batch.is_empty() {
                            self.flush(&mut batch).await;
                            if !batch.is_empty() {
                                sleep(Duration::from_millis(250)).await;
                            }
                        }
                        return;
                    }
                },
                _ = tick.tick() => self.flush(&mut batch).await,
            }
            if batch.len() >= self.config.max_clickhouse_batch {
                self.flush(&mut batch).await;
            }
            self.metrics
                .set_lane_pending("model_microbars", (batch.len() + receiver.len()) as u64);
        }
    }
    async fn flush(&self, rows: &mut Vec<ModelBarRow>) {
        if rows.is_empty() {
            return;
        }
        self.metrics
            .set_lane_pending("model_microbars", rows.len() as u64);
        let body = rows.iter().map(|row| json!({
            "ticker": row.ticker, "local_date": row.local_date, "label_resolution_us": row.label_resolution_us,
            "bucket_index": row.bucket_index, "bar_family": row.bar_family,
            "open": row.open, "close": row.close, "high": row.high, "low": row.low,
            "size_sum": row.size_sum, "size_open": row.size_open, "size_close": row.size_close,
            "size_high": row.size_high, "size_low": row.size_low, "event_count": row.event_count,
            "first_event_timestamp_us": row.first_event_timestamp_us, "last_event_timestamp_us": row.last_event_timestamp_us,
            "bar_start_session_us": row.bar_start_session_us, "bar_end_session_us": row.bar_end_session_us,
        }).to_string()).collect::<Vec<_>>().join("\n");
        if let Err(error) = self
            .query(&format!(
                "INSERT INTO live_model_microbars FORMAT JSONEachRow\n{body}"
            ))
            .await
        {
            self.metrics.record_lane_failure("model_microbars", &error);
            eprintln!("Model microbar insert failed: {error}");
            return;
        }
        let count = rows.len() as u64;
        rows.clear();
        self.metrics
            .record_lane_success("model_microbars", count, "Committed model microbars.");
        self.metrics.set_lane_pending("model_microbars", 0);
    }
    async fn query(&self, body: &str) -> Result<String, String> {
        let mut request = self
            .client
            .post(format!(
                "{}/?database={}",
                self.config.clickhouse_url,
                urlencoding::encode(&self.config.clickhouse_database)
            ))
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
}

fn event_points(event: &LiveCompactEvent) -> Vec<EventPoint> {
    let primary_scale = if event.event_meta & 0x02 != 0 {
        10_000.0
    } else {
        100.0
    };
    let secondary_scale = if event.event_meta & 0x04 != 0 {
        10_000.0
    } else {
        100.0
    };
    if event.event_meta & 1 == 1 {
        let price = event.price_primary_int as f32 / primary_scale;
        if price > 0.0 {
            vec![EventPoint {
                family: "trade",
                price,
                size: event.size_primary,
            }]
        } else {
            Vec::new()
        }
    } else {
        let mut out = Vec::new();
        let bid = event.price_secondary_int as f32 / secondary_scale;
        let ask = event.price_primary_int as f32 / primary_scale;
        if bid > 0.0 {
            out.push(EventPoint {
                family: "quote_bid",
                price: bid,
                size: event.size_secondary,
            });
        }
        if ask > 0.0 {
            out.push(EventPoint {
                family: "quote_ask",
                price: ask,
                size: event.size_primary,
            });
        }
        out
    }
}

fn local_coordinates(sip_timestamp_us: u64) -> Option<(String, i64)> {
    let seconds = (sip_timestamp_us / 1_000_000) as i64;
    let nanos = ((sip_timestamp_us % 1_000_000) * 1_000) as u32;
    let local = chrono::DateTime::from_timestamp(seconds, nanos)?.with_timezone(&New_York);
    let date = format!(
        "{:04}-{:02}-{:02}",
        local.year(),
        local.month(),
        local.day()
    );
    let session_us = ((local.hour() * 3600 + local.minute() * 60 + local.second()) as i64)
        * 1_000_000
        + local.timestamp_subsec_micros() as i64;
    Some((date, session_us))
}

fn sort_key(event: &LiveCompactEvent) -> SortKey {
    SortKey(
        event.sip_timestamp_us,
        event.source_sequence,
        event.event_meta & 1,
        event.arrival_sequence,
    )
}

fn parse_resolution_us(value: &str) -> Result<i64, String> {
    let value = value.trim().to_ascii_lowercase();
    if let Some(raw) = value.strip_suffix("ms") {
        return raw
            .parse::<i64>()
            .ok()
            .filter(|value| *value > 0)
            .map(|value| value * 1_000)
            .ok_or_else(|| format!("invalid model bar timeframe: {value}"));
    }
    if let Some(raw) = value.strip_suffix('s') {
        return raw
            .parse::<i64>()
            .ok()
            .filter(|value| *value > 0)
            .map(|value| value * 1_000_000)
            .ok_or_else(|| format!("invalid model bar timeframe: {value}"));
    }
    Err(format!("model bar timeframe must end in ms or s: {value}"))
}

fn stable_hash(value: &str) -> u64 {
    value
        .bytes()
        .fold(14_695_981_039_346_656_037u64, |hash, byte| {
            (hash ^ byte as u64).wrapping_mul(1_099_511_628_211)
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};

    #[test]
    fn quote_points_match_training_families_and_scales() {
        let event = LiveCompactEvent {
            arrival_sequence: 1,
            condition_token_1: 0,
            condition_token_2: 0,
            condition_token_3: 0,
            condition_token_4: 0,
            condition_token_5: 0,
            event_date: "2026-07-13".into(),
            event_meta: 0x06,
            exchange_primary: 0,
            exchange_secondary: 0,
            ingest_ts: Utc.timestamp_opt(1_752_400_000, 0).unwrap(),
            issue_flags: 0,
            price_primary_int: 101_234,
            price_secondary_int: 101_200,
            schema_version: 4,
            sip_timestamp_us: 1_752_400_000_000_000,
            size_primary: 10.0,
            size_secondary: 20.0,
            source_sequence: 2,
            ticker: "TEST".into(),
        };
        let points = event_points(&event);
        assert_eq!(points.len(), 2);
        assert_eq!(points[0].family, "quote_bid");
        assert!((points[0].price - 10.12).abs() < 0.0001);
        assert_eq!(points[1].family, "quote_ask");
    }

    #[test]
    fn parses_training_resolution() {
        assert_eq!(parse_resolution_us("100ms").unwrap(), 100_000);
    }
}
