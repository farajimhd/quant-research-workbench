use crate::compact_event::LiveCompactEvent;
use crate::config::GatewayConfig;
use crate::metrics::SharedMetrics;
use crate::timefmt::clickhouse_datetime64;
use chrono::{Datelike, Timelike, Utc};
use chrono_tz::America::New_York;
use reqwest::Client;
use serde::Serialize;
use serde_json::json;
use std::collections::{HashMap, HashSet};
use tokio::sync::{broadcast, mpsc};
use tokio::task::JoinHandle;
use tokio::time::{interval, sleep, timeout, Duration, Instant};

pub const INTRADAY_BAR_SCHEMA_VERSION: u16 = 2;
pub const BASE_RESOLUTION_US: i64 = 100_000;
const SESSION_START_US: i64 = 4 * 60 * 60 * 1_000_000;
const SESSION_END_US: i64 = 20 * 60 * 60 * 1_000_000;
const OBSOLETE_BAR_TABLES: &[&str] = &[
    "live_market_bars",
    "bars_by_symbol_time",
    "bars_by_time_symbol",
    "live_model_microbars",
];

type BarKey = (String, String, i64, i64, &'static str);
type FinalizedSeries = (String, String, &'static str);

#[derive(Clone, Eq, Hash, PartialEq)]
struct RepairRequest {
    ticker: String,
    local_date: String,
    bucket_index: i64,
    sip_timestamp_us: u64,
    source_sequence: u64,
    event_type: u8,
    arrival_sequence: u64,
}

enum WriterMessage {
    Row(IntradayBarRow),
    Repair(RepairRequest),
}

#[derive(Clone)]
pub struct IntradayBarRouter {
    senders: Vec<mpsc::Sender<LiveCompactEvent>>,
}

pub struct IntradayBarService {
    pub router: IntradayBarRouter,
    pub rows: broadcast::Sender<IntradayBarRow>,
    tasks: Vec<JoinHandle<()>>,
}

impl IntradayBarService {
    pub fn into_tasks(self) -> Vec<JoinHandle<()>> {
        self.tasks
    }
}

impl IntradayBarRouter {
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
    size: f64,
}

#[derive(Clone, Eq, Hash, PartialEq)]
struct EventIdentity {
    sip_timestamp_us: u64,
    source_sequence: u64,
    event_meta: u8,
    price_primary_int: u32,
    price_secondary_int: u32,
    size_primary_bits: u32,
    size_secondary_bits: u32,
    exchange_primary: u8,
    exchange_secondary: u8,
    condition_tokens: [u8; 5],
    issue_flags: u16,
}

#[derive(Clone, Serialize)]
pub struct IntradayBarRow {
    schema_version: u16,
    ticker: String,
    local_date: String,
    label_resolution_us: i64,
    bucket_index: i64,
    bar_family: &'static str,
    open: f32,
    close: f32,
    high: f32,
    low: f32,
    size_sum: f64,
    size_open: f64,
    size_close: f64,
    size_high: f64,
    size_low: f64,
    event_count: u64,
    first_event_timestamp_us: u64,
    last_event_timestamp_us: u64,
    bar_start_session_us: i64,
    bar_end_session_us: i64,
    #[serde(skip)]
    first_key: SortKey,
    #[serde(skip)]
    last_key: SortKey,
}

impl IntradayBarRow {
    fn from_event(
        event: &LiveCompactEvent,
        point: &EventPoint,
        bucket: i64,
        local_date: String,
    ) -> Self {
        let key = sort_key(event);
        Self {
            schema_version: INTRADAY_BAR_SCHEMA_VERSION,
            ticker: event.ticker.clone(),
            local_date,
            label_resolution_us: BASE_RESOLUTION_US,
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
            first_event_timestamp_us: event.sip_timestamp_us,
            last_event_timestamp_us: event.sip_timestamp_us,
            bar_start_session_us: bucket * BASE_RESOLUTION_US,
            bar_end_session_us: (bucket + 1) * BASE_RESOLUTION_US,
            first_key: key,
            last_key: key,
        }
    }

    fn update_event(&mut self, event: &LiveCompactEvent, point: &EventPoint) {
        let key = sort_key(event);
        if key < self.first_key {
            self.first_key = key;
            self.open = point.price;
            self.size_open = point.size;
            self.first_event_timestamp_us = event.sip_timestamp_us;
        }
        if key >= self.last_key {
            self.last_key = key;
            self.close = point.price;
            self.size_close = point.size;
            self.last_event_timestamp_us = event.sip_timestamp_us;
        }
        self.high = self.high.max(point.price);
        self.low = self.low.min(point.price);
        self.size_sum += point.size;
        self.size_high = self.size_high.max(point.size);
        self.size_low = self.size_low.min(point.size);
        self.event_count = self.event_count.saturating_add(1);
    }

    fn from_base(base: &Self, resolution_us: i64) -> Self {
        let bucket_index = base.bar_start_session_us.div_euclid(resolution_us);
        let mut row = base.clone();
        row.label_resolution_us = resolution_us;
        row.bucket_index = bucket_index;
        row.bar_start_session_us = bucket_index * resolution_us;
        row.bar_end_session_us = (bucket_index + 1) * resolution_us;
        row
    }

    fn update_base(&mut self, base: &Self) {
        if base.first_key < self.first_key {
            self.first_key = base.first_key;
            self.open = base.open;
            self.size_open = base.size_open;
            self.first_event_timestamp_us = base.first_event_timestamp_us;
        }
        if base.last_key >= self.last_key {
            self.last_key = base.last_key;
            self.close = base.close;
            self.size_close = base.size_close;
            self.last_event_timestamp_us = base.last_event_timestamp_us;
        }
        self.high = self.high.max(base.high);
        self.low = self.low.min(base.low);
        self.size_sum += base.size_sum;
        self.size_high = self.size_high.max(base.size_high);
        self.size_low = self.size_low.min(base.size_low);
        self.event_count = self.event_count.saturating_add(base.event_count);
    }
}

pub async fn spawn_intraday_bar_service(
    config: GatewayConfig,
    metrics: SharedMetrics,
) -> Result<IntradayBarService, String> {
    let mut resolutions = config
        .intraday_bar_timeframes
        .iter()
        .map(|value| parse_resolution_us(value))
        .collect::<Result<Vec<_>, _>>()?;
    resolutions.sort_unstable();
    resolutions.dedup();
    validate_resolutions(&resolutions)?;
    validate_identifier(&config.intraday_bar_table, "QMD_INTRADAY_BAR_TABLE")?;
    validate_identifier(&config.compact_event_table, "QMD_COMPACT_EVENT_TABLE")?;

    let (row_sender, row_receiver) = mpsc::channel(config.intraday_bar_channel_capacity);
    let (broadcast_sender, _) = broadcast::channel(10_000);
    let writer = IntradayBarWriter::new(config.clone(), metrics.clone(), resolutions.clone());
    writer.initialize().await?;
    metrics.set_lane_state(
        "intraday_bars",
        "healthy",
        "Canonical intraday bar table initialized; awaiting closed 100ms bars.",
    );
    let mut tasks = vec![tokio::spawn(writer.run(row_receiver))];
    let mut senders = Vec::new();
    let lateness_us = config.compact_event_reorder_lag_ms.saturating_mul(1_000) as i64;
    for _ in 0..config.intraday_bar_shard_count.max(1) {
        let (sender, mut receiver) =
            mpsc::channel::<LiveCompactEvent>(config.intraday_bar_channel_capacity);
        let output = row_sender.clone();
        let live_rows = broadcast_sender.clone();
        let shard_resolutions = resolutions.clone();
        let shard_metrics = metrics.clone();
        tasks.push(tokio::spawn(async move {
            let mut base_bars: HashMap<BarKey, IntradayBarRow> = HashMap::new();
            let mut base_seen: HashMap<BarKey, HashSet<EventIdentity>> = HashMap::new();
            let mut rollups: HashMap<BarKey, IntradayBarRow> = HashMap::new();
            let mut max_seen: HashMap<(String, String), i64> = HashMap::new();
            let mut finalized_through: HashMap<FinalizedSeries, i64> = HashMap::new();
            loop {
                let event = match timeout(Duration::from_millis(100), receiver.recv()).await {
                    Ok(Some(event)) => event,
                    Ok(None) => break,
                    Err(_) => {
                        if !flush_wall_ready(
                            &mut base_bars,
                            &mut base_seen,
                            &mut rollups,
                            &mut finalized_through,
                            &shard_resolutions,
                            &live_rows,
                            &output,
                            &shard_metrics,
                            lateness_us,
                        )
                        .await
                        {
                            return;
                        }
                        continue;
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
                let series = (event.ticker.clone(), local_date.clone());
                max_seen
                    .entry(series.clone())
                    .and_modify(|value| *value = (*value).max(local_session_us))
                    .or_insert(local_session_us);
                let bucket = local_session_us.div_euclid(BASE_RESOLUTION_US);
                for point in event_points(&event) {
                    let finalized = finalized_through
                        .get(&(event.ticker.clone(), local_date.clone(), point.family))
                        .copied()
                        .unwrap_or_default();
                    if (bucket + 1) * BASE_RESOLUTION_US <= finalized {
                        shard_metrics.inc_intraday_bar_repair_requested();
                        if output
                            .send(WriterMessage::Repair(RepairRequest {
                                ticker: event.ticker.clone(),
                                local_date: local_date.clone(),
                                bucket_index: bucket,
                                sip_timestamp_us: event.sip_timestamp_us,
                                source_sequence: event.source_sequence,
                                event_type: event.event_meta & 1,
                                arrival_sequence: event.arrival_sequence,
                            }))
                            .await
                            .is_err()
                        {
                            shard_metrics.inc_intraday_bar_event_dropped();
                            return;
                        }
                        continue;
                    }
                    let key = (
                        event.ticker.clone(),
                        local_date.clone(),
                        BASE_RESOLUTION_US,
                        bucket,
                        point.family,
                    );
                    if !base_seen
                        .entry(key.clone())
                        .or_default()
                        .insert(event_identity(&event))
                    {
                        continue;
                    }
                    base_bars
                        .entry(key)
                        .and_modify(|bar| bar.update_event(&event, &point))
                        .or_insert_with(|| {
                            IntradayBarRow::from_event(&event, &point, bucket, local_date.clone())
                        });
                }
                let watermark = max_seen[&series].saturating_sub(lateness_us);
                if !flush_ready(
                    &mut base_bars,
                    &mut base_seen,
                    &mut rollups,
                    &mut finalized_through,
                    &shard_resolutions,
                    Some(&series),
                    watermark,
                    &live_rows,
                    &output,
                    &shard_metrics,
                )
                .await
                {
                    return;
                }
            }
            let _ = flush_ready(
                &mut base_bars,
                &mut base_seen,
                &mut rollups,
                &mut finalized_through,
                &shard_resolutions,
                None,
                i64::MAX,
                &live_rows,
                &output,
                &shard_metrics,
            )
            .await;
        }));
        senders.push(sender);
    }
    drop(row_sender);
    Ok(IntradayBarService {
        router: IntradayBarRouter { senders },
        rows: broadcast_sender,
        tasks,
    })
}

#[allow(clippy::too_many_arguments)]
async fn flush_ready(
    base_bars: &mut HashMap<BarKey, IntradayBarRow>,
    base_seen: &mut HashMap<BarKey, HashSet<EventIdentity>>,
    rollups: &mut HashMap<BarKey, IntradayBarRow>,
    finalized_through: &mut HashMap<FinalizedSeries, i64>,
    resolutions: &[i64],
    series: Option<&(String, String)>,
    watermark: i64,
    live_rows: &broadcast::Sender<IntradayBarRow>,
    output: &mpsc::Sender<WriterMessage>,
    metrics: &SharedMetrics,
) -> bool {
    let mut ready = base_bars
        .keys()
        .filter(|key| {
            series.map_or(true, |series| key.0 == series.0 && key.1 == series.1)
                && (key.3 + 1) * BASE_RESOLUTION_US <= watermark
        })
        .cloned()
        .collect::<Vec<_>>();
    ready.sort();
    for key in ready {
        let Some(base) = base_bars.remove(&key) else {
            continue;
        };
        base_seen.remove(&key);
        finalized_through
            .entry((
                base.ticker.clone(),
                base.local_date.clone(),
                base.bar_family,
            ))
            .and_modify(|value| *value = (*value).max(base.bar_end_session_us))
            .or_insert(base.bar_end_session_us);
        if !emit_row(base.clone(), live_rows, output, metrics).await {
            return false;
        }
        for resolution_us in resolutions
            .iter()
            .copied()
            .filter(|value| *value > BASE_RESOLUTION_US)
        {
            let row = IntradayBarRow::from_base(&base, resolution_us);
            let key = (
                row.ticker.clone(),
                row.local_date.clone(),
                resolution_us,
                row.bucket_index,
                row.bar_family,
            );
            rollups
                .entry(key)
                .and_modify(|parent| parent.update_base(&base))
                .or_insert(row);
        }
    }
    let mut ready_rollups = rollups
        .keys()
        .filter(|key| {
            series.map_or(true, |series| key.0 == series.0 && key.1 == series.1)
                && (key.3 + 1) * key.2 <= watermark
        })
        .cloned()
        .collect::<Vec<_>>();
    ready_rollups.sort();
    for key in ready_rollups {
        if let Some(row) = rollups.remove(&key) {
            if !emit_row(row, live_rows, output, metrics).await {
                return false;
            }
        }
    }
    true
}

async fn flush_wall_ready(
    base_bars: &mut HashMap<BarKey, IntradayBarRow>,
    base_seen: &mut HashMap<BarKey, HashSet<EventIdentity>>,
    rollups: &mut HashMap<BarKey, IntradayBarRow>,
    finalized_through: &mut HashMap<FinalizedSeries, i64>,
    resolutions: &[i64],
    live_rows: &broadcast::Sender<IntradayBarRow>,
    output: &mpsc::Sender<WriterMessage>,
    metrics: &SharedMetrics,
    lateness_us: i64,
) -> bool {
    let now_us = Utc::now().timestamp_micros().max(0) as u64;
    let Some((local_date, local_session_us)) = local_coordinates(now_us) else {
        return true;
    };
    let watermark = local_session_us.saturating_sub(lateness_us);
    let series = base_bars
        .keys()
        .chain(rollups.keys())
        .map(|key| (key.0.clone(), key.1.clone()))
        .collect::<std::collections::BTreeSet<_>>();
    for current in series {
        let current_watermark = if current.1 < local_date {
            i64::MAX
        } else if current.1 == local_date {
            watermark
        } else {
            continue;
        };
        if !flush_ready(
            base_bars,
            base_seen,
            rollups,
            finalized_through,
            resolutions,
            Some(&current),
            current_watermark,
            live_rows,
            output,
            metrics,
        )
        .await
        {
            return false;
        }
    }
    true
}

async fn emit_row(
    row: IntradayBarRow,
    live_rows: &broadcast::Sender<IntradayBarRow>,
    output: &mpsc::Sender<WriterMessage>,
    metrics: &SharedMetrics,
) -> bool {
    let _ = live_rows.send(row.clone());
    if output.send(WriterMessage::Row(row)).await.is_err() {
        metrics.inc_intraday_bar_event_dropped();
        return false;
    }
    metrics.inc_intraday_bar_emitted(1);
    true
}

struct IntradayBarWriter {
    client: Client,
    config: GatewayConfig,
    metrics: SharedMetrics,
    resolutions: Vec<i64>,
}

impl IntradayBarWriter {
    fn new(config: GatewayConfig, metrics: SharedMetrics, resolutions: Vec<i64>) -> Self {
        Self {
            client: Client::new(),
            config,
            metrics,
            resolutions,
        }
    }

    async fn initialize(&self) -> Result<(), String> {
        self.query(&format!(
            r#"CREATE TABLE IF NOT EXISTS {table}
            (
                schema_version UInt16,
                ticker LowCardinality(String),
                local_date Date,
                label_resolution_us UInt64,
                bucket_index UInt64,
                bar_family LowCardinality(String),
                open Float32,
                close Float32,
                high Float32,
                low Float32,
                size_sum Float64,
                size_open Float64,
                size_close Float64,
                size_high Float64,
                size_low Float64,
                event_count UInt64,
                first_event_timestamp_us UInt64,
                last_event_timestamp_us UInt64,
                bar_start_session_us Int64,
                bar_end_session_us Int64,
                updated_at_utc DateTime64(3, 'UTC') DEFAULT now64(3)
            ) ENGINE = ReplacingMergeTree(updated_at_utc)
            PARTITION BY local_date
            ORDER BY (local_date, ticker, label_resolution_us, bucket_index, bar_family)
            {settings}"#,
            table = self.config.intraday_bar_table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        ))
        .await?;
        self.validate_schema().await?;
        self.bootstrap_if_empty().await?;
        self.drop_obsolete_tables().await
    }

    async fn validate_schema(&self) -> Result<(), String> {
        let description = self
            .query(&format!(
                "DESCRIBE TABLE {} FORMAT TabSeparatedRaw",
                self.config.intraday_bar_table
            ))
            .await?;
        let actual = description
            .lines()
            .filter_map(|line| {
                let mut fields = line.split('\t');
                Some((fields.next()?.to_string(), fields.next()?.to_string()))
            })
            .collect::<HashMap<_, _>>();
        let expected = [
            ("schema_version", "UInt16"),
            ("ticker", "LowCardinality(String)"),
            ("local_date", "Date"),
            ("label_resolution_us", "UInt64"),
            ("bucket_index", "UInt64"),
            ("bar_family", "LowCardinality(String)"),
            ("open", "Float32"),
            ("close", "Float32"),
            ("high", "Float32"),
            ("low", "Float32"),
            ("size_sum", "Float64"),
            ("size_open", "Float64"),
            ("size_close", "Float64"),
            ("size_high", "Float64"),
            ("size_low", "Float64"),
            ("event_count", "UInt64"),
            ("first_event_timestamp_us", "UInt64"),
            ("last_event_timestamp_us", "UInt64"),
            ("bar_start_session_us", "Int64"),
            ("bar_end_session_us", "Int64"),
            ("updated_at_utc", "DateTime64(3, 'UTC')"),
        ];
        let mismatches = expected
            .iter()
            .filter(|(name, expected_type)| {
                actual.get(*name).map(String::as_str) != Some(*expected_type)
            })
            .map(|(name, expected_type)| {
                format!(
                    "{name}: expected {expected_type}, found {}",
                    actual.get(*name).map(String::as_str).unwrap_or("missing")
                )
            })
            .collect::<Vec<_>>();
        if !mismatches.is_empty() {
            return Err(format!(
                "{} is incompatible; obsolete tables were not dropped ({})",
                self.config.intraday_bar_table,
                mismatches.join("; ")
            ));
        }
        let create_sql = self
            .query(&format!(
                "SHOW CREATE TABLE {} FORMAT TabSeparatedRaw",
                self.config.intraday_bar_table
            ))
            .await?;
        let normalized = create_sql
            .replace('`', "")
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ");
        for required in [
            "ReplacingMergeTree(updated_at_utc)",
            "PARTITION BY local_date",
            "ORDER BY (local_date, ticker, label_resolution_us, bucket_index, bar_family)",
        ] {
            if !normalized.contains(required) {
                return Err(format!(
                    "{} is incompatible: SHOW CREATE is missing {required}; obsolete tables were not dropped",
                    self.config.intraday_bar_table
                ));
            }
        }
        Ok(())
    }

    async fn bootstrap_if_empty(&self) -> Result<(), String> {
        let source_exists = parse_count(
            &self
                .query(&format!(
                    "EXISTS TABLE {} FORMAT TabSeparated",
                    self.config.compact_event_table
                ))
                .await?,
        )?;
        if source_exists == 0 {
            return Ok(());
        }
        let source_rows = parse_count(
            &self
                .query(&format!(
                    "SELECT count() FROM {} FORMAT TabSeparated",
                    self.config.compact_event_table
                ))
                .await?,
        )?;
        if source_rows == 0 {
            return Ok(());
        }
        for resolution_us in &self.resolutions {
            let resolution_rows = parse_count(
                &self
                    .query(&format!(
                        "SELECT count() FROM {} WHERE label_resolution_us = {} FORMAT TabSeparated",
                        self.config.intraday_bar_table, resolution_us
                    ))
                    .await?,
            )?;
            if resolution_rows > 0 {
                continue;
            }
            if *resolution_us == BASE_RESOLUTION_US {
                self.query(&self.bootstrap_base_sql(None)).await?;
            } else {
                self.query(&self.bootstrap_rollup_sql(*resolution_us, None))
                    .await?;
            }
        }
        for resolution_us in &self.resolutions {
            let resolution_rows = parse_count(
                &self
                    .query(&format!(
                        "SELECT count() FROM {} WHERE label_resolution_us = {} FORMAT TabSeparated",
                        self.config.intraday_bar_table, resolution_us
                    ))
                    .await?,
            )?;
            if resolution_rows == 0 {
                return Err(format!(
                    "{} bootstrap produced zero {}us bars from {source_rows} compact events; obsolete tables were not dropped",
                    self.config.intraday_bar_table, resolution_us
                ));
            }
        }
        Ok(())
    }

    fn bootstrap_base_sql(&self, repair: Option<&RepairRequest>) -> String {
        let filter = repair
            .map(|request| {
                format!(
                    " AND ticker = '{}' AND local_date_value = toDate('{}') AND bucket = {}",
                    escape_sql_string(&request.ticker),
                    request.local_date,
                    request.bucket_index,
                )
            })
            .unwrap_or_default();
        format!(
            r#"INSERT INTO {target}
            (schema_version, ticker, local_date, label_resolution_us, bucket_index, bar_family,
             open, close, high, low, size_sum, size_open, size_close, size_high, size_low,
            event_count, first_event_timestamp_us, last_event_timestamp_us,
             bar_start_session_us, bar_end_session_us)
            WITH
              fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)) AS event_ts_utc,
              toTimeZone(event_ts_utc, 'America/New_York') AS event_ts_local,
              toDate(event_ts_local) AS local_date_value,
              toInt64(sip_timestamp_us)
                - toUnixTimestamp64Micro(toDateTime64(toStartOfDay(event_ts_local), 6, 'America/New_York')) AS session_us,
              intDiv(session_us, {base}) AS bucket,
              tuple(sip_timestamp_us, source_sequence, bitAnd(event_meta, 1), arrival_sequence) AS event_order
            SELECT
              {schema_version}, ticker, local_date_value, {base}, bucket, bar_family,
              toFloat32(argMin(price, event_order)), toFloat32(argMax(price, event_order)),
              toFloat32(max(price)), toFloat32(min(price)), toFloat32(sum(size)),
              toFloat32(argMin(size, event_order)), toFloat32(argMax(size, event_order)),
              toFloat32(max(size)), toFloat32(min(size)), toUInt32(count()),
              toInt64(min(sip_timestamp_us)), toInt64(max(sip_timestamp_us)),
              bucket * {base}, (bucket + 1) * {base}
            FROM
            (
              SELECT *, 'trade' AS bar_family,
                toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price,
                toFloat64(size_primary) AS size
              FROM {source} FINAL WHERE bitAnd(event_meta, 1) = 1
              UNION ALL
              SELECT *, 'quote_bid' AS bar_family,
                toFloat64(price_secondary_int) / if(bitAnd(event_meta, 4) != 0, 10000., 100.) AS price,
                toFloat64(size_secondary) AS size
              FROM {source} FINAL WHERE bitAnd(event_meta, 1) = 0
              UNION ALL
              SELECT *, 'quote_ask' AS bar_family,
                toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price,
                toFloat64(size_primary) AS size
              FROM {source} FINAL WHERE bitAnd(event_meta, 1) = 0
            )
            WHERE price > 0 AND session_us >= {session_start} AND session_us < {session_end}{filter}
            GROUP BY ticker, local_date_value, bucket, bar_family"#,
            target = self.config.intraday_bar_table,
            source = self.config.compact_event_table,
            schema_version = INTRADAY_BAR_SCHEMA_VERSION,
            base = BASE_RESOLUTION_US,
            session_start = SESSION_START_US,
            session_end = SESSION_END_US,
            filter = filter,
        )
    }

    fn bootstrap_rollup_sql(&self, resolution_us: i64, repair: Option<&RepairRequest>) -> String {
        let filter = repair
            .map(|request| {
                format!(
                    " AND ticker = '{}' AND local_date = toDate('{}') AND intDiv(bar_start_session_us, {}) = {}",
                    escape_sql_string(&request.ticker),
                    request.local_date,
                    resolution_us,
                    request.bucket_index * BASE_RESOLUTION_US / resolution_us,
                )
            })
            .unwrap_or_default();
        format!(
            r#"INSERT INTO {table}
            (schema_version, ticker, local_date, label_resolution_us, bucket_index, bar_family,
             open, close, high, low, size_sum, size_open, size_close, size_high, size_low,
             event_count, first_event_timestamp_us, last_event_timestamp_us,
             bar_start_session_us, bar_end_session_us)
            SELECT
              {schema_version}, ticker, local_date, {resolution},
              intDiv(bar_start_session_us, {resolution}) AS bucket, bar_family,
              argMin(open, bucket_index), argMax(close, bucket_index), max(high), min(low), sum(size_sum),
              argMin(size_open, bucket_index), argMax(size_close, bucket_index), max(size_high), min(size_low),
              toUInt64(sum(event_count)), min(first_event_timestamp_us), max(last_event_timestamp_us),
              bucket * {resolution}, (bucket + 1) * {resolution}
            FROM {table} FINAL
            WHERE label_resolution_us = {base}{filter}
            GROUP BY ticker, local_date, bucket, bar_family"#,
            table = self.config.intraday_bar_table,
            schema_version = INTRADAY_BAR_SCHEMA_VERSION,
            resolution = resolution_us,
            base = BASE_RESOLUTION_US,
            filter = filter,
        )
    }

    async fn drop_obsolete_tables(&self) -> Result<(), String> {
        let validation = self
            .query(&format!(
                "SELECT count() >= 0 FROM {} FORMAT TabSeparated",
                self.config.intraday_bar_table
            ))
            .await?;
        if validation.trim() != "1" {
            return Err(format!(
                "{} did not pass readiness validation; obsolete tables were not dropped",
                self.config.intraday_bar_table
            ));
        }
        for table in OBSOLETE_BAR_TABLES {
            self.query(&format!("DROP TABLE IF EXISTS {table}")).await?;
        }
        Ok(())
    }

    async fn run(self, mut receiver: mpsc::Receiver<WriterMessage>) {
        let mut batch = Vec::with_capacity(self.config.max_clickhouse_batch);
        let mut repairs = HashMap::<RepairRequest, Instant>::new();
        let mut tick = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                message = receiver.recv() => match message {
                    Some(WriterMessage::Row(row)) => batch.push(row),
                    Some(WriterMessage::Repair(request)) => {
                        repairs.entry(request).or_insert_with(|| {
                            Instant::now() + Duration::from_millis(self.config.flush_interval_ms.saturating_mul(2))
                        });
                    }
                    None => {
                        while !batch.is_empty() {
                            self.flush(&mut batch).await;
                            if !batch.is_empty() {
                                sleep(Duration::from_millis(250)).await;
                            }
                        }
                        if !repairs.is_empty() {
                            sleep(Duration::from_millis(self.config.flush_interval_ms.saturating_mul(2))).await;
                            self.flush_repairs(&mut repairs, true).await;
                        }
                        return;
                    }
                },
                _ = tick.tick() => {
                    self.flush(&mut batch).await;
                    self.flush_repairs(&mut repairs, false).await;
                },
            }
            if batch.len() >= self.config.max_clickhouse_batch {
                self.flush(&mut batch).await;
            }
            self.metrics.set_lane_pending(
                "intraday_bars",
                (batch.len() + repairs.len() + receiver.len()) as u64,
            );
        }
    }

    async fn flush_repairs(&self, repairs: &mut HashMap<RepairRequest, Instant>, force: bool) {
        let now = Instant::now();
        let ready = repairs
            .iter()
            .filter(|(_, due)| force || **due <= now)
            .map(|(request, _)| request.clone())
            .collect::<HashSet<_>>();
        for request in ready {
            if let Err(error) = self.repair_bucket(&request).await {
                self.metrics.record_lane_failure("intraday_bars", &error);
                eprintln!(
                    "Intraday bar late-event repair failed: ticker={} local_date={} bucket={} sip_timestamp_us={} source_sequence={} event_type={} arrival_sequence={} error={error}",
                    request.ticker,
                    request.local_date,
                    request.bucket_index,
                    request.sip_timestamp_us,
                    request.source_sequence,
                    request.event_type,
                    request.arrival_sequence,
                );
                repairs.insert(
                    request,
                    Instant::now()
                        + Duration::from_millis(self.config.flush_interval_ms.saturating_mul(2)),
                );
                continue;
            }
            repairs.remove(&request);
            self.metrics.inc_intraday_bar_repair_completed();
            self.metrics.record_lane_success(
                "intraday_bars",
                1,
                "Rebuilt one late-event 100ms bucket and its parent rollups.",
            );
        }
    }

    async fn repair_bucket(&self, request: &RepairRequest) -> Result<(), String> {
        let source_count = parse_count(
            &self
                .query(&format!(
                    "SELECT count() FROM {} FINAL WHERE ticker = '{}' AND sip_timestamp_us = {} AND source_sequence = {} AND bitAnd(event_meta, 1) = {} AND arrival_sequence = {} FORMAT TabSeparated",
                    self.config.compact_event_table,
                    escape_sql_string(&request.ticker),
                    request.sip_timestamp_us,
                    request.source_sequence,
                    request.event_type,
                    request.arrival_sequence,
                ))
                .await?,
        )?;
        if source_count == 0 {
            return Err("late compact event is not durable yet; retrying bucket rebuild".into());
        }
        self.query(&self.bootstrap_base_sql(Some(request))).await?;
        for resolution_us in self
            .resolutions
            .iter()
            .copied()
            .filter(|value| *value > BASE_RESOLUTION_US)
        {
            self.query(&self.bootstrap_rollup_sql(resolution_us, Some(request)))
                .await?;
        }
        Ok(())
    }

    async fn flush(&self, rows: &mut Vec<IntradayBarRow>) {
        if rows.is_empty() {
            return;
        }
        self.metrics
            .set_lane_pending("intraday_bars", rows.len() as u64);
        let body = rows
            .iter()
            .map(|row| {
                json!({
                    "schema_version": row.schema_version,
                    "ticker": row.ticker,
                    "local_date": row.local_date,
                    "label_resolution_us": row.label_resolution_us,
                    "bucket_index": row.bucket_index,
                    "bar_family": row.bar_family,
                    "open": row.open,
                    "close": row.close,
                    "high": row.high,
                    "low": row.low,
                    "size_sum": row.size_sum,
                    "size_open": row.size_open,
                    "size_close": row.size_close,
                    "size_high": row.size_high,
                    "size_low": row.size_low,
                    "event_count": row.event_count,
                    "first_event_timestamp_us": row.first_event_timestamp_us,
                    "last_event_timestamp_us": row.last_event_timestamp_us,
                    "bar_start_session_us": row.bar_start_session_us,
                    "bar_end_session_us": row.bar_end_session_us,
                })
                .to_string()
            })
            .collect::<Vec<_>>()
            .join("\n");
        if let Err(error) = self
            .query(&format!(
                "INSERT INTO {} FORMAT JSONEachRow\n{body}",
                self.config.intraday_bar_table
            ))
            .await
        {
            self.metrics.record_lane_failure("intraday_bars", &error);
            eprintln!("Canonical intraday bar insert failed: {error}");
            return;
        }
        let coverage_result = self.record_coverage(rows).await;
        let count = rows.len() as u64;
        rows.clear();
        self.metrics.inc_intraday_bar_persisted(count);
        self.metrics.record_lane_success(
            "intraday_bars",
            count,
            "Committed canonical intraday bars derived from closed 100ms bars.",
        );
        self.metrics.set_lane_pending("intraday_bars", 0);
        match coverage_result {
            Ok(()) => self.metrics.record_lane_success(
                "coverage_ledger",
                1,
                "Recorded canonical intraday-bar coverage confirmation.",
            ),
            Err(error) => {
                self.metrics.record_lane_failure("coverage_ledger", &error);
                eprintln!("Canonical intraday bar coverage update failed: {error}");
            }
        }
    }

    async fn record_coverage(&self, rows: &[IntradayBarRow]) -> Result<(), String> {
        let base_rows = rows
            .iter()
            .filter(|row| row.label_resolution_us == BASE_RESOLUTION_US)
            .collect::<Vec<_>>();
        if base_rows.is_empty() {
            return Ok(());
        }
        let min_us = base_rows
            .iter()
            .map(|row| row.first_event_timestamp_us)
            .min()
            .unwrap_or_default();
        let max_us = base_rows
            .iter()
            .map(|row| row.last_event_timestamp_us)
            .max()
            .unwrap_or_default();
        let Ok(min_us_i64) = i64::try_from(min_us) else {
            return Err(format!("invalid intraday bar coverage start {min_us}"));
        };
        let Some(start) = chrono::DateTime::<Utc>::from_timestamp_micros(min_us_i64) else {
            return Err(format!("invalid intraday bar coverage start {min_us}"));
        };
        let Ok(max_us_i64) = i64::try_from(max_us) else {
            return Err(format!("invalid intraday bar coverage end {max_us}"));
        };
        let Some(end) = chrono::DateTime::<Utc>::from_timestamp_micros(max_us_i64) else {
            return Err(format!("invalid intraday bar coverage end {max_us}"));
        };
        if end <= start {
            return Ok(());
        }
        let now = Utc::now();
        let started_at = self.config.qmd_run_started_at().unwrap_or(start);
        let row = json!({
            "coverage_kind": "q_live_events",
            "coverage_id": format!("intraday_{}", self.config.qmd_run_id),
            "source": "qmd_intraday_bar_writer",
            "status": "intraday_bars_persisted",
            "coverage_start_utc": clickhouse_datetime64(&started_at.min(start)),
            "coverage_end_utc": clickhouse_datetime64(&end),
            "rows_written": base_rows.len() as u64,
            "event_rows": 0u64,
            "bar_rows": base_rows.len() as u64,
            "error_count": 0u64,
            "started_at_utc": clickhouse_datetime64(&started_at.min(start)),
            "updated_at_utc": clickhouse_datetime64(&now),
            "completed_at_utc": Option::<String>::None,
            "metadata_json": json!({
                "table": self.config.intraday_bar_table,
                "base_resolution_us": BASE_RESOLUTION_US,
                "rollup_resolutions_us": self.resolutions,
                "coverage_rule": "base 100ms bars confirm the compact-event interval; higher bars roll up from closed base bars"
            }).to_string(),
        });
        self.query(&format!(
            "INSERT INTO {} FORMAT JSONEachRow\n{}",
            self.config.qmd_live_event_coverage_table, row
        ))
        .await
        .map(|_| ())
    }

    async fn query(&self, body: &str) -> Result<String, String> {
        let mut request = self
            .client
            .post(format!(
                "{}/?database={}",
                self.config.clickhouse_url,
                urlencoding::encode(&self.config.clickhouse_database)
            ))
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
        return (price > 0.0)
            .then_some(EventPoint {
                family: "trade",
                price,
                size: f64::from(event.size_primary),
            })
            .into_iter()
            .collect();
    }
    let mut out = Vec::new();
    let bid = event.price_secondary_int as f32 / secondary_scale;
    let ask = event.price_primary_int as f32 / primary_scale;
    if bid > 0.0 {
        out.push(EventPoint {
            family: "quote_bid",
            price: bid,
            size: f64::from(event.size_secondary),
        });
    }
    if ask > 0.0 {
        out.push(EventPoint {
            family: "quote_ask",
            price: ask,
            size: f64::from(event.size_primary),
        });
    }
    out
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

fn event_identity(event: &LiveCompactEvent) -> EventIdentity {
    EventIdentity {
        sip_timestamp_us: event.sip_timestamp_us,
        source_sequence: event.source_sequence,
        event_meta: event.event_meta,
        price_primary_int: event.price_primary_int,
        price_secondary_int: event.price_secondary_int,
        size_primary_bits: event.size_primary.to_bits(),
        size_secondary_bits: event.size_secondary.to_bits(),
        exchange_primary: event.exchange_primary,
        exchange_secondary: event.exchange_secondary,
        condition_tokens: [
            event.condition_token_1,
            event.condition_token_2,
            event.condition_token_3,
            event.condition_token_4,
            event.condition_token_5,
        ],
        issue_flags: event.issue_flags,
    }
}

fn parse_resolution_us(value: &str) -> Result<i64, String> {
    let value = value.trim().to_ascii_lowercase();
    let (raw, multiplier) = if let Some(raw) = value.strip_suffix("ms") {
        (raw, 1_000)
    } else if let Some(raw) = value.strip_suffix('s') {
        (raw, 1_000_000)
    } else if let Some(raw) = value.strip_suffix('m') {
        (raw, 60 * 1_000_000)
    } else if let Some(raw) = value.strip_suffix('h') {
        (raw, 60 * 60 * 1_000_000)
    } else {
        return Err(format!(
            "intraday bar timeframe must end in ms, s, m, or h: {value}"
        ));
    };
    raw.parse::<i64>()
        .ok()
        .filter(|parsed| *parsed > 0)
        .and_then(|parsed| parsed.checked_mul(multiplier))
        .ok_or_else(|| format!("invalid intraday bar timeframe: {value}"))
}

fn validate_resolutions(resolutions: &[i64]) -> Result<(), String> {
    if resolutions.first().copied() != Some(BASE_RESOLUTION_US) {
        return Err("QMD intraday bars require 100ms as the base resolution".into());
    }
    if resolutions
        .iter()
        .any(|value| value % BASE_RESOLUTION_US != 0)
    {
        return Err("every QMD intraday bar timeframe must be an integer multiple of 100ms".into());
    }
    for required in [100_000, 1_000_000, 5_000_000, 30_000_000, 60_000_000] {
        if !resolutions.contains(&required) {
            return Err(format!(
                "QMD_INTRADAY_BAR_TIMEFRAMES must include training resolution {required}us"
            ));
        }
    }
    Ok(())
}

fn validate_identifier(value: &str, name: &str) -> Result<(), String> {
    if value.is_empty()
        || !value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '_')
    {
        return Err(format!("{name} must be a non-empty ClickHouse identifier"));
    }
    Ok(())
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
            escape_sql_string(storage_policy.trim())
        )
    }
}

fn parse_count(value: &str) -> Result<u64, String> {
    value
        .trim()
        .parse::<u64>()
        .map_err(|error| format!("invalid ClickHouse count response {value:?}: {error}"))
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

    fn quote_event(timestamp_us: u64, sequence: u64, bid: u32, ask: u32) -> LiveCompactEvent {
        LiveCompactEvent {
            arrival_sequence: sequence,
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
            price_primary_int: ask,
            price_secondary_int: bid,
            schema_version: 4,
            sip_timestamp_us: timestamp_us,
            size_primary: 10.0,
            size_secondary: 20.0,
            source_sequence: sequence,
            ticker: "TEST".into(),
        }
    }

    #[test]
    fn quote_points_match_training_families_and_scales() {
        let points = event_points(&quote_event(1_752_400_000_000_000, 2, 101_200, 101_234));
        assert_eq!(points.len(), 2);
        assert_eq!(points[0].family, "quote_bid");
        assert!((points[0].price - 10.12).abs() < 0.0001);
        assert_eq!(points[1].family, "quote_ask");
    }

    #[test]
    fn parent_rollup_uses_closed_base_bar_algebra() {
        let first = quote_event(1_752_400_000_010_000, 1, 100_000, 101_000);
        let second = quote_event(1_752_400_000_090_000, 2, 99_000, 102_000);
        let point1 = event_points(&first).remove(0);
        let point2 = event_points(&second).remove(0);
        let mut base = IntradayBarRow::from_event(&first, &point1, 144_000, "2026-07-13".into());
        base.update_event(&second, &point2);
        let mut parent = IntradayBarRow::from_base(&base, 1_000_000);
        let third = quote_event(1_752_400_000_110_000, 3, 103_000, 104_000);
        let point3 = event_points(&third).remove(0);
        let next = IntradayBarRow::from_event(&third, &point3, 144_001, "2026-07-13".into());
        parent.update_base(&next);
        assert_eq!(parent.event_count, 3);
        assert!((parent.open - 10.0).abs() < 0.0001);
        assert!((parent.close - 10.3).abs() < 0.0001);
        assert!((parent.high - 10.3).abs() < 0.0001);
        assert!((parent.low - 9.9).abs() < 0.0001);
    }

    #[tokio::test]
    async fn sparse_parent_closes_without_another_base_event() {
        let event = quote_event(1_752_400_000_010_000, 1, 100_000, 101_000);
        let point = event_points(&event).remove(0);
        let base = IntradayBarRow::from_event(&event, &point, 144_000, "2026-07-13".into());
        let key = (
            base.ticker.clone(),
            base.local_date.clone(),
            BASE_RESOLUTION_US,
            base.bucket_index,
            base.bar_family,
        );
        let mut base_bars = HashMap::from([(key.clone(), base)]);
        let mut base_seen = HashMap::from([(key, HashSet::from([event_identity(&event)]))]);
        let mut rollups = HashMap::new();
        let mut finalized = HashMap::new();
        let (output, mut receiver) = mpsc::channel(8);
        let (broadcast, _) = broadcast::channel(8);
        let metrics = SharedMetrics::new();
        let series = ("TEST".to_string(), "2026-07-13".to_string());

        assert!(
            flush_ready(
                &mut base_bars,
                &mut base_seen,
                &mut rollups,
                &mut finalized,
                &[BASE_RESOLUTION_US, 1_000_000],
                Some(&series),
                14_400_100_000,
                &broadcast,
                &output,
                &metrics,
            )
            .await
        );
        assert!(
            matches!(receiver.try_recv(), Ok(WriterMessage::Row(row)) if row.label_resolution_us == BASE_RESOLUTION_US)
        );
        assert!(receiver.try_recv().is_err());

        assert!(
            flush_ready(
                &mut base_bars,
                &mut base_seen,
                &mut rollups,
                &mut finalized,
                &[BASE_RESOLUTION_US, 1_000_000],
                Some(&series),
                14_401_000_000,
                &broadcast,
                &output,
                &metrics,
            )
            .await
        );
        assert!(
            matches!(receiver.try_recv(), Ok(WriterMessage::Row(row)) if row.label_resolution_us == 1_000_000)
        );
    }

    #[test]
    fn canonical_grid_includes_training_and_operational_resolutions() {
        let values = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"]
            .iter()
            .map(|value| parse_resolution_us(value).unwrap())
            .collect::<Vec<_>>();
        validate_resolutions(&values).unwrap();
        assert_eq!(values.last().copied(), Some(3_600_000_000));
    }
}
