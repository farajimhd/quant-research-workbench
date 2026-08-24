use crate::bars::{TradeAggregationRules, TradeUpdateRule};
use crate::compact_event::{CompactEventDecoder, LiveCompactEvent};
use crate::config::GatewayConfig;
use crate::event::MarketEvent;
use crate::maintenance::SharedMaintenanceState;
use crate::market_calendar::MarketCalendarClient;
use crate::metrics::SharedMetrics;
use crate::timefmt::clickhouse_datetime64;
use chrono::{Datelike, Timelike, Utc};
use chrono_tz::America::New_York;
use reqwest::Client;
use serde::Serialize;
use serde_json::json;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, Mutex};
use tokio::task::JoinHandle;
use tokio::time::{interval, sleep, timeout, Duration, Instant};

pub const INTRADAY_BAR_SCHEMA_VERSION: u16 = 3;
pub const INTRADAY_BAR_CALCULATION_REVISION: &str = "qmd-family-bars-v3";
pub const BASE_RESOLUTION_US: i64 = 100_000;
const SESSION_START_US: i64 = 4 * 60 * 60 * 1_000_000;
const SESSION_END_US: i64 = 20 * 60 * 60 * 1_000_000;
const OBSOLETE_BAR_TABLES: &[&str] = &[
    "live_market_bars",
    "bars_by_symbol_time",
    "bars_by_time_symbol",
    "live_model_microbars",
];
const BOOTSTRAP_STATE_TABLE: &str = "qmd_intraday_bar_bootstrap_v1";

type BarKey = (String, String, i64, i64, &'static str);
type FinalizedSeries = (String, String, &'static str);

#[derive(Clone)]
struct RepairRequest {
    ticker: String,
    local_date: String,
    bucket_index: i64,
    sip_timestamp_us: u64,
    source_sequence: u64,
    event_type: u8,
    arrival_sequence: u64,
}

impl PartialEq for RepairRequest {
    fn eq(&self, other: &Self) -> bool {
        self.ticker == other.ticker
            && self.local_date == other.local_date
            && self.bucket_index == other.bucket_index
    }
}

impl Eq for RepairRequest {}

impl Hash for RepairRequest {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.ticker.hash(state);
        self.local_date.hash(state);
        self.bucket_index.hash(state);
    }
}

enum WriterMessage {
    Row(IntradayBarRow),
    Repair(RepairRequest),
}

#[derive(Clone, Debug)]
struct CoverageWindow {
    end: chrono::DateTime<Utc>,
    rows_written: u64,
    start: chrono::DateTime<Utc>,
}

fn intraday_coverage_groups(rows: &[IntradayBarRow]) -> BTreeMap<String, CoverageWindow> {
    let mut grouped = BTreeMap::new();
    for row in rows
        .iter()
        .filter(|row| row.label_resolution_us == BASE_RESOLUTION_US)
    {
        let Ok(start_us) = i64::try_from(row.first_event_timestamp_us) else {
            continue;
        };
        let Ok(end_us) = i64::try_from(row.last_event_timestamp_us) else {
            continue;
        };
        let Some(start) = chrono::DateTime::<Utc>::from_timestamp_micros(start_us) else {
            continue;
        };
        let Some(end) = chrono::DateTime::<Utc>::from_timestamp_micros(end_us) else {
            continue;
        };
        let window = grouped
            .entry(row.local_date.clone())
            .or_insert_with(|| CoverageWindow {
                end,
                rows_written: 0,
                start,
            });
        window.start = window.start.min(start);
        window.end = window.end.max(end);
        window.rows_written = window.rows_written.saturating_add(1);
    }
    grouped
}

#[derive(Clone)]
pub struct IntradayBarRouter {
    senders: Vec<mpsc::Sender<LiveCompactEvent>>,
}

pub struct IntradayBarService {
    pub reconciler: IntradayBarReconciler,
    pub router: IntradayBarRouter,
    pub rows: broadcast::Sender<IntradayBarRow>,
    tasks: Vec<JoinHandle<()>>,
}

#[derive(Clone)]
pub struct IntradayBarReconciler {
    writer: IntradayBarWriter,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct IntradayBarReconciliationSummary {
    pub completed_batches: u64,
    pub newly_completed_batches: u64,
    pub planned_batches: u64,
    pub source_rows: u64,
}

#[derive(Clone)]
struct IntradayWriterPending {
    counts: Arc<Vec<AtomicU64>>,
    metrics: SharedMetrics,
}

impl IntradayWriterPending {
    fn new(writer_count: usize, metrics: SharedMetrics) -> Self {
        Self {
            counts: Arc::new((0..writer_count).map(|_| AtomicU64::new(0)).collect()),
            metrics,
        }
    }

    fn set(&self, writer_id: usize, count: u64) {
        self.counts[writer_id].store(count, Ordering::Relaxed);
        let total = self
            .counts
            .iter()
            .map(|value| value.load(Ordering::Relaxed))
            .fold(0_u64, u64::saturating_add);
        self.metrics.set_lane_pending("intraday_bars", total);
    }
}

impl IntradayBarService {
    pub fn into_tasks(self) -> Vec<JoinHandle<()>> {
        self.tasks
    }
}

impl IntradayBarReconciler {
    /// Reconcile the complete retained Live window through the same canonical
    /// SQL authority used by the reviewed v3 migration. Completed batches are
    /// restart-safe and skipped; incomplete batches resume automatically.
    pub async fn reconcile_retained_window(
        &self,
        dates: &[chrono::NaiveDate],
        maintenance: &SharedMaintenanceState,
    ) -> Result<IntradayBarReconciliationSummary, String> {
        self.writer
            .bootstrap_dates(Some(dates), Some(maintenance))
            .await
    }
}

pub async fn run_intraday_bar_reconciliation_service(
    config: GatewayConfig,
    reconciler: IntradayBarReconciler,
    maintenance: SharedMaintenanceState,
    calendar: MarketCalendarClient,
) {
    if !config.derived_reconciliation_enabled {
        return;
    }
    loop {
        let snapshot = calendar.snapshot(Utc::now());
        if !snapshot.active_collection_window && !maintenance.snapshot().await.active {
            let now = Utc::now();
            let today = now.with_timezone(&New_York).date_naive();
            let dates = calendar.prior_sessions(
                today,
                config
                    .recent_live_prior_market_days
                    .max(0)
                    .saturating_add(1) as usize,
            );
            maintenance
                .start(
                    "derived_reconciliation",
                    "after_hours",
                    "Reconciling retained canonical intraday bars.",
                    None,
                    Some(now),
                )
                .await;
            match reconciler
                .reconcile_retained_window(&dates, &maintenance)
                .await
            {
                Ok(summary) => {
                    maintenance
                        .finish(
                            "up_to_date",
                            &format!(
                                "Derived bar reconciliation complete: batches={}/{} newly_completed={} source_rows={}",
                                summary.completed_batches,
                                summary.planned_batches,
                                summary.newly_completed_batches,
                                summary.source_rows,
                            ),
                        )
                        .await;
                }
                Err(error) => {
                    maintenance
                        .finish(
                            "derived_reconciliation_failed",
                            &format!("Derived bar reconciliation failed: {error}"),
                        )
                        .await;
                    eprintln!("QMD derived bar reconciliation failed: {error}");
                }
            }
        }
        sleep(Duration::from_millis(
            config.derived_reconciliation_interval_ms,
        ))
        .await;
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
    rule: TradeUpdateRule,
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
    first_event_key: SortKey,
    #[serde(skip)]
    last_event_key: SortKey,
    #[serde(skip)]
    first_price_key: Option<SortKey>,
    #[serde(skip)]
    last_price_key: Option<SortKey>,
    #[serde(skip)]
    first_size_key: Option<SortKey>,
    #[serde(skip)]
    last_size_key: Option<SortKey>,
}

impl IntradayBarRow {
    fn from_event(
        event: &LiveCompactEvent,
        point: &EventPoint,
        bucket: i64,
        local_date: String,
    ) -> Self {
        let key = sort_key(event);
        let include_price = point.family != "trade" || point.rule.update_last;
        let include_high_low = point.family != "trade" || point.rule.update_high_low;
        let include_size = point.family != "trade" || point.rule.update_volume;
        let price = if include_price { point.price } else { 0.0 };
        let extreme = if include_high_low { point.price } else { 0.0 };
        let size = if include_size { point.size } else { 0.0 };
        Self {
            schema_version: INTRADAY_BAR_SCHEMA_VERSION,
            ticker: event.ticker.clone(),
            local_date,
            label_resolution_us: BASE_RESOLUTION_US,
            bucket_index: bucket,
            bar_family: point.family,
            open: price,
            close: price,
            high: extreme,
            low: extreme,
            size_sum: size,
            size_open: size,
            size_close: size,
            size_high: size,
            size_low: size,
            event_count: if point.family != "trade" || point.rule.update_volume {
                1
            } else {
                0
            },
            first_event_timestamp_us: event.sip_timestamp_us,
            last_event_timestamp_us: event.sip_timestamp_us,
            bar_start_session_us: bucket * BASE_RESOLUTION_US,
            bar_end_session_us: (bucket + 1) * BASE_RESOLUTION_US,
            first_event_key: key,
            last_event_key: key,
            first_price_key: include_price.then_some(key),
            last_price_key: include_price.then_some(key),
            first_size_key: include_size.then_some(key),
            last_size_key: include_size.then_some(key),
        }
    }

    fn update_event(&mut self, event: &LiveCompactEvent, point: &EventPoint) {
        let key = sort_key(event);
        let include_price = point.family != "trade" || point.rule.update_last;
        let include_high_low = point.family != "trade" || point.rule.update_high_low;
        let include_size = point.family != "trade" || point.rule.update_volume;
        if include_price && self.first_price_key.is_none_or(|current| key < current) {
            self.first_price_key = Some(key);
            self.open = point.price;
        }
        if include_price && self.last_price_key.is_none_or(|current| key >= current) {
            self.last_price_key = Some(key);
            self.close = point.price;
        }
        if include_high_low {
            self.high = self.high.max(point.price);
            self.low = positive_min_f32(self.low, point.price);
        }
        if include_size {
            self.size_sum += point.size;
            if self.first_size_key.is_none_or(|current| key < current) {
                self.first_size_key = Some(key);
                self.size_open = point.size;
            }
            if self.last_size_key.is_none_or(|current| key >= current) {
                self.last_size_key = Some(key);
                self.size_close = point.size;
            }
            self.size_high = self.size_high.max(point.size);
            self.size_low = positive_min_f64(self.size_low, point.size);
        }
        if key < self.first_event_key {
            self.first_event_key = key;
            self.first_event_timestamp_us = event.sip_timestamp_us;
        }
        if key >= self.last_event_key {
            self.last_event_key = key;
            self.last_event_timestamp_us = event.sip_timestamp_us;
        }
        if point.family != "trade" || point.rule.update_volume {
            self.event_count = self.event_count.saturating_add(1);
        }
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
        if base.first_price_key.is_some()
            && self
                .first_price_key
                .is_none_or(|current| base.first_price_key < Some(current))
        {
            self.first_price_key = base.first_price_key;
            self.open = base.open;
        }
        if base.first_size_key.is_some()
            && self
                .first_size_key
                .is_none_or(|current| base.first_size_key < Some(current))
        {
            self.first_size_key = base.first_size_key;
            self.size_open = base.size_open;
        }
        if base.first_event_key < self.first_event_key {
            self.first_event_key = base.first_event_key;
            self.first_event_timestamp_us = base.first_event_timestamp_us;
        }
        if base.last_price_key.is_some()
            && self
                .last_price_key
                .is_none_or(|current| base.last_price_key >= Some(current))
        {
            self.last_price_key = base.last_price_key;
            self.close = base.close;
        }
        if base.last_size_key.is_some()
            && self
                .last_size_key
                .is_none_or(|current| base.last_size_key >= Some(current))
        {
            self.last_size_key = base.last_size_key;
            self.size_close = base.size_close;
        }
        if base.last_event_key >= self.last_event_key {
            self.last_event_key = base.last_event_key;
            self.last_event_timestamp_us = base.last_event_timestamp_us;
        }
        self.high = self.high.max(base.high);
        self.low = positive_min_f32(self.low, base.low);
        self.size_sum += base.size_sum;
        self.size_high = self.size_high.max(base.size_high);
        self.size_low = positive_min_f64(self.size_low, base.size_low);
        self.event_count = self.event_count.saturating_add(base.event_count);
    }
}

pub async fn spawn_intraday_bar_service(
    config: GatewayConfig,
    metrics: SharedMetrics,
    decoder: CompactEventDecoder,
    trade_rules: TradeAggregationRules,
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
    validate_identifier(&config.derived_coverage_table, "QMD_DERIVED_COVERAGE_TABLE")?;

    let (broadcast_sender, _) = broadcast::channel(10_000);
    let writer = IntradayBarWriter::new(config.clone(), metrics.clone(), resolutions.clone());
    writer.initialize().await?;
    metrics.set_lane_state(
        "intraday_bars",
        "healthy",
        "Canonical intraday bar table initialized; awaiting closed 100ms bars.",
    );
    let shard_count = config.intraday_bar_shard_count.max(1);
    let writer_count = shard_count.min(4);
    let per_writer_capacity = (config.intraday_bar_channel_capacity / writer_count).max(1);
    let mut tasks = Vec::new();
    let mut writer_senders = Vec::with_capacity(writer_count);
    let writer_pending = IntradayWriterPending::new(writer_count, metrics.clone());
    for writer_id in 0..writer_count {
        let (sender, receiver) = mpsc::channel(per_writer_capacity);
        writer_senders.push(sender);
        tasks.push(tokio::spawn(writer.clone().run(
            writer_id,
            writer_pending.clone(),
            receiver,
        )));
    }
    let mut senders = Vec::new();
    let lateness_us = config.compact_event_reorder_lag_ms.saturating_mul(1_000) as i64;
    for shard_id in 0..shard_count {
        let (sender, mut receiver) =
            mpsc::channel::<LiveCompactEvent>(config.intraday_bar_channel_capacity);
        let output = writer_senders[shard_id % writer_count].clone();
        let live_rows = broadcast_sender.clone();
        let shard_resolutions = resolutions.clone();
        let shard_metrics = metrics.clone();
        let shard_decoder = decoder.clone();
        let shard_trade_rules = trade_rules.clone();
        tasks.push(tokio::spawn(async move {
            let mut base_bars: BTreeMap<BarKey, IntradayBarRow> = BTreeMap::new();
            let mut base_seen: HashMap<BarKey, HashSet<EventIdentity>> = HashMap::new();
            let mut rollups: BTreeMap<BarKey, IntradayBarRow> = BTreeMap::new();
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
                for point in event_points(&event, &shard_decoder, &shard_trade_rules) {
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
    drop(writer_senders);
    Ok(IntradayBarService {
        reconciler: IntradayBarReconciler {
            writer: writer.clone(),
        },
        router: IntradayBarRouter { senders },
        rows: broadcast_sender,
        tasks,
    })
}

#[allow(clippy::too_many_arguments)]
async fn flush_ready(
    base_bars: &mut BTreeMap<BarKey, IntradayBarRow>,
    base_seen: &mut HashMap<BarKey, HashSet<EventIdentity>>,
    rollups: &mut BTreeMap<BarKey, IntradayBarRow>,
    finalized_through: &mut HashMap<FinalizedSeries, i64>,
    resolutions: &[i64],
    series: Option<&(String, String)>,
    watermark: i64,
    live_rows: &broadcast::Sender<IntradayBarRow>,
    output: &mpsc::Sender<WriterMessage>,
    metrics: &SharedMetrics,
) -> bool {
    let mut ready = series_bar_keys(base_bars, series)
        .into_iter()
        .filter(|key| (key.3 + 1) * BASE_RESOLUTION_US <= watermark)
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
    let mut ready_rollups = series_bar_keys(rollups, series)
        .into_iter()
        .filter(|key| (key.3 + 1) * key.2 <= watermark)
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

fn series_bar_keys(
    rows: &BTreeMap<BarKey, IntradayBarRow>,
    series: Option<&(String, String)>,
) -> Vec<BarKey> {
    let Some((ticker, local_date)) = series else {
        return rows.keys().cloned().collect();
    };
    let lower = (ticker.clone(), local_date.clone(), i64::MIN, i64::MIN, "");
    let upper = (
        ticker.clone(),
        local_date.clone(),
        i64::MAX,
        i64::MAX,
        "\u{10ffff}",
    );
    rows.range(lower..=upper)
        .map(|(key, _)| key.clone())
        .collect()
}

async fn flush_wall_ready(
    base_bars: &mut BTreeMap<BarKey, IntradayBarRow>,
    base_seen: &mut HashMap<BarKey, HashSet<EventIdentity>>,
    rollups: &mut BTreeMap<BarKey, IntradayBarRow>,
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

#[derive(Clone)]
struct IntradayBarWriter {
    client: Client,
    config: GatewayConfig,
    metrics: SharedMetrics,
    resolutions: Vec<i64>,
    coverage_windows: Arc<Mutex<HashMap<String, CoverageWindow>>>,
}

impl IntradayBarWriter {
    fn new(config: GatewayConfig, metrics: SharedMetrics, resolutions: Vec<i64>) -> Self {
        Self {
            client: Client::new(),
            config,
            metrics,
            resolutions,
            coverage_windows: Arc::new(Mutex::new(HashMap::new())),
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
                calculation_revision LowCardinality(String),
                source_revision String,
                complete UInt8,
                updated_at_utc DateTime64(3, 'UTC') DEFAULT now64(3)
            ) ENGINE = ReplacingMergeTree(updated_at_utc)
            PARTITION BY local_date
            ORDER BY (local_date, ticker, label_resolution_us, bucket_index, bar_family)
            {settings}"#,
            table = self.config.intraday_bar_table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        ))
        .await?;
        self.query(&format!(
            r#"CREATE TABLE IF NOT EXISTS {table}
            (
                product LowCardinality(String),
                local_date Date,
                scope_id String,
                calculation_revision LowCardinality(String),
                source_revision String,
                source_row_count UInt64,
                output_row_count UInt64,
                status LowCardinality(String),
                detail String,
                updated_at_utc DateTime64(3, 'UTC') DEFAULT now64(3)
            ) ENGINE = ReplacingMergeTree(updated_at_utc)
            PARTITION BY local_date
            ORDER BY (product, local_date, scope_id, calculation_revision)
            {settings}"#,
            table = self.config.derived_coverage_table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        ))
        .await?;
        self.validate_schema().await?;
        if self.config.intraday_bar_bootstrap_on_start {
            self.bootstrap_if_empty().await?;
        }
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
            ("calculation_revision", "LowCardinality(String)"),
            ("source_revision", "String"),
            ("complete", "UInt8"),
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

    async fn bootstrap_if_empty(&self) -> Result<IntradayBarReconciliationSummary, String> {
        self.bootstrap_dates(None, None).await
    }

    async fn bootstrap_dates(
        &self,
        requested_dates: Option<&[chrono::NaiveDate]>,
        maintenance: Option<&SharedMaintenanceState>,
    ) -> Result<IntradayBarReconciliationSummary, String> {
        let source_exists = parse_count(
            &self
                .query(&format!(
                    "EXISTS TABLE {} FORMAT TabSeparated",
                    self.config.compact_event_table
                ))
                .await?,
        )?;
        if source_exists == 0 {
            return Ok(IntradayBarReconciliationSummary::default());
        }
        let mut source_rows = 0_u64;
        self.query(&format!(
            r#"CREATE TABLE IF NOT EXISTS {BOOTSTRAP_STATE_TABLE} (
                calculation_revision LowCardinality(String), local_date Date,
                batch_id UInt32, batch_fingerprint String,
                ticker_count UInt32, base_row_count UInt64,
                status LowCardinality(String), updated_at_utc DateTime64(3, 'UTC')
            ) ENGINE = ReplacingMergeTree(updated_at_utc)
            PARTITION BY local_date
            ORDER BY (calculation_revision, local_date, batch_id)"#
        ))
        .await?;
        self.query(&format!(
            "ALTER TABLE {BOOTSTRAP_STATE_TABLE} ADD COLUMN IF NOT EXISTS batch_fingerprint String DEFAULT '' AFTER batch_id"
        ))
        .await?;
        let requested = requested_dates
            .unwrap_or_default()
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();
        let mut incomplete_dates = Vec::new();
        for date in &requested {
            let complete = parse_count(
                &self
                    .query(&format!(
                        "SELECT count() FROM {} FINAL WHERE product = 'intraday_family_bars' AND local_date = toDate('{}') AND scope_id = 'session' AND calculation_revision = '{}' AND status = 'complete' FORMAT TabSeparated",
                        self.config.derived_coverage_table,
                        date,
                        INTRADAY_BAR_CALCULATION_REVISION,
                    ))
                    .await?,
            )? > 0;
            if !complete {
                incomplete_dates.push(date.clone());
            }
        }
        if requested_dates.is_some() && incomplete_dates.is_empty() {
            return Ok(IntradayBarReconciliationSummary::default());
        }
        let date_filter = if incomplete_dates.is_empty() {
            String::new()
        } else {
            let values = incomplete_dates
                .iter()
                .map(|date| format!("toDate('{date}')"))
                .collect::<Vec<_>>()
                .join(",");
            let min_date = incomplete_dates.iter().min().expect("dates are nonempty");
            let max_date = incomplete_dates.iter().max().expect("dates are nonempty");
            format!(
                "WHERE event_date BETWEEN toDate('{min_date}') - 1 AND toDate('{max_date}') + 1 AND toDate(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'America/New_York')) IN ({values})"
            )
        };
        let discovered = self
            .query(&format!(
                r#"SELECT
                    toString(toDate(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'America/New_York'))) AS local_date,
                    ticker
                FROM {source}
                {date_filter}
                {connector} ticker != ''
                GROUP BY local_date, ticker
                ORDER BY local_date, ticker
                FORMAT TSV"#,
                source = self.config.compact_event_table,
                connector = if date_filter.is_empty() { "WHERE" } else { "AND" },
            ))
            .await?;
        let mut by_date = BTreeMap::<String, Vec<String>>::new();
        for line in discovered.lines() {
            let Some((local_date, ticker)) = line.split_once('\t') else {
                continue;
            };
            by_date
                .entry(local_date.to_string())
                .or_default()
                .push(ticker.to_string());
        }
        let retained_date_count = self
            .config
            .recent_live_prior_market_days
            .max(0)
            .saturating_add(1) as usize;
        let first_retained_date = by_date
            .keys()
            .rev()
            .nth(retained_date_count.saturating_sub(1))
            .cloned();
        if let Some(first_retained_date) = first_retained_date {
            by_date.retain(|local_date, _| local_date >= &first_retained_date);
        }
        if let Some(maintenance) = maintenance {
            let jobs = by_date
                .values()
                .map(|tickers| {
                    tickers
                        .len()
                        .div_ceil(self.config.intraday_bar_bootstrap_symbol_batch)
                })
                .sum::<usize>();
            maintenance.configure_job_total(jobs as u64).await;
        }
        let mut planned_batches = 0_u64;
        let mut completed_batches = 0_u64;
        let mut newly_completed_batches = 0_u64;
        for (local_date, tickers) in by_date {
            let parsed_date = chrono::NaiveDate::parse_from_str(&local_date, "%Y-%m-%d")
                .map_err(|error| format!("invalid bootstrap local date {local_date}: {error}"))?;
            for (batch_index, ticker_batch) in tickers
                .chunks(self.config.intraday_bar_bootstrap_symbol_batch)
                .enumerate()
            {
                planned_batches = planned_batches.saturating_add(1);
                let batch_id = u32::try_from(batch_index)
                    .map_err(|_| "too many intraday bootstrap batches".to_string())?;
                let batch_fingerprint =
                    format!("{:016x}", stable_hash(&ticker_batch.join("\u{1f}")));
                if let Some(maintenance) = maintenance {
                    maintenance
                        .start_derived_batch(&format!(
                            "{local_date} {}/{}",
                            batch_index.saturating_add(1),
                            tickers
                                .len()
                                .div_ceil(self.config.intraday_bar_bootstrap_symbol_batch)
                        ))
                        .await;
                }
                let already_complete = parse_count(
                    &self
                        .query(&format!(
                            "SELECT count() FROM {BOOTSTRAP_STATE_TABLE} FINAL WHERE calculation_revision = '{revision}' AND local_date = toDate('{local_date}') AND batch_id = {batch_id} AND batch_fingerprint = '{batch_fingerprint}' AND status = 'complete' FORMAT TabSeparated",
                            revision = INTRADAY_BAR_CALCULATION_REVISION,
                        ))
                        .await?,
                )? > 0;
                if already_complete {
                    completed_batches = completed_batches.saturating_add(1);
                    if let Some(maintenance) = maintenance {
                        maintenance.complete_derived_batch(0).await;
                    }
                    continue;
                }
                let ticker_list = ticker_batch
                    .iter()
                    .map(|ticker| format!("'{}'", escape_sql_string(ticker)))
                    .collect::<Vec<_>>()
                    .join(",");
                let source_start = parsed_date - chrono::Duration::days(1);
                let source_end = parsed_date + chrono::Duration::days(1);
                let base_filter = format!(
                    " AND local_date_value = toDate('{local_date}') AND ticker IN ({ticker_list})"
                );
                let source_filter = format!(
                    " AND event_date BETWEEN toDate('{source_start}') AND toDate('{source_end}') AND ticker IN ({ticker_list})"
                );
                let source_row_count = parse_count(
                    &self
                        .query(&format!(
                            "SELECT count() FROM {source} FINAL WHERE event_date BETWEEN toDate('{source_start}') AND toDate('{source_end}') AND ticker IN ({ticker_list}) AND toDate(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'America/New_York')) = toDate('{local_date}') FORMAT TabSeparated",
                            source = self.config.compact_event_table,
                        ))
                        .await?,
                )?;
                source_rows = source_rows.saturating_add(source_row_count);
                if source_row_count == 0 {
                    completed_batches = completed_batches.saturating_add(1);
                    if let Some(maintenance) = maintenance {
                        maintenance.complete_derived_batch(0).await;
                    }
                    continue;
                }
                let base_sql = self.bootstrap_base_sql_with_filter(&base_filter, &source_filter);
                self.query(&format!("EXPLAIN SYNTAX {base_sql}")).await?;
                self.query(&base_sql).await?;
                let rollup_filter = format!(
                    " AND local_date = toDate('{local_date}') AND ticker IN ({ticker_list})"
                );
                if let Some(rollup_sql) = self.bootstrap_all_rollups_sql_with_filter(&rollup_filter)
                {
                    self.query(&format!("EXPLAIN SYNTAX {rollup_sql}")).await?;
                    self.query(&rollup_sql).await?;
                }
                let base_row_count = parse_count(
                    &self
                        .query(&format!(
                            "SELECT count() FROM {} FINAL WHERE calculation_revision = '{}' AND complete = 1 AND local_date = toDate('{}') AND label_resolution_us = {} AND ticker IN ({}) FORMAT TabSeparated",
                            self.config.intraday_bar_table,
                            INTRADAY_BAR_CALCULATION_REVISION,
                            local_date,
                            BASE_RESOLUTION_US,
                            ticker_list,
                        ))
                        .await?,
                )?;
                if base_row_count == 0 {
                    return Err(format!(
                        "bounded intraday bootstrap produced zero base rows for {local_date} batch {batch_id}"
                    ));
                }
                self.query(&format!(
                    "INSERT INTO {BOOTSTRAP_STATE_TABLE} (calculation_revision, local_date, batch_id, batch_fingerprint, ticker_count, base_row_count, status, updated_at_utc) VALUES ('{revision}', toDate('{local_date}'), {batch_id}, '{batch_fingerprint}', {ticker_count}, {base_row_count}, 'complete', now64(3))",
                    revision = INTRADAY_BAR_CALCULATION_REVISION,
                    ticker_count = ticker_batch.len(),
                ))
                .await?;
                let output_row_count = parse_count(
                    &self
                        .query(&format!(
                            "SELECT count() FROM {} FINAL WHERE calculation_revision = '{}' AND complete = 1 AND local_date = toDate('{}') AND ticker IN ({}) FORMAT TabSeparated",
                            self.config.intraday_bar_table,
                            INTRADAY_BAR_CALCULATION_REVISION,
                            local_date,
                            ticker_list,
                        ))
                        .await?,
                )?;
                self.record_derived_coverage(
                    &local_date,
                    &format!("batch:{batch_id}:{batch_fingerprint}"),
                    source_row_count,
                    output_row_count,
                    "complete",
                    &format!("tickers={}", ticker_batch.len()),
                )
                .await?;
                completed_batches = completed_batches.saturating_add(1);
                newly_completed_batches = newly_completed_batches.saturating_add(1);
                if let Some(maintenance) = maintenance {
                    maintenance.complete_derived_batch(output_row_count).await;
                }
            }
            self.record_derived_coverage(
                &local_date,
                "session",
                0,
                0,
                "complete",
                &format!("completed_batches={planned_batches}"),
            )
            .await?;
        }
        if planned_batches == 0 || completed_batches != planned_batches {
            return Err(format!(
                "bounded intraday bootstrap incomplete: planned={planned_batches}, completed={completed_batches}, source_rows={source_rows}"
            ));
        }
        Ok(IntradayBarReconciliationSummary {
            completed_batches,
            newly_completed_batches,
            planned_batches,
            source_rows,
        })
    }

    async fn record_derived_coverage(
        &self,
        local_date: &str,
        scope_id: &str,
        source_row_count: u64,
        output_row_count: u64,
        status: &str,
        detail: &str,
    ) -> Result<(), String> {
        self.query(&format!(
            "INSERT INTO {} (product, local_date, scope_id, calculation_revision, source_revision, source_row_count, output_row_count, status, detail, updated_at_utc) VALUES ('intraday_family_bars', toDate('{}'), '{}', '{}', '{}', {}, {}, '{}', '{}', now64(3))",
            self.config.derived_coverage_table,
            escape_sql_string(local_date),
            escape_sql_string(scope_id),
            INTRADAY_BAR_CALCULATION_REVISION,
            escape_sql_string(&self.config.qmd_run_id),
            source_row_count,
            output_row_count,
            escape_sql_string(status),
            escape_sql_string(detail),
        ))
        .await
        .map(|_| ())
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
        self.bootstrap_base_sql_with_filter(&filter, "")
    }

    fn bootstrap_base_sql_with_filter(&self, filter: &str, source_filter: &str) -> String {
        format!(
            r#"INSERT INTO {target}
            (schema_version, ticker, local_date, label_resolution_us, bucket_index, bar_family,
             open, close, high, low, size_sum, size_open, size_close, size_high, size_low,
            event_count, first_event_timestamp_us, last_event_timestamp_us,
             bar_start_session_us, bar_end_session_us, calculation_revision, source_revision, complete)
            WITH
              fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)) AS event_ts_utc,
              toTimeZone(event_ts_utc, 'America/New_York') AS event_ts_local,
              toDate(event_ts_local) AS local_date_value,
              toInt64(sip_timestamp_us)
                - toUnixTimestamp64Micro(toDateTime64(toStartOfDay(event_ts_local), 6, 'America/New_York')) AS session_us,
              intDiv(session_us, {base}) AS bucket,
              tuple(sip_timestamp_us, source_sequence, bitAnd(event_meta, 1), arrival_sequence) AS event_order,
              (SELECT groupArray(toUInt16(token_id)) FROM {reference_db}.event_condition_token_reference
                WHERE source_family = 'trade_conditions' AND is_join_canonical = 1) AS known_tokens,
              (SELECT groupArray(toUInt16(token_id)) FROM {reference_db}.event_condition_token_reference
                WHERE source_family = 'trade_conditions' AND is_join_canonical = 1 AND update_high_low = 1) AS high_low_tokens,
              (SELECT groupArray(toUInt16(token_id)) FROM {reference_db}.event_condition_token_reference
                WHERE source_family = 'trade_conditions' AND is_join_canonical = 1 AND update_last = 1) AS last_tokens,
              (SELECT groupArray(toUInt16(token_id)) FROM {reference_db}.event_condition_token_reference
                WHERE source_family = 'trade_conditions' AND is_join_canonical = 1 AND update_volume = 1) AS volume_tokens,
              (SELECT groupArray(toUInt16(token_id)) FROM {reference_db}.event_condition_token_reference
                WHERE source_family = 'trade_conditions' AND is_join_canonical = 1 AND modifier_int = 12) AS form_t_tokens
            SELECT
              {schema_version}, ticker, local_date_value, {base}, bucket, bar_family,
              toFloat32(argMinIf(price, event_order, price_eligible)),
              toFloat32(argMaxIf(price, event_order, price_eligible)),
              toFloat32(maxIf(price, high_low_eligible)),
              toFloat32(minIf(price, high_low_eligible)),
              toFloat64(sumIf(size, volume_eligible)),
              toFloat64(argMinIf(size, event_order, volume_eligible)),
              toFloat64(argMaxIf(size, event_order, volume_eligible)),
              toFloat64(maxIf(size, volume_eligible)),
              toFloat64(minIf(size, volume_eligible)),
              toUInt64(countIf(bar_family != 'trade' OR volume_eligible)),
              toInt64(min(sip_timestamp_us)), toInt64(max(sip_timestamp_us)),
              bucket * {base}, (bucket + 1) * {base}, '{calculation_revision}', '{source_revision}', 1
            FROM
            (
              SELECT *, 'trade' AS bar_family,
                toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price,
                toFloat64(size_primary) AS size,
                arrayFilter(token -> token > 0, [toUInt16(condition_token_1), toUInt16(condition_token_2), toUInt16(condition_token_3), toUInt16(condition_token_4), toUInt16(condition_token_5)]) AS condition_tokens,
                (toHour(event_ts_local) < 9 OR (toHour(event_ts_local) = 9 AND toMinute(event_ts_local) < 30) OR toHour(event_ts_local) >= 16)
                  AND arrayExists(token -> has(form_t_tokens, token), condition_tokens)
                  AND arrayAll(token -> has(form_t_tokens, token) OR (has(high_low_tokens, token) AND has(last_tokens, token)), condition_tokens) AS form_t_price_eligible,
                empty(condition_tokens) OR arrayAll(token -> has(known_tokens, token) AND (has(high_low_tokens, token) OR (has(form_t_tokens, token) AND form_t_price_eligible)), condition_tokens) AS high_low_eligible,
                empty(condition_tokens) OR arrayAll(token -> has(known_tokens, token) AND (has(last_tokens, token) OR (has(form_t_tokens, token) AND form_t_price_eligible)), condition_tokens) AS last_eligible,
                empty(condition_tokens) OR arrayAll(token -> has(known_tokens, token) AND (has(volume_tokens, token) OR (has(form_t_tokens, token) AND form_t_price_eligible)), condition_tokens) AS volume_eligible,
                last_eligible AS price_eligible
              FROM {source} FINAL WHERE bitAnd(event_meta, 1) = 1{source_filter}
              UNION ALL
              SELECT *, 'quote_bid' AS bar_family,
                toFloat64(price_secondary_int) / if(bitAnd(event_meta, 4) != 0, 10000., 100.) AS price,
                toFloat64(size_secondary) AS size, [], false, true, true, true, true
              FROM {source} FINAL WHERE bitAnd(event_meta, 1) = 0{source_filter}
              UNION ALL
              SELECT *, 'quote_ask' AS bar_family,
                toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price,
                toFloat64(size_primary) AS size, [], false, true, true, true, true
              FROM {source} FINAL WHERE bitAnd(event_meta, 1) = 0{source_filter}
            )
            WHERE price > 0 AND size > 0
              AND (bar_family != 'trade' OR price_eligible OR volume_eligible)
              AND session_us >= {session_start} AND session_us < {session_end}{filter}
            GROUP BY ticker, local_date_value, bucket, bar_family"#,
            target = self.config.intraday_bar_table,
            source = self.config.compact_event_table,
            reference_db = self.config.historical_clickhouse_database,
            schema_version = INTRADAY_BAR_SCHEMA_VERSION,
            base = BASE_RESOLUTION_US,
            session_start = SESSION_START_US,
            session_end = SESSION_END_US,
            filter = filter,
            source_filter = source_filter,
            calculation_revision = INTRADAY_BAR_CALCULATION_REVISION,
            source_revision = escape_sql_string(&self.config.qmd_run_id),
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
        self.bootstrap_rollup_sql_with_filter(resolution_us, &filter)
    }

    fn bootstrap_rollup_sql_with_filter(&self, resolution_us: i64, filter: &str) -> String {
        format!(
            r#"INSERT INTO {table}
            (schema_version, ticker, local_date, label_resolution_us, bucket_index, bar_family,
             open, close, high, low, size_sum, size_open, size_close, size_high, size_low,
             event_count, first_event_timestamp_us, last_event_timestamp_us,
             bar_start_session_us, bar_end_session_us, calculation_revision, source_revision, complete)
            SELECT
              {schema_version}, ticker, local_date, {resolution},
              intDiv(bar_start_session_us, {resolution}) AS bucket, bar_family,
              argMinIf(open, bucket_index, open > 0), argMaxIf(close, bucket_index, close > 0),
              max(high), minIf(low, low > 0), sum(size_sum),
              argMinIf(size_open, bucket_index, size_open > 0),
              argMaxIf(size_close, bucket_index, size_close > 0),
              max(size_high), minIf(size_low, size_low > 0),
              toUInt64(sum(event_count)), min(first_event_timestamp_us), max(last_event_timestamp_us),
              bucket * {resolution}, (bucket + 1) * {resolution}, '{calculation_revision}', '{source_revision}', 1
            FROM {table} FINAL
            WHERE schema_version = {schema_version}
              AND calculation_revision = '{calculation_revision}' AND complete = 1
              AND label_resolution_us = {base}{filter}
            GROUP BY ticker, local_date, bucket, bar_family"#,
            table = self.config.intraday_bar_table,
            schema_version = INTRADAY_BAR_SCHEMA_VERSION,
            resolution = resolution_us,
            base = BASE_RESOLUTION_US,
            filter = filter,
            calculation_revision = INTRADAY_BAR_CALCULATION_REVISION,
            source_revision = escape_sql_string(&self.config.qmd_run_id),
        )
    }

    fn bootstrap_all_rollups_sql_with_filter(&self, filter: &str) -> Option<String> {
        let resolutions = self
            .resolutions
            .iter()
            .copied()
            .filter(|value| *value > BASE_RESOLUTION_US)
            .map(|value| value.to_string())
            .collect::<Vec<_>>();
        if resolutions.is_empty() {
            return None;
        }
        Some(format!(
            r#"INSERT INTO {table}
            (schema_version, ticker, local_date, label_resolution_us, bucket_index, bar_family,
             open, close, high, low, size_sum, size_open, size_close, size_high, size_low,
             event_count, first_event_timestamp_us, last_event_timestamp_us,
             bar_start_session_us, bar_end_session_us, calculation_revision, source_revision, complete)
            SELECT
              {schema_version}, ticker, local_date, target_resolution_us,
              intDiv(bar_start_session_us, target_resolution_us) AS bucket, bar_family,
              argMinIf(open, bucket_index, open > 0), argMaxIf(close, bucket_index, close > 0),
              max(high), minIf(low, low > 0), sum(size_sum),
              argMinIf(size_open, bucket_index, size_open > 0),
              argMaxIf(size_close, bucket_index, size_close > 0),
              max(size_high), minIf(size_low, size_low > 0),
              toUInt64(sum(event_count)), min(first_event_timestamp_us), max(last_event_timestamp_us),
              bucket * target_resolution_us, (bucket + 1) * target_resolution_us,
              '{calculation_revision}', '{source_revision}', 1
            FROM {table} FINAL
            ARRAY JOIN [{resolutions}] AS target_resolution_us
            WHERE schema_version = {schema_version}
              AND calculation_revision = '{calculation_revision}' AND complete = 1
              AND label_resolution_us = {base}{filter}
            GROUP BY ticker, local_date, target_resolution_us, bucket, bar_family"#,
            table = self.config.intraday_bar_table,
            schema_version = INTRADAY_BAR_SCHEMA_VERSION,
            resolutions = resolutions.join(","),
            base = BASE_RESOLUTION_US,
            filter = filter,
            calculation_revision = INTRADAY_BAR_CALCULATION_REVISION,
            source_revision = escape_sql_string(&self.config.qmd_run_id),
        ))
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

    async fn run(
        self,
        writer_id: usize,
        pending: IntradayWriterPending,
        mut receiver: mpsc::Receiver<WriterMessage>,
    ) {
        let mut batch = Vec::with_capacity(self.config.max_clickhouse_batch);
        let mut repairs = HashMap::<RepairRequest, Instant>::new();
        let mut tick = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                message = receiver.recv() => match message {
                    Some(WriterMessage::Row(row)) => batch.push(row),
                    Some(WriterMessage::Repair(request)) => {
                        repairs.remove(&request);
                        repairs.insert(
                            request,
                            Instant::now()
                                + Duration::from_millis(
                                    self.config.flush_interval_ms.saturating_mul(2),
                                ),
                        );
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
            pending.set(
                writer_id,
                (batch.len() + repairs.len() + receiver.len()) as u64,
            );
        }
    }

    async fn flush_repairs(&self, repairs: &mut HashMap<RepairRequest, Instant>, force: bool) {
        let now = Instant::now();
        let limit = if force { usize::MAX } else { 1 };
        let ready = repairs
            .iter()
            .filter(|(_, due)| force || **due <= now)
            .map(|(request, _)| request.clone())
            .take(limit)
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
                    "SELECT count() FROM {} WHERE ticker = '{}' AND sip_timestamp_us = {} AND source_sequence = {} AND bitAnd(event_meta, 1) = {} AND arrival_sequence = {} FORMAT TabSeparated",
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
                    "calculation_revision": INTRADAY_BAR_CALCULATION_REVISION,
                    "source_revision": self.config.qmd_run_id,
                    "complete": 1u8,
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
        let grouped = intraday_coverage_groups(rows);
        if grouped.is_empty() {
            return Ok(());
        }
        let now = Utc::now();
        let mut windows = self.coverage_windows.lock().await;
        let mut coverage_rows = Vec::with_capacity(grouped.len());
        for (local_date, batch) in grouped {
            let window = windows
                .entry(local_date.clone())
                .or_insert_with(|| CoverageWindow {
                    end: batch.end,
                    rows_written: 0,
                    start: batch.start,
                });
            window.start = window.start.min(batch.start);
            window.end = window.end.max(batch.end);
            window.rows_written = window.rows_written.saturating_add(batch.rows_written);
            coverage_rows.push(json!({
                "coverage_kind": "q_live_events",
                "coverage_id": format!("intraday_{}::{local_date}", self.config.qmd_run_id),
                "source": "qmd_intraday_bar_writer",
                "status": "intraday_bars_persisted",
                "coverage_start_utc": clickhouse_datetime64(&window.start),
                "coverage_end_utc": clickhouse_datetime64(&window.end),
                "rows_written": window.rows_written,
                "event_rows": 0u64,
                "bar_rows": window.rows_written,
                "error_count": 0u64,
                "started_at_utc": clickhouse_datetime64(&window.start),
                "updated_at_utc": clickhouse_datetime64(&now),
                "completed_at_utc": Option::<String>::None,
                "metadata_json": json!({
                    "table": self.config.intraday_bar_table,
                    "partition_local_date": local_date.clone(),
                    "market_session_date": local_date,
                    "base_resolution_us": BASE_RESOLUTION_US,
                    "rollup_resolutions_us": self.resolutions,
                    "coverage_rule": "base 100ms bars confirm the session partition; higher bars roll up from closed base bars"
                }).to_string(),
            }));
        }
        drop(windows);
        let body = coverage_rows
            .into_iter()
            .map(|row| row.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        self.query(&format!(
            "INSERT INTO {} FORMAT JSONEachRow\n{}",
            self.config.qmd_live_event_coverage_table, body
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

fn event_points(
    event: &LiveCompactEvent,
    decoder: &CompactEventDecoder,
    trade_rules: &TradeAggregationRules,
) -> Vec<EventPoint> {
    match decoder.decode(event) {
        MarketEvent::Trade(trade) if trade.price > 0.0 && trade.size > 0.0 => {
            let rule = trade_rules.resolve(&trade.conditions, trade.ts);
            (rule.update_high_low || rule.update_last || rule.update_volume)
                .then_some(EventPoint {
                    family: "trade",
                    price: trade.price as f32,
                    size: trade.size,
                    rule,
                })
                .into_iter()
                .collect()
        }
        MarketEvent::Quote(quote) => {
            let mut out = Vec::with_capacity(2);
            if quote.bid_price > 0.0 && quote.bid_size > 0 {
                out.push(EventPoint {
                    family: "quote_bid",
                    price: quote.bid_price as f32,
                    size: f64::from(quote.bid_size),
                    rule: TradeUpdateRule::regular(),
                });
            }
            if quote.ask_price > 0.0 && quote.ask_size > 0 {
                out.push(EventPoint {
                    family: "quote_ask",
                    price: quote.ask_price as f32,
                    size: f64::from(quote.ask_size),
                    rule: TradeUpdateRule::regular(),
                });
            }
            out
        }
        _ => Vec::new(),
    }
}

fn positive_min_f32(current: f32, candidate: f32) -> f32 {
    if current <= 0.0 {
        candidate
    } else if candidate <= 0.0 {
        current
    } else {
        current.min(candidate)
    }
}

fn positive_min_f64(current: f64, candidate: f64) -> f64 {
    if current <= 0.0 {
        candidate
    } else if candidate <= 0.0 {
        current
    } else {
        current.min(candidate)
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

    fn points(event: &LiveCompactEvent) -> Vec<EventPoint> {
        event_points(
            event,
            &CompactEventDecoder::default(),
            &TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap(),
        )
    }

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
    fn coverage_groups_only_base_rows_by_session_partition() {
        let first_event = quote_event(1_752_400_000_010_000, 1, 100_000, 101_000);
        let second_event = quote_event(1_752_486_400_010_000, 2, 102_000, 103_000);
        let first_point = points(&first_event).remove(0);
        let second_point = points(&second_event).remove(0);
        let first =
            IntradayBarRow::from_event(&first_event, &first_point, 144_000, "2026-07-13".into());
        let second =
            IntradayBarRow::from_event(&second_event, &second_point, 144_000, "2026-07-14".into());
        let rollup = IntradayBarRow::from_base(&first, 1_000_000);

        let groups = intraday_coverage_groups(&[first, second, rollup]);

        assert_eq!(groups.len(), 2);
        assert_eq!(groups["2026-07-13"].rows_written, 1);
        assert_eq!(groups["2026-07-14"].rows_written, 1);
    }

    #[test]
    fn quote_points_match_training_families_and_scales() {
        let points = points(&quote_event(1_752_400_000_000_000, 2, 101_200, 101_234));
        assert_eq!(points.len(), 2);
        assert_eq!(points[0].family, "quote_bid");
        assert!((points[0].price - 10.12).abs() < 0.0001);
        assert_eq!(points[1].family, "quote_ask");
    }

    #[test]
    fn trade_conditions_keep_price_and_volume_ordering_independent() {
        let mut first = quote_event(1_752_400_000_010_000, 1, 100_000, 101_000);
        first.event_meta = 0x03;
        first.price_primary_int = 100_000;
        first.size_primary = 10.0;
        let mut volume_only = first.clone();
        volume_only.arrival_sequence = 2;
        volume_only.source_sequence = 2;
        volume_only.sip_timestamp_us += 10_000;
        volume_only.price_primary_int = 200_000;
        volume_only.size_primary = 25.0;
        volume_only.condition_token_1 = 21;
        let decoder = CompactEventDecoder::new([], [(21, 2)], [], []);
        let rules = TradeAggregationRules::new([
            (0, TradeUpdateRule::regular()),
            (
                2,
                TradeUpdateRule {
                    update_high_low: false,
                    update_last: false,
                    update_volume: true,
                },
            ),
        ])
        .unwrap();
        let first_point = event_points(&first, &decoder, &rules).remove(0);
        let volume_point = event_points(&volume_only, &decoder, &rules).remove(0);
        let mut row =
            IntradayBarRow::from_event(&first, &first_point, 144_000, "2026-07-13".into());
        row.update_event(&volume_only, &volume_point);
        assert_eq!(row.open, 10.0);
        assert_eq!(row.close, 10.0);
        assert_eq!(row.high, 10.0);
        assert_eq!(row.low, 10.0);
        assert_eq!(row.size_sum, 35.0);
        assert_eq!(row.size_close, 25.0);
        assert_eq!(row.event_count, 2);
    }

    #[test]
    fn high_low_only_trade_does_not_change_open_or_close() {
        let mut regular = quote_event(1_752_400_000_010_000, 1, 100_000, 101_000);
        regular.event_meta = 0x03;
        regular.price_primary_int = 100_000;
        let mut high_low_only = regular.clone();
        high_low_only.arrival_sequence = 2;
        high_low_only.source_sequence = 2;
        high_low_only.sip_timestamp_us += 10_000;
        high_low_only.price_primary_int = 110_000;
        high_low_only.condition_token_1 = 22;
        let decoder = CompactEventDecoder::new([], [(22, 3)], [], []);
        let rules = TradeAggregationRules::new([
            (0, TradeUpdateRule::regular()),
            (
                3,
                TradeUpdateRule {
                    update_high_low: true,
                    update_last: false,
                    update_volume: false,
                },
            ),
        ])
        .unwrap();
        let regular_point = event_points(&regular, &decoder, &rules).remove(0);
        let high_low_point = event_points(&high_low_only, &decoder, &rules).remove(0);
        let mut row =
            IntradayBarRow::from_event(&regular, &regular_point, 144_000, "2026-07-13".into());
        row.update_event(&high_low_only, &high_low_point);
        assert_eq!(row.open, 10.0);
        assert_eq!(row.close, 10.0);
        assert_eq!(row.high, 11.0);
        assert_eq!(row.low, 10.0);
        assert_eq!(row.size_sum, 10.0);
        assert_eq!(row.event_count, 1);
    }

    #[test]
    fn parent_rollup_uses_closed_base_bar_algebra() {
        let first = quote_event(1_752_400_000_010_000, 1, 100_000, 101_000);
        let second = quote_event(1_752_400_000_090_000, 2, 99_000, 102_000);
        let point1 = points(&first).remove(0);
        let point2 = points(&second).remove(0);
        let mut base = IntradayBarRow::from_event(&first, &point1, 144_000, "2026-07-13".into());
        base.update_event(&second, &point2);
        let mut parent = IntradayBarRow::from_base(&base, 1_000_000);
        let third = quote_event(1_752_400_000_110_000, 3, 103_000, 104_000);
        let point3 = points(&third).remove(0);
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
        let point = points(&event).remove(0);
        let base = IntradayBarRow::from_event(&event, &point, 144_000, "2026-07-13".into());
        let key = (
            base.ticker.clone(),
            base.local_date.clone(),
            BASE_RESOLUTION_US,
            base.bucket_index,
            base.bar_family,
        );
        let mut base_bars = BTreeMap::from([(key.clone(), base)]);
        let mut base_seen = HashMap::from([(key, HashSet::from([event_identity(&event)]))]);
        let mut rollups = BTreeMap::new();
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

    #[test]
    fn late_repair_identity_collapses_events_for_the_same_bucket() {
        let first = RepairRequest {
            ticker: "AAPL".into(),
            local_date: "2026-08-11".into(),
            bucket_index: 42,
            sip_timestamp_us: 100,
            source_sequence: 1,
            event_type: 0,
            arrival_sequence: 10,
        };
        let mut later = first.clone();
        later.sip_timestamp_us = 199;
        later.source_sequence = 9;
        later.event_type = 1;
        later.arrival_sequence = 20;
        let requests = HashSet::from([first, later]);
        assert_eq!(requests.len(), 1);
    }
}
