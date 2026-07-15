use crate::bars::{TradeAggregationRules, TradeUpdateRule};
use crate::event::{MarketEvent, QuoteEvent};
use chrono::{DateTime, Datelike, Duration, NaiveDate, TimeZone, Timelike, Utc, Weekday};
use chrono_tz::America::New_York;
use serde::Serialize;
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Arc;
use tokio::sync::Mutex;

pub const MARKET_PRODUCT_SCHEMA_VERSION: u16 = 1;
pub const SESSION_START_US: u64 = 4 * 60 * 60 * 1_000_000;
pub const SESSION_END_US: u64 = 20 * 60 * 60 * 1_000_000;
pub const DEFAULT_PRODUCT_RESOLUTIONS_US: &[u64] = &[
    100_000,
    1_000_000,
    5_000_000,
    10_000_000,
    30_000_000,
    60_000_000,
    300_000_000,
    3_600_000_000,
];

pub fn parse_resolution_us(value: &str) -> Option<u64> {
    let normalized = value.trim().to_ascii_lowercase();
    if let Some(raw) = normalized.strip_suffix("ms") {
        return raw.trim().parse::<u64>().ok()?.checked_mul(1_000);
    }
    if let Some(raw) = normalized.strip_suffix('s') {
        return raw.trim().parse::<u64>().ok()?.checked_mul(1_000_000);
    }
    if let Some(raw) = normalized.strip_suffix('m') {
        return raw.trim().parse::<u64>().ok()?.checked_mul(60_000_000);
    }
    if let Some(raw) = normalized.strip_suffix('h') {
        return raw.trim().parse::<u64>().ok()?.checked_mul(3_600_000_000);
    }
    normalized.parse::<u64>().ok()
}

const ESTIMATED_FAMILY_ROW_BYTES: usize = 256;
const ESTIMATED_CONDITION_ROW_BYTES: usize = 160;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProductState {
    Partial,
    Closed,
    Corrected,
}

#[derive(Clone, Debug, Serialize)]
pub struct FamilyBarRow {
    pub schema_version: u16,
    pub local_date: String,
    pub ticker: String,
    pub label_resolution_us: u64,
    pub bucket_index: u64,
    pub bar_family: String,
    pub open: f32,
    pub close: f32,
    pub high: f32,
    pub low: f32,
    pub size_sum: f64,
    pub size_open: f64,
    pub size_close: f64,
    pub size_high: f64,
    pub size_low: f64,
    pub event_count: u64,
    pub first_event_timestamp_us: u64,
    pub last_event_timestamp_us: u64,
    pub bar_start: DateTime<Utc>,
    pub bar_end: DateTime<Utc>,
    pub as_of: DateTime<Utc>,
    pub revision: u64,
    pub state: ProductState,
}

#[derive(Clone, Debug, Serialize)]
pub struct ConditionBarRow {
    pub schema_version: u16,
    pub local_date: String,
    pub ticker: String,
    pub label_resolution_us: u64,
    pub bucket_index: u64,
    pub condition_halt_pause_flag: u8,
    pub condition_resume_flag: u8,
    pub condition_news_risk_flag: u8,
    pub condition_luld_limit_state_flag: u8,
    pub condition_event_count: u64,
    pub first_event_timestamp_us: u64,
    pub last_event_timestamp_us: u64,
    pub bar_start: DateTime<Utc>,
    pub bar_end: DateTime<Utc>,
    pub as_of: DateTime<Utc>,
    pub revision: u64,
    pub state: ProductState,
}

#[derive(Clone, Debug, Serialize)]
pub struct MacroFamilyBarRow {
    pub schema_version: u16,
    pub session_date: String,
    pub timeframe: String,
    pub ticker: String,
    pub bar_family: String,
    pub bar_start: DateTime<Utc>,
    pub bar_end: DateTime<Utc>,
    pub open: f32,
    pub close: f32,
    pub high: f32,
    pub low: f32,
    pub size_sum: f64,
    pub size_open: f64,
    pub size_close: f64,
    pub size_high: f64,
    pub size_low: f64,
    pub event_count: u64,
    pub first_event_timestamp_us: u64,
    pub last_event_timestamp_us: u64,
    pub as_of: DateTime<Utc>,
    pub revision: u64,
    pub state: ProductState,
}

#[derive(Clone, Debug, Serialize)]
pub struct FamilyBarSnapshot {
    pub as_of: DateTime<Utc>,
    pub rows: Vec<FamilyBarRow>,
    pub ticker: String,
    pub resolution_us: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct ConditionBarSnapshot {
    pub as_of: DateTime<Utc>,
    pub rows: Vec<ConditionBarRow>,
    pub ticker: String,
    pub resolution_us: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct MacroBarSnapshot {
    pub as_of: DateTime<Utc>,
    pub rows: Vec<MacroFamilyBarRow>,
    pub ticker: String,
    pub timeframe: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct ProductCacheMetrics {
    pub estimated_bytes: usize,
    pub evictions: u64,
    pub family_rows: usize,
    pub condition_rows: usize,
    pub max_bytes: usize,
    pub max_partitions: usize,
    pub max_rows: usize,
    pub partitions: usize,
}

#[derive(Clone, Debug)]
pub struct ProductCacheLimits {
    pub max_bytes: usize,
    pub max_partitions: usize,
    pub max_rows: usize,
}

impl ProductCacheLimits {
    pub fn normalized(self) -> Self {
        Self {
            max_bytes: self.max_bytes.max(1024 * 1024),
            max_partitions: self.max_partitions.max(1),
            max_rows: self.max_rows.max(1_000),
        }
    }
}

#[derive(Clone, Default)]
pub struct ConditionClassifier {
    halt_quote_conditions: Arc<HashSet<u16>>,
    resume_quote_conditions: Arc<HashSet<u16>>,
    news_quote_conditions: Arc<HashSet<u16>>,
    luld_quote_conditions: Arc<HashSet<u16>>,
    halt_quote_indicators: Arc<HashSet<u16>>,
    resume_quote_indicators: Arc<HashSet<u16>>,
    news_quote_indicators: Arc<HashSet<u16>>,
    luld_quote_indicators: Arc<HashSet<u16>>,
}

impl ConditionClassifier {
    pub fn training_aligned() -> Self {
        Self {
            halt_quote_conditions: Arc::new([43].into_iter().collect()),
            resume_quote_conditions: Arc::new([16].into_iter().collect()),
            news_quote_conditions: Arc::new([21, 23, 25, 27].into_iter().collect()),
            luld_quote_conditions: Arc::new([35, 39, 43].into_iter().collect()),
            halt_quote_indicators: Arc::new(
                [
                    17, 102, 114, 117, 153, 154, 155, 156, 157, 158, 159, 160, 161, 163, 165, 166,
                    168, 184, 186,
                ]
                .into_iter()
                .collect(),
            ),
            resume_quote_indicators: Arc::new(
                [103, 169, 170, 171, 172, 173, 174, 178]
                    .into_iter()
                    .collect(),
            ),
            news_quote_indicators: Arc::new([151, 152, 167].into_iter().collect()),
            luld_quote_indicators: Arc::new(
                [
                    11, 12, 22, 23, 24, 25, 26, 27, 28, 29, 30, 114, 153, 165, 166, 186,
                ]
                .into_iter()
                .collect(),
            ),
        }
    }

    fn classify(&self, event: &MarketEvent) -> ConditionFlags {
        let MarketEvent::Quote(quote) = event else {
            return ConditionFlags::default();
        };
        ConditionFlags {
            halt: intersects(&quote.conditions, &self.halt_quote_conditions)
                || intersects(&quote.indicators, &self.halt_quote_indicators),
            resume: intersects(&quote.conditions, &self.resume_quote_conditions)
                || intersects(&quote.indicators, &self.resume_quote_indicators),
            news: intersects(&quote.conditions, &self.news_quote_conditions)
                || intersects(&quote.indicators, &self.news_quote_indicators),
            luld: intersects(&quote.conditions, &self.luld_quote_conditions)
                || intersects(&quote.indicators, &self.luld_quote_indicators),
        }
    }
}

#[derive(Clone)]
pub struct SharedMarketProductStore {
    shards: Arc<Vec<Arc<Mutex<MarketProductEngine>>>>,
}

pub struct MarketProductEngine {
    classifier: ConditionClassifier,
    evictions: u64,
    limits: ProductCacheLimits,
    order: VecDeque<PartitionKey>,
    partitions: HashMap<PartitionKey, ProductPartition>,
    resolutions_us: Vec<u64>,
    trade_rules: TradeAggregationRules,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct PartitionKey {
    ticker: String,
    local_date: String,
}

#[derive(Default)]
struct ProductPartition {
    family: HashMap<FamilyKey, FamilyAccumulator>,
    conditions: HashMap<ConditionKey, ConditionBarRow>,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct FamilyKey {
    resolution_us: u64,
    bucket_index: u64,
    family: &'static str,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct ConditionKey {
    resolution_us: u64,
    bucket_index: u64,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct EventOrder(u64, u64, u8);

struct FamilyAccumulator {
    first_key: EventOrder,
    last_key: EventOrder,
    row: FamilyBarRow,
}

#[derive(Clone, Copy, Default)]
struct ConditionFlags {
    halt: bool,
    resume: bool,
    news: bool,
    luld: bool,
}

#[derive(Clone, Copy)]
struct FamilyPoint {
    family: &'static str,
    price: f64,
    size: f64,
    rule: TradeUpdateRule,
}

struct SessionCoordinate {
    local_date: String,
    local_date_value: NaiveDate,
    local_session_us: u64,
}

impl SharedMarketProductStore {
    pub fn new(
        resolutions_us: Vec<u64>,
        limits: ProductCacheLimits,
        shard_count: usize,
        trade_rules: TradeAggregationRules,
        classifier: ConditionClassifier,
    ) -> Self {
        let count = shard_count.max(1);
        let limits = limits.normalized();
        let shard_limits = ProductCacheLimits {
            max_bytes: limits.max_bytes.div_ceil(count),
            max_partitions: limits.max_partitions.div_ceil(count),
            max_rows: limits.max_rows.div_ceil(count),
        };
        let shards = (0..count)
            .map(|_| {
                Arc::new(Mutex::new(MarketProductEngine::new(
                    resolutions_us.clone(),
                    shard_limits.clone(),
                    trade_rules.clone(),
                    classifier.clone(),
                )))
            })
            .collect();
        Self {
            shards: Arc::new(shards),
        }
    }

    pub async fn apply_event(&self, event: &MarketEvent, as_of: DateTime<Utc>) {
        let index = stable_hash(event.ticker()) as usize % self.shards.len();
        self.shards[index].lock().await.apply_event(event, as_of);
    }

    pub async fn family_snapshot(
        &self,
        ticker: &str,
        resolution_us: u64,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> FamilyBarSnapshot {
        let index = stable_hash(ticker) as usize % self.shards.len();
        self.shards[index]
            .lock()
            .await
            .family_snapshot(ticker, resolution_us, limit, as_of)
    }

    pub async fn family_snapshot_for(
        &self,
        ticker: &str,
        resolution_us: u64,
        bar_family: &str,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> FamilyBarSnapshot {
        let index = stable_hash(ticker) as usize % self.shards.len();
        self.shards[index].lock().await.family_snapshot_for_before(
            ticker,
            resolution_us,
            Some(bar_family),
            limit,
            as_of,
            None,
        )
    }

    pub async fn trade_price_snapshot(
        &self,
        ticker: &str,
        resolution_us: u64,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> FamilyBarSnapshot {
        let index = stable_hash(ticker) as usize % self.shards.len();
        self.shards[index]
            .lock()
            .await
            .trade_price_snapshot_for_before(ticker, resolution_us, limit, as_of, None)
    }

    pub async fn condition_snapshot(
        &self,
        ticker: &str,
        resolution_us: u64,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> ConditionBarSnapshot {
        let index = stable_hash(ticker) as usize % self.shards.len();
        self.shards[index]
            .lock()
            .await
            .condition_snapshot(ticker, resolution_us, limit, as_of)
    }

    pub async fn macro_snapshot(
        &self,
        ticker: &str,
        timeframe: &str,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> MacroBarSnapshot {
        let index = stable_hash(ticker) as usize % self.shards.len();
        self.shards[index]
            .lock()
            .await
            .macro_snapshot(ticker, timeframe, limit, as_of)
    }

    pub async fn metrics(&self) -> ProductCacheMetrics {
        let mut total = ProductCacheMetrics {
            estimated_bytes: 0,
            evictions: 0,
            family_rows: 0,
            condition_rows: 0,
            max_bytes: 0,
            max_partitions: 0,
            max_rows: 0,
            partitions: 0,
        };
        for shard in self.shards.iter() {
            let metrics = shard.lock().await.metrics();
            total.estimated_bytes += metrics.estimated_bytes;
            total.evictions += metrics.evictions;
            total.family_rows += metrics.family_rows;
            total.condition_rows += metrics.condition_rows;
            total.max_bytes += metrics.max_bytes;
            total.max_partitions += metrics.max_partitions;
            total.max_rows += metrics.max_rows;
            total.partitions += metrics.partitions;
        }
        total
    }
}

impl MarketProductEngine {
    pub fn new(
        mut resolutions_us: Vec<u64>,
        limits: ProductCacheLimits,
        trade_rules: TradeAggregationRules,
        classifier: ConditionClassifier,
    ) -> Self {
        resolutions_us.retain(|value| *value > 0);
        resolutions_us.sort_unstable();
        resolutions_us.dedup();
        if resolutions_us.is_empty() {
            resolutions_us.extend_from_slice(DEFAULT_PRODUCT_RESOLUTIONS_US);
        }
        Self {
            classifier,
            evictions: 0,
            limits: limits.normalized(),
            order: VecDeque::new(),
            partitions: HashMap::new(),
            resolutions_us,
            trade_rules,
        }
    }

    pub fn apply_event(&mut self, event: &MarketEvent, as_of: DateTime<Utc>) {
        let Some(coordinate) = session_coordinate(event.ts()) else {
            return;
        };
        let ticker = event.ticker().to_ascii_uppercase();
        let partition_key = PartitionKey {
            ticker: ticker.clone(),
            local_date: coordinate.local_date.clone(),
        };
        touch(&mut self.order, &partition_key);
        let points = family_points(event, &self.trade_rules);
        let flags = self.classifier.classify(event);
        let resolutions = self.resolutions_us.clone();
        let partition = self.partitions.entry(partition_key.clone()).or_default();
        for resolution_us in resolutions {
            let bucket_index = coordinate.local_session_us / resolution_us;
            let Some((bar_start, bar_end)) =
                bucket_bounds(coordinate.local_date_value, bucket_index, resolution_us)
            else {
                continue;
            };
            for point in &points {
                if !point_is_relevant(point) {
                    continue;
                }
                let key = FamilyKey {
                    resolution_us,
                    bucket_index,
                    family: point.family,
                };
                let order = EventOrder(
                    event.ts().timestamp_micros().max(0) as u64,
                    event_sequence(event),
                    family_order(point.family),
                );
                match partition.family.get_mut(&key) {
                    Some(row) => row.update(event, point, order, as_of),
                    None => {
                        partition.family.insert(
                            key,
                            FamilyAccumulator::new(
                                &ticker,
                                &coordinate.local_date,
                                resolution_us,
                                bucket_index,
                                bar_start,
                                bar_end,
                                event,
                                point,
                                order,
                                as_of,
                            ),
                        );
                    }
                }
            }
            if flags.any() {
                let key = ConditionKey {
                    resolution_us,
                    bucket_index,
                };
                let timestamp_us = event.ts().timestamp_micros().max(0) as u64;
                match partition.conditions.get_mut(&key) {
                    Some(row) => update_condition(row, flags, timestamp_us, as_of),
                    None => {
                        partition.conditions.insert(
                            key,
                            ConditionBarRow {
                                schema_version: MARKET_PRODUCT_SCHEMA_VERSION,
                                local_date: coordinate.local_date.clone(),
                                ticker: ticker.clone(),
                                label_resolution_us: resolution_us,
                                bucket_index,
                                condition_halt_pause_flag: flags.halt as u8,
                                condition_resume_flag: flags.resume as u8,
                                condition_news_risk_flag: flags.news as u8,
                                condition_luld_limit_state_flag: flags.luld as u8,
                                condition_event_count: 1,
                                first_event_timestamp_us: timestamp_us,
                                last_event_timestamp_us: timestamp_us,
                                bar_start,
                                bar_end,
                                as_of,
                                revision: 1,
                                state: state_for(bar_end, as_of, false),
                            },
                        );
                    }
                }
            }
        }
        self.enforce_limits(Some(&partition_key));
    }

    pub fn family_snapshot(
        &mut self,
        ticker: &str,
        resolution_us: u64,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> FamilyBarSnapshot {
        self.family_snapshot_before(ticker, resolution_us, limit, as_of, None)
    }

    pub fn family_snapshot_before(
        &mut self,
        ticker: &str,
        resolution_us: u64,
        limit: usize,
        as_of: DateTime<Utc>,
        before: Option<DateTime<Utc>>,
    ) -> FamilyBarSnapshot {
        self.family_snapshot_for_before(ticker, resolution_us, None, limit, as_of, before)
    }

    pub fn family_snapshot_for_before(
        &mut self,
        ticker: &str,
        resolution_us: u64,
        bar_family: Option<&str>,
        limit: usize,
        as_of: DateTime<Utc>,
        before: Option<DateTime<Utc>>,
    ) -> FamilyBarSnapshot {
        let ticker = ticker.to_ascii_uppercase();
        let mut rows = self
            .partitions
            .iter_mut()
            .filter(|(key, _)| key.ticker == ticker)
            .flat_map(|(_, partition)| partition.family.values_mut())
            .filter(|entry| entry.row.label_resolution_us == resolution_us)
            .filter(|entry| bar_family.is_none_or(|family| entry.row.bar_family == family))
            .filter(|entry| before.is_none_or(|bound| entry.row.bar_start < bound))
            .map(|entry| {
                refresh_family_state(&mut entry.row, as_of);
                entry.row.clone()
            })
            .collect::<Vec<_>>();
        rows.sort_by_key(|row| (row.bar_start, row.bar_family.clone()));
        retain_tail(&mut rows, limit);
        FamilyBarSnapshot {
            as_of,
            rows,
            ticker,
            resolution_us,
        }
    }

    pub fn trade_price_snapshot_for_before(
        &mut self,
        ticker: &str,
        resolution_us: u64,
        limit: usize,
        as_of: DateTime<Utc>,
        before: Option<DateTime<Utc>>,
    ) -> FamilyBarSnapshot {
        let ticker = ticker.to_ascii_uppercase();
        let mut rows = self
            .partitions
            .iter_mut()
            .filter(|(key, _)| key.ticker == ticker)
            .flat_map(|(_, partition)| partition.family.values_mut())
            .filter(|entry| entry.row.label_resolution_us == resolution_us)
            .filter(|entry| entry.row.bar_family == "trade")
            .filter(|entry| before.is_none_or(|bound| entry.row.bar_start < bound))
            .filter_map(|entry| {
                refresh_family_state(&mut entry.row, as_of);
                valid_trade_price_bar(&entry.row).then(|| entry.row.clone())
            })
            .collect::<Vec<_>>();
        rows.sort_by_key(|row| row.bar_start);
        retain_tail(&mut rows, limit);
        FamilyBarSnapshot {
            as_of,
            rows,
            ticker,
            resolution_us,
        }
    }

    pub fn condition_snapshot(
        &mut self,
        ticker: &str,
        resolution_us: u64,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> ConditionBarSnapshot {
        let ticker = ticker.to_ascii_uppercase();
        let mut rows = self
            .partitions
            .iter_mut()
            .filter(|(key, _)| key.ticker == ticker)
            .flat_map(|(_, partition)| partition.conditions.values_mut())
            .filter(|row| row.label_resolution_us == resolution_us)
            .map(|row| {
                refresh_condition_state(row, as_of);
                row.clone()
            })
            .collect::<Vec<_>>();
        rows.sort_by_key(|row| row.bar_start);
        retain_tail(&mut rows, limit);
        ConditionBarSnapshot {
            as_of,
            rows,
            ticker,
            resolution_us,
        }
    }

    pub fn macro_snapshot(
        &mut self,
        ticker: &str,
        timeframe: &str,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> MacroBarSnapshot {
        let ticker = ticker.to_ascii_uppercase();
        let timeframe = canonical_macro_timeframe(timeframe);
        let daily_resolution = self
            .resolutions_us
            .iter()
            .copied()
            .max()
            .unwrap_or(60_000_000);
        let family = self.family_snapshot(&ticker, daily_resolution, usize::MAX, as_of);
        let mut groups: HashMap<(String, &'static str), Vec<FamilyBarRow>> = HashMap::new();
        for row in family.rows {
            let Some(date) = NaiveDate::parse_from_str(&row.local_date, "%Y-%m-%d").ok() else {
                continue;
            };
            let period = macro_period_label(date, &timeframe);
            let family_name = match row.bar_family.as_str() {
                "trade" => "trade",
                "quote_bid" => "quote_bid",
                "quote_ask" => "quote_ask",
                _ => continue,
            };
            groups.entry((period, family_name)).or_default().push(row);
        }
        let mut rows = groups
            .into_iter()
            .filter_map(|((period, family), mut values)| {
                values.sort_by_key(|row| (row.bar_start, row.first_event_timestamp_us));
                macro_from_family_rows(&ticker, &timeframe, &period, family, &values, as_of)
            })
            .collect::<Vec<_>>();
        rows.sort_by_key(|row| (row.bar_start, row.bar_family.clone()));
        retain_tail(&mut rows, limit);
        MacroBarSnapshot {
            as_of,
            rows,
            ticker,
            timeframe,
        }
    }

    pub fn metrics(&self) -> ProductCacheMetrics {
        let family_rows = self.partitions.values().map(|p| p.family.len()).sum();
        let condition_rows = self.partitions.values().map(|p| p.conditions.len()).sum();
        ProductCacheMetrics {
            estimated_bytes: family_rows * ESTIMATED_FAMILY_ROW_BYTES
                + condition_rows * ESTIMATED_CONDITION_ROW_BYTES,
            evictions: self.evictions,
            family_rows,
            condition_rows,
            max_bytes: self.limits.max_bytes,
            max_partitions: self.limits.max_partitions,
            max_rows: self.limits.max_rows,
            partitions: self.partitions.len(),
        }
    }

    fn enforce_limits(&mut self, protected: Option<&PartitionKey>) {
        loop {
            let metrics = self.metrics();
            if metrics.partitions <= self.limits.max_partitions
                && metrics.family_rows + metrics.condition_rows <= self.limits.max_rows
                && metrics.estimated_bytes <= self.limits.max_bytes
            {
                break;
            }
            let position = self
                .order
                .iter()
                .position(|key| protected.is_none_or(|value| value != key))
                .or_else(|| (!self.order.is_empty()).then_some(0));
            let Some(position) = position else { break };
            let Some(key) = self.order.remove(position) else {
                break;
            };
            if self.partitions.remove(&key).is_some() {
                self.evictions += 1;
            }
        }
    }
}

impl FamilyAccumulator {
    #[allow(clippy::too_many_arguments)]
    fn new(
        ticker: &str,
        local_date: &str,
        resolution_us: u64,
        bucket_index: u64,
        bar_start: DateTime<Utc>,
        bar_end: DateTime<Utc>,
        event: &MarketEvent,
        point: &FamilyPoint,
        order: EventOrder,
        as_of: DateTime<Utc>,
    ) -> Self {
        let timestamp_us = event.ts().timestamp_micros().max(0) as u64;
        let include_price =
            point.family != "trade" || point.rule.update_last || point.rule.update_high_low;
        let include_size = point.family != "trade" || point.rule.update_volume;
        let price = if include_price {
            point.price as f32
        } else {
            0.0
        };
        let size = if include_size { point.size } else { 0.0 };
        Self {
            first_key: order,
            last_key: order,
            row: FamilyBarRow {
                schema_version: MARKET_PRODUCT_SCHEMA_VERSION,
                local_date: local_date.to_string(),
                ticker: ticker.to_string(),
                label_resolution_us: resolution_us,
                bucket_index,
                bar_family: point.family.to_string(),
                open: price,
                close: price,
                high: price,
                low: price,
                size_sum: size,
                size_open: size,
                size_close: size,
                size_high: size,
                size_low: size,
                event_count: 1,
                first_event_timestamp_us: timestamp_us,
                last_event_timestamp_us: timestamp_us,
                bar_start,
                bar_end,
                as_of,
                revision: 1,
                state: state_for(bar_end, as_of, false),
            },
        }
    }

    fn update(
        &mut self,
        event: &MarketEvent,
        point: &FamilyPoint,
        order: EventOrder,
        as_of: DateTime<Utc>,
    ) {
        let was_closed = self.row.state != ProductState::Partial;
        let timestamp_us = event.ts().timestamp_micros().max(0) as u64;
        let include_price =
            point.family != "trade" || point.rule.update_last || point.rule.update_high_low;
        let include_size = point.family != "trade" || point.rule.update_volume;
        let is_earlier = order < self.first_key;
        let is_later = order > self.last_key;
        if include_price {
            let price = point.price as f32;
            if self.row.open <= 0.0 || is_earlier {
                self.first_key = order;
                self.row.open = price;
                self.row.first_event_timestamp_us = timestamp_us;
            }
            if self.row.close <= 0.0 || is_later {
                self.last_key = order;
                self.row.close = price;
                self.row.last_event_timestamp_us = timestamp_us;
            }
            if point.family != "trade" || point.rule.update_high_low {
                self.row.high = self.row.high.max(price);
                self.row.low = positive_min_f32(self.row.low, price);
            }
        }
        if include_size {
            self.row.size_sum += point.size;
            if is_earlier {
                self.row.size_open = point.size;
            }
            if is_later {
                self.row.size_close = point.size;
            }
            self.row.size_high = self.row.size_high.max(point.size);
            self.row.size_low = positive_min_f64(self.row.size_low, point.size);
        }
        self.row.event_count += 1;
        self.row.first_event_timestamp_us = self.row.first_event_timestamp_us.min(timestamp_us);
        self.row.last_event_timestamp_us = self.row.last_event_timestamp_us.max(timestamp_us);
        self.row.as_of = as_of;
        self.row.revision += 1;
        self.row.state = state_for(self.row.bar_end, as_of, was_closed);
    }
}

impl ConditionFlags {
    fn any(self) -> bool {
        self.halt || self.resume || self.news || self.luld
    }
}

fn family_points(event: &MarketEvent, rules: &TradeAggregationRules) -> Vec<FamilyPoint> {
    match event {
        MarketEvent::Trade(trade) if trade.price > 0.0 && trade.size > 0.0 => vec![FamilyPoint {
            family: "trade",
            price: trade.price,
            size: trade.size,
            rule: rules.resolve(&trade.conditions, trade.ts),
        }],
        MarketEvent::Quote(quote) => quote_points(quote),
        _ => Vec::new(),
    }
}

fn quote_points(quote: &QuoteEvent) -> Vec<FamilyPoint> {
    let regular = TradeUpdateRule::regular();
    let mut points = Vec::with_capacity(2);
    if quote.bid_price > 0.0 && quote.bid_size > 0 {
        points.push(FamilyPoint {
            family: "quote_bid",
            price: quote.bid_price,
            size: f64::from(quote.bid_size),
            rule: regular,
        });
    }
    if quote.ask_price > 0.0 && quote.ask_size > 0 {
        points.push(FamilyPoint {
            family: "quote_ask",
            price: quote.ask_price,
            size: f64::from(quote.ask_size),
            rule: regular,
        });
    }
    points
}

fn point_is_relevant(point: &FamilyPoint) -> bool {
    point.price > 0.0
        && point.size > 0.0
        && (point.family != "trade"
            || point.rule.update_high_low
            || point.rule.update_last
            || point.rule.update_volume)
}

fn session_coordinate(timestamp: DateTime<Utc>) -> Option<SessionCoordinate> {
    let local = timestamp.with_timezone(&New_York);
    let local_session_us = u64::from(local.num_seconds_from_midnight()) * 1_000_000
        + u64::from(local.nanosecond() / 1_000);
    if !(SESSION_START_US..SESSION_END_US).contains(&local_session_us) {
        return None;
    }
    let date = local.date_naive();
    Some(SessionCoordinate {
        local_date: date.format("%Y-%m-%d").to_string(),
        local_date_value: date,
        local_session_us,
    })
}

fn bucket_bounds(
    local_date: NaiveDate,
    bucket_index: u64,
    resolution_us: u64,
) -> Option<(DateTime<Utc>, DateTime<Utc>)> {
    let start_us = bucket_index.checked_mul(resolution_us)?;
    let end_us = start_us.checked_add(resolution_us)?;
    let midnight = New_York
        .with_ymd_and_hms(
            local_date.year(),
            local_date.month(),
            local_date.day(),
            0,
            0,
            0,
        )
        .single()?;
    let start = midnight + Duration::microseconds(i64::try_from(start_us).ok()?);
    let end = midnight + Duration::microseconds(i64::try_from(end_us).ok()?);
    Some((start.with_timezone(&Utc), end.with_timezone(&Utc)))
}

fn update_condition(
    row: &mut ConditionBarRow,
    flags: ConditionFlags,
    timestamp_us: u64,
    as_of: DateTime<Utc>,
) {
    let was_closed = row.state != ProductState::Partial;
    row.condition_halt_pause_flag |= flags.halt as u8;
    row.condition_resume_flag |= flags.resume as u8;
    row.condition_news_risk_flag |= flags.news as u8;
    row.condition_luld_limit_state_flag |= flags.luld as u8;
    row.condition_event_count += 1;
    row.first_event_timestamp_us = row.first_event_timestamp_us.min(timestamp_us);
    row.last_event_timestamp_us = row.last_event_timestamp_us.max(timestamp_us);
    row.as_of = as_of;
    row.revision += 1;
    row.state = state_for(row.bar_end, as_of, was_closed);
}

fn refresh_family_state(row: &mut FamilyBarRow, as_of: DateTime<Utc>) {
    if row.state == ProductState::Partial && row.bar_end <= as_of {
        row.state = ProductState::Closed;
        row.as_of = as_of;
        row.revision += 1;
    }
}

fn refresh_condition_state(row: &mut ConditionBarRow, as_of: DateTime<Utc>) {
    if row.state == ProductState::Partial && row.bar_end <= as_of {
        row.state = ProductState::Closed;
        row.as_of = as_of;
        row.revision += 1;
    }
}

fn state_for(bar_end: DateTime<Utc>, as_of: DateTime<Utc>, was_closed: bool) -> ProductState {
    if was_closed {
        ProductState::Corrected
    } else if bar_end <= as_of {
        ProductState::Closed
    } else {
        ProductState::Partial
    }
}

fn canonical_macro_timeframe(value: &str) -> String {
    match value.trim().to_ascii_lowercase().as_str() {
        "1w" | "week" | "weekly" => "1w".to_string(),
        "1y" | "year" | "yearly" => "1y".to_string(),
        _ => "1d".to_string(),
    }
}

fn macro_period_label(date: NaiveDate, timeframe: &str) -> String {
    match timeframe {
        "1w" => {
            let offset = match date.weekday() {
                Weekday::Mon => 0,
                Weekday::Tue => 1,
                Weekday::Wed => 2,
                Weekday::Thu => 3,
                Weekday::Fri => 4,
                Weekday::Sat => 5,
                Weekday::Sun => 6,
            };
            (date - Duration::days(offset))
                .format("%Y-%m-%d")
                .to_string()
        }
        "1y" => format!("{:04}-01-01", date.year()),
        _ => date.format("%Y-%m-%d").to_string(),
    }
}

fn macro_from_family_rows(
    ticker: &str,
    timeframe: &str,
    period: &str,
    family: &str,
    rows: &[FamilyBarRow],
    as_of: DateTime<Utc>,
) -> Option<MacroFamilyBarRow> {
    let first = rows.iter().find(|row| row.open > 0.0)?;
    let last = rows.iter().rev().find(|row| row.close > 0.0)?;
    let bar_start = rows.iter().map(|row| row.bar_start).min()?;
    let bar_end = rows.iter().map(|row| row.bar_end).max()?;
    Some(MacroFamilyBarRow {
        schema_version: MARKET_PRODUCT_SCHEMA_VERSION,
        session_date: period.to_string(),
        timeframe: timeframe.to_string(),
        ticker: ticker.to_string(),
        bar_family: family.to_string(),
        bar_start,
        bar_end,
        open: first.open,
        close: last.close,
        high: rows.iter().map(|row| row.high).fold(0.0_f32, f32::max),
        low: rows
            .iter()
            .map(|row| row.low)
            .filter(|value| *value > 0.0)
            .fold(0.0_f32, positive_min_f32),
        size_sum: rows.iter().map(|row| row.size_sum).sum(),
        size_open: first.size_open,
        size_close: last.size_close,
        size_high: rows.iter().map(|row| row.size_high).fold(0.0_f64, f64::max),
        size_low: rows
            .iter()
            .map(|row| row.size_low)
            .filter(|value| *value > 0.0)
            .fold(0.0_f64, positive_min_f64),
        event_count: rows.iter().map(|row| row.event_count).sum(),
        first_event_timestamp_us: rows.iter().map(|row| row.first_event_timestamp_us).min()?,
        last_event_timestamp_us: rows.iter().map(|row| row.last_event_timestamp_us).max()?,
        as_of,
        revision: rows.iter().map(|row| row.revision).sum(),
        state: if rows.iter().any(|row| row.state == ProductState::Corrected) {
            ProductState::Corrected
        } else if bar_end <= as_of && rows.iter().all(|row| row.state == ProductState::Closed) {
            ProductState::Closed
        } else {
            ProductState::Partial
        },
    })
}

fn event_sequence(event: &MarketEvent) -> u64 {
    match event {
        MarketEvent::Trade(value) => value.sequence,
        MarketEvent::Quote(value) => value.sequence,
    }
}

fn family_order(family: &str) -> u8 {
    match family {
        "trade" => 0,
        "quote_bid" => 1,
        "quote_ask" => 2,
        _ => 3,
    }
}

fn intersects(values: &[u16], expected: &HashSet<u16>) -> bool {
    values.iter().any(|value| expected.contains(value))
}

fn touch(order: &mut VecDeque<PartitionKey>, key: &PartitionKey) {
    if let Some(index) = order.iter().position(|candidate| candidate == key) {
        order.remove(index);
    }
    order.push_back(key.clone());
}

fn retain_tail<T>(rows: &mut Vec<T>, limit: usize) {
    if rows.len() > limit {
        rows.drain(0..rows.len() - limit);
    }
}

fn valid_trade_price_bar(row: &FamilyBarRow) -> bool {
    row.bar_family == "trade"
        && [row.open, row.high, row.low, row.close]
            .into_iter()
            .all(|value| value.is_finite() && value > 0.0)
        && row.high >= row.open.max(row.close)
        && row.low <= row.open.min(row.close)
        && row.high >= row.low
}

fn positive_min_f32(left: f32, right: f32) -> f32 {
    if left <= 0.0 {
        right
    } else if right <= 0.0 {
        left
    } else {
        left.min(right)
    }
}

fn positive_min_f64(left: f64, right: f64) -> f64 {
    if left <= 0.0 {
        right
    } else if right <= 0.0 {
        left
    } else {
        left.min(right)
    }
}

fn stable_hash(value: &str) -> u64 {
    value.bytes().fold(1_469_598_103_934_665_603, |hash, byte| {
        (hash ^ u64::from(byte)).wrapping_mul(1_099_511_628_211)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::{QuoteEvent, TradeEvent};
    use chrono::TimeZone;
    use serde_json::json;

    fn rules() -> TradeAggregationRules {
        TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap()
    }

    fn trade(ts: DateTime<Utc>, sequence: u64, price: f64, size: f64) -> MarketEvent {
        trade_with_conditions(ts, sequence, price, size, vec![])
    }

    fn trade_with_conditions(
        ts: DateTime<Utc>,
        sequence: u64,
        price: f64,
        size: f64,
        conditions: Vec<u16>,
    ) -> MarketEvent {
        MarketEvent::Trade(TradeEvent {
            conditions,
            exchange: 4,
            ingest_ts: ts,
            participant_ts: None,
            price,
            raw: json!({}),
            sequence,
            size,
            tape: 1,
            ticker: "AAPL".to_string(),
            trade_id: sequence.to_string(),
            trf_id: 0,
            trf_ts: None,
            ts,
        })
    }

    #[test]
    fn fixed_buckets_are_independent_and_late_events_correct_closed_rows() {
        let limits = ProductCacheLimits {
            max_bytes: 16 * 1024 * 1024,
            max_partitions: 8,
            max_rows: 100_000,
        };
        let mut engine = MarketProductEngine::new(
            vec![60_000_000],
            limits,
            rules(),
            ConditionClassifier::training_aligned(),
        );
        let first = Utc.with_ymd_and_hms(2026, 7, 10, 8, 1, 30).unwrap();
        let later = Utc.with_ymd_and_hms(2026, 7, 10, 8, 1, 50).unwrap();
        let as_of = Utc.with_ymd_and_hms(2026, 7, 10, 8, 2, 30).unwrap();
        engine.apply_event(&trade(later, 2, 102.0, 20.0), later);
        let _ = engine.family_snapshot("AAPL", 60_000_000, 10, as_of);
        engine.apply_event(&trade(first, 1, 100.0, 10.0), as_of);
        let snapshot = engine.family_snapshot("AAPL", 60_000_000, 10, later);
        assert_eq!(snapshot.rows.len(), 1);
        assert_eq!(snapshot.rows[0].open, 100.0);
        assert_eq!(snapshot.rows[0].state, ProductState::Corrected);
    }

    #[test]
    fn family_snapshot_before_pages_by_fixed_bar_start() {
        let limits = ProductCacheLimits {
            max_bytes: 16 * 1024 * 1024,
            max_partitions: 8,
            max_rows: 100_000,
        };
        let mut engine = MarketProductEngine::new(
            vec![1_000_000],
            limits,
            rules(),
            ConditionClassifier::training_aligned(),
        );
        let start = Utc.with_ymd_and_hms(2026, 7, 10, 13, 45, 0).unwrap();
        for (offset, price) in [(0, 100.0), (1, 101.0), (2, 102.0), (3, 103.0)] {
            let ts = start + Duration::seconds(offset);
            engine.apply_event(&trade(ts, offset as u64 + 1, price, 1.0), ts);
        }

        let before = start + Duration::seconds(3);
        let as_of = start + Duration::seconds(4);
        let snapshot = engine.family_snapshot_before("AAPL", 1_000_000, 2, as_of, Some(before));

        assert_eq!(snapshot.rows.len(), 2);
        assert_eq!(snapshot.rows[0].bar_start, start + Duration::seconds(1));
        assert_eq!(snapshot.rows[1].bar_start, start + Duration::seconds(2));
        assert!(snapshot.rows.iter().all(|row| row.bar_start < before));
    }

    #[test]
    fn trade_price_snapshot_omits_size_only_trade_buckets_before_limiting() {
        let limits = ProductCacheLimits {
            max_bytes: 16 * 1024 * 1024,
            max_partitions: 8,
            max_rows: 100_000,
        };
        let trade_rules = TradeAggregationRules::new([
            (0, TradeUpdateRule::regular()),
            (
                7,
                TradeUpdateRule {
                    update_high_low: false,
                    update_last: false,
                    update_volume: true,
                },
            ),
        ])
        .unwrap();
        let mut engine = MarketProductEngine::new(
            vec![100_000],
            limits,
            trade_rules,
            ConditionClassifier::training_aligned(),
        );
        let start = Utc.with_ymd_and_hms(2026, 7, 10, 13, 45, 0).unwrap();
        engine.apply_event(&trade(start, 1, 315.0, 100.0), start);
        engine.apply_event(
            &trade_with_conditions(
                start + Duration::milliseconds(100),
                2,
                231.82,
                50.0,
                vec![7],
            ),
            start + Duration::milliseconds(100),
        );
        engine.apply_event(
            &trade(start + Duration::milliseconds(200), 3, 315.1, 75.0),
            start + Duration::milliseconds(200),
        );

        let snapshot = engine.trade_price_snapshot_for_before(
            "AAPL",
            100_000,
            2,
            start + Duration::seconds(1),
            None,
        );

        assert_eq!(snapshot.rows.len(), 2);
        assert_eq!(snapshot.rows[0].open, 315.0);
        assert_eq!(snapshot.rows[1].open, 315.1);
        assert!(snapshot.rows.iter().all(valid_trade_price_bar));
    }

    #[test]
    fn condition_rows_match_training_labels() {
        let ts = Utc.with_ymd_and_hms(2026, 7, 10, 13, 45, 10).unwrap();
        let quote = MarketEvent::Quote(QuoteEvent {
            ask_exchange: 1,
            ask_price: 100.1,
            ask_size: 20,
            bid_exchange: 1,
            bid_price: 100.0,
            bid_size: 10,
            conditions: vec![43, 25],
            indicators: vec![17],
            ingest_ts: ts,
            raw: json!({}),
            sequence: 1,
            tape: 1,
            ticker: "AAPL".to_string(),
            ts,
        });
        let limits = ProductCacheLimits {
            max_bytes: 16 * 1024 * 1024,
            max_partitions: 8,
            max_rows: 100_000,
        };
        let mut engine = MarketProductEngine::new(
            vec![1_000_000],
            limits,
            rules(),
            ConditionClassifier::training_aligned(),
        );
        engine.apply_event(&quote, ts);
        let mut snapshot = engine.condition_snapshot("AAPL", 1_000_000, 10, ts);
        let row = snapshot.rows.remove(0);
        assert_eq!(row.condition_halt_pause_flag, 1);
        assert_eq!(row.condition_news_risk_flag, 1);
        assert_eq!(row.condition_luld_limit_state_flag, 1);
    }

    #[test]
    fn cache_limits_evict_whole_ticker_day_partitions() {
        let limits = ProductCacheLimits {
            max_bytes: 1_048_576,
            max_partitions: 1,
            max_rows: 10_000,
        };
        let mut engine = MarketProductEngine::new(
            vec![60_000_000],
            limits,
            rules(),
            ConditionClassifier::training_aligned(),
        );
        let first = Utc.with_ymd_and_hms(2026, 7, 10, 14, 0, 0).unwrap();
        let second = Utc.with_ymd_and_hms(2026, 7, 13, 14, 0, 0).unwrap();
        engine.apply_event(&trade(first, 1, 100.0, 1.0), first);
        engine.apply_event(&trade(second, 2, 101.0, 1.0), second);
        let metrics = engine.metrics();
        assert_eq!(metrics.partitions, 1);
        assert_eq!(metrics.evictions, 1);
    }

    #[test]
    fn one_oversized_partition_cannot_bypass_cache_limits() {
        let limits = ProductCacheLimits {
            max_bytes: 1_048_576,
            max_partitions: 1,
            max_rows: 1_000,
        };
        let mut engine = MarketProductEngine::new(
            vec![100_000],
            limits,
            rules(),
            ConditionClassifier::training_aligned(),
        );
        let start = Utc.with_ymd_and_hms(2026, 7, 10, 8, 0, 0).unwrap();
        for sequence in 0..=1_000_u64 {
            let ts = start + Duration::milliseconds(sequence as i64 * 100);
            engine.apply_event(&trade(ts, sequence + 1, 100.0, 1.0), ts);
        }
        let metrics = engine.metrics();
        assert!(metrics.family_rows + metrics.condition_rows <= metrics.max_rows);
        assert!(metrics.estimated_bytes <= metrics.max_bytes);
        assert!(metrics.evictions >= 1);
    }
}
