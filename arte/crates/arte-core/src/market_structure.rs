//! End-stamped one-second market/V7 bridge, version 1. Not a trading-ready token.
pub mod scheduler;
use crate::{
    market::{Series, Update},
    v7_extraction::Candle,
    v7_seed::{HistoricalSeed, SplitAdjustment},
    v7_stream::{Level, Stream, StreamPolicy},
    Error, Result,
};
const SECOND: u64 = 1_000_000_000;
const RECOVERY_VERSION: &str = "market-structure-recovery-v5";
const MAX_RECOVERY_BYTES: usize = 64 * 1024 * 1024;
use crate::events::{EventKey, Observation, Payload};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
#[derive(Debug, PartialEq)]
pub enum ObservationUpdate {
    Duplicate,
    Applied(Update),
}
#[derive(Serialize)]
pub struct Timeframe {
    pub interval_ns: u64,
    pub macd_periods: (u32, u32, u32),
    pub maximum_bars: usize,
}
#[derive(Serialize)]
pub struct Config {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub start_second: u64,
    pub end_second: u64,
    pub macd_periods: (u32, u32, u32),
    pub maximum_bars: usize,
    pub maximum_market_events: usize,
    pub additional_timeframes: Vec<Timeframe>,
    pub structure: StreamPolicy,
}
pub struct Runtime {
    provider: u16,
    market: Series,
    additional: BTreeMap<u64, Series>,
    structure: Stream,
    start_ns: u64,
    end_ns: u64,
    observed_at_ns: u64,
    failed: Option<String>,
    configuration_hash: String,
    applied: BTreeMap<EventKey, String>,
    maximum_market_events: usize,
    prior_levels: Vec<crate::strategy_targets::TargetLevel>,
    prior_levels_at_ns: u64,
    maximum_levels: usize,
}
/// Bytes must be persisted with their hash in an independently verified manifest.
/// This snapshot is streaming recovery, never a historical next-session seed.
pub struct Checkpoint {
    pub bytes: Vec<u8>,
    pub hash: String,
}
#[derive(Serialize, Deserialize)]
struct Recovery {
    provider: u16,
    version: String,
    configuration_hash: String,
    market: Series,
    additional: BTreeMap<u64, Series>,
    structure: crate::v7_stream::Recovery,
    start_ns: u64,
    end_ns: u64,
    observed_at_ns: u64,
    applied: Vec<(EventKey, String)>,
    maximum_market_events: usize,
    prior_levels: Vec<crate::strategy_targets::TargetLevel>,
    prior_levels_at_ns: u64,
    maximum_levels: usize,
}
impl Runtime {
    pub fn source_scope(&self) -> crate::event_order::Scope {
        crate::event_order::Scope {
            provider: self.provider,
            instrument: self.structure.instrument,
            session: self.structure.session,
        }
    }
    pub fn new(seed: &HistoricalSeed, config: Config, split: &SplitAdjustment) -> Result<Self> {
        if config.provider == 0
            || config.maximum_market_events == 0
            || config.maximum_market_events > 10_000_000
        {
            return Err(Error::Capacity("market event identity budget".into()));
        }
        let configuration_hash =
            crate::content_hash(&(RECOVERY_VERSION, &config, split.factor, &split.evidence))?;
        let start_ns = config
            .start_second
            .checked_mul(SECOND)
            .ok_or_else(|| Error::Invalid("session clock overflow".into()))?;
        let end_ns = config
            .end_second
            .checked_mul(SECOND)
            .ok_or_else(|| Error::Invalid("session clock overflow".into()))?;
        let maximum_levels = config.structure.maximum_levels;
        if config.additional_timeframes.len() > 16 {
            return Err(Error::Capacity("additional timeframe budget".into()));
        }
        let mut additional = BTreeMap::new();
        let mut total_bars = config.maximum_bars;
        for timeframe in &config.additional_timeframes {
            let interval = timeframe.interval_ns;
            if interval <= SECOND
                || interval > 86_400 * SECOND
                || !interval.is_multiple_of(SECOND)
                || !start_ns.is_multiple_of(interval)
                || !end_ns.is_multiple_of(interval)
                || additional.contains_key(&interval)
            {
                return Err(Error::Invalid(
                    "duplicate or unaligned additional timeframe".into(),
                ));
            }
            total_bars = total_bars
                .checked_add(timeframe.maximum_bars)
                .ok_or_else(|| Error::Capacity("combined timeframe bar budget".into()))?;
            if total_bars > 1_000_000 {
                return Err(Error::Capacity("combined timeframe bar budget".into()));
            }
            let (fast, slow, signal) = timeframe.macd_periods;
            additional.insert(
                interval,
                Series::new(interval, fast, slow, signal, timeframe.maximum_bars)?,
            );
        }
        let structure = Stream::new(
            seed,
            config.instrument,
            config.session,
            config.start_second,
            config.end_second,
            config.structure,
            split,
        )?;
        let (fast, slow, signal) = config.macd_periods;
        let prior_levels =
            crate::structure_projection::current(&structure, start_ns, maximum_levels)?;
        Ok(Self {
            provider: config.provider,
            market: Series::new(SECOND, fast, slow, signal, config.maximum_bars)?,
            additional,
            structure,
            start_ns,
            end_ns,
            observed_at_ns: start_ns,
            failed: None,
            configuration_hash,
            applied: BTreeMap::new(),
            maximum_market_events: config.maximum_market_events,
            prior_levels,
            prior_levels_at_ns: start_ns,
            maximum_levels,
        })
    }
    fn available(&self) -> Result<()> {
        if self.failed.is_some() {
            return Err(Error::Unready(
                "market/structure bridge requires recovery".into(),
            ));
        }
        Ok(())
    }
    fn clock(&self, event_ns: u64, observed_at_ns: u64) -> Result<()> {
        self.available()?;
        if event_ns < self.start_ns
            || event_ns > self.end_ns
            || observed_at_ns < event_ns
            || observed_at_ns < self.observed_at_ns
        {
            return Err(Error::Invalid(
                "market/structure session or observation clock".into(),
            ));
        }
        Ok(())
    }
    fn update_structure(&mut self, previous: usize, observed_at_ns: u64) -> Result<()> {
        if self.market.completed().len() > previous {
            let prior_at = self
                .structure
                .as_of
                .checked_mul(SECOND)
                .ok_or_else(|| Error::Invalid("prior V7 clock overflow".into()))?;
            let prior_levels = crate::structure_projection::current(
                &self.structure,
                prior_at,
                self.maximum_levels,
            )?;
            let bar = &self.market.completed().last().unwrap().bar;
            self.structure.update(
                Candle {
                    t: bar.end_ns / SECOND,
                    open: bar.open,
                    high: bar.high,
                    low: bar.low,
                    close: bar.close,
                    volume: bar.volume,
                },
                observed_at_ns / SECOND,
            )?;
            self.prior_levels = prior_levels;
            self.prior_levels_at_ns = prior_at;
        }
        self.observed_at_ns = observed_at_ns;
        Ok(())
    }
    pub(crate) fn trade(
        &mut self,
        sip_ns: u64,
        observed_at_ns: u64,
        price: f64,
        size: f64,
        eligible: bool,
    ) -> Result<Update> {
        self.clock(sip_ns, observed_at_ns)?;
        if sip_ns == self.end_ns {
            return Err(Error::Invalid("trade outside half-open session".into()));
        }
        let previous = self.market.completed().len();
        let result = (|| {
            let update = self.market.trade(sip_ns, price, size, eligible)?;
            if matches!(update, Update::Late) {
                return Err(Error::Unready("late event requires ordered repair".into()));
            }
            for series in self.additional.values_mut() {
                if matches!(series.trade(sip_ns, price, size, eligible)?, Update::Late) {
                    return Err(Error::Unready("late additional timeframe event".into()));
                }
            }
            self.update_structure(previous, observed_at_ns)?;
            Ok(update)
        })();
        if let Err(error) = &result {
            self.failed = Some(error.to_string());
        }
        result
    }
    /// Input must already be ordered and condition-qualified. Feed freshness remains
    /// a separate exposure gate. Receipts are not overwritten or synthesized here.
    pub fn observe_trade(
        &mut self,
        event: &Observation,
        eligible: bool,
    ) -> Result<ObservationUpdate> {
        self.observe_ordered_trade(event, eligible, event.available_at_ns)
    }
    /// Processing time can advance beyond original receipt during ordering. Never
    /// rewrite the observation's availability or receipt to make it monotonic.
    pub fn observe_ordered_trade(
        &mut self,
        event: &Observation,
        eligible: bool,
        processed_at_ns: u64,
    ) -> Result<ObservationUpdate> {
        self.available()?;
        event.validate()?;
        if processed_at_ns < event.available_at_ns {
            return Err(Error::Invalid(
                "market processing precedes observation availability".into(),
            ));
        }
        if event.key.provider != self.provider
            || event.key.instrument != self.structure.instrument
            || event.key.session != self.structure.session
        {
            return Err(Error::Conflict("market observation scope differs".into()));
        }
        let Payload::Trade { price, size, .. } = &event.payload else {
            return Err(Error::Invalid(
                "quote supplied to trade computation path".into(),
            ));
        };
        // Receipt/availability may differ on retransmission. Source economics and
        // calculation eligibility may not silently change under the same key.
        let hash = crate::content_hash(&(&event.key, &event.payload, event.sip, eligible))?;
        if let Some(previous) = self.applied.get(&event.key) {
            if previous == &hash {
                return Ok(ObservationUpdate::Duplicate);
            }
            self.failed = Some("conflicting source event requires repair".into());
            return Err(Error::Conflict("market event identity changed".into()));
        }
        if self.applied.len() == self.maximum_market_events {
            self.failed = Some("market event identity budget exhausted".into());
            return Err(Error::Capacity(
                "market event identities full; no eviction".into(),
            ));
        }
        if price.atoms > (1_i64 << 53) || size.atoms > (1_i64 << 53) {
            return Err(Error::Invalid(
                "market decimal exceeds exact integer conversion range".into(),
            ));
        }
        let update = self.trade(
            event.sip.ns,
            processed_at_ns,
            price.atoms as f64 / 10_f64.powi(price.scale.into()),
            size.atoms as f64 / 10_f64.powi(size.scale.into()),
            eligible,
        )?;
        self.applied.insert(event.key.clone(), hash);
        Ok(ObservationUpdate::Applied(update))
    }
    /// Watermark must already be established by the ordering/coverage authority.
    pub fn advance(&mut self, watermark_ns: u64, observed_at_ns: u64) -> Result<()> {
        self.clock(watermark_ns, observed_at_ns)?;
        let previous = self.market.completed().len();
        let result = (|| {
            self.market.advance(watermark_ns)?;
            for series in self.additional.values_mut() {
                series.advance(watermark_ns)?;
            }
            self.update_structure(previous, observed_at_ns)
        })();
        if let Err(error) = &result {
            self.failed = Some(error.to_string());
        }
        result
    }
    pub fn levels(&self) -> Result<impl Iterator<Item = &Level>> {
        self.available()?;
        self.structure.qualified_levels()
    }
    pub fn strategy_levels(
        &self,
        at_ns: u64,
        maximum: usize,
    ) -> Result<Vec<crate::strategy_targets::TargetLevel>> {
        self.available()?;
        crate::structure_projection::current(&self.structure, at_ns, maximum)
    }
    pub fn prior_strategy_levels(&self) -> Result<(u64, &[crate::strategy_targets::TargetLevel])> {
        self.available()?;
        Ok((self.prior_levels_at_ns, &self.prior_levels))
    }
    pub fn market(&self) -> Result<&Series> {
        self.available()?;
        Ok(&self.market)
    }
    pub fn timeframe(&self, interval_ns: u64) -> Result<&Series> {
        self.available()?;
        if interval_ns == SECOND {
            return Ok(&self.market);
        }
        self.additional
            .get(&interval_ns)
            .ok_or_else(|| Error::Unready("timeframe was not declared at startup".into()))
    }
    fn series(&self) -> impl Iterator<Item = (u64, &Series)> {
        std::iter::once((SECOND, &self.market)).chain(
            self.additional
                .iter()
                .map(|(interval, series)| (*interval, series)),
        )
    }
    fn next_completion_ns(&self) -> Option<u64> {
        self.series()
            .filter_map(|(_, series)| series.developing().map(|bar| bar.end_ns))
            .min()
    }
    pub fn failure(&self) -> Option<&str> {
        self.failed.as_deref()
    }
    pub fn configuration_hash(&self) -> &str {
        &self.configuration_hash
    }
    pub fn checkpoint(&self) -> Result<Checkpoint> {
        self.available()?;
        let recovery = Recovery {
            provider: self.provider,
            version: RECOVERY_VERSION.into(),
            configuration_hash: self.configuration_hash.clone(),
            market: self.market.clone(),
            additional: self.additional.clone(),
            structure: self.structure.checkpoint()?,
            start_ns: self.start_ns,
            end_ns: self.end_ns,
            observed_at_ns: self.observed_at_ns,
            applied: self
                .applied
                .iter()
                .map(|(key, hash)| (key.clone(), hash.clone()))
                .collect(),
            maximum_market_events: self.maximum_market_events,
            prior_levels: self.prior_levels.clone(),
            prior_levels_at_ns: self.prior_levels_at_ns,
            maximum_levels: self.maximum_levels,
        };
        let bytes =
            serde_json::to_vec(&recovery).map_err(|e| Error::Serialization(e.to_string()))?;
        if bytes.len() > MAX_RECOVERY_BYTES {
            return Err(Error::Capacity(
                "combined market recovery byte budget".into(),
            ));
        }
        let hash = format!("{:x}", Sha256::digest(&bytes));
        Ok(Checkpoint { bytes, hash })
    }
    /// expected_hash must come from trusted durable publication, not from the same
    /// untrusted payload. This detects corruption; it is not a signature or fencing.
    pub fn restore(
        bytes: &[u8],
        expected_hash: &str,
        seed_hash: &str,
        configuration_hash: &str,
    ) -> Result<Self> {
        if bytes.len() > MAX_RECOVERY_BYTES
            || format!("{:x}", Sha256::digest(bytes)) != expected_hash
        {
            return Err(Error::Invalid(
                "combined recovery size or content hash".into(),
            ));
        }
        let recovery: Recovery =
            serde_json::from_slice(bytes).map_err(|e| Error::Serialization(e.to_string()))?;
        if recovery.version != RECOVERY_VERSION || recovery.configuration_hash != configuration_hash
        {
            return Err(Error::Conflict(
                "combined recovery version or configuration".into(),
            ));
        }
        let structure = Stream::restore(recovery.structure, seed_hash)?;
        if recovery.additional.len() > 16
            || recovery.additional.iter().any(|(interval, series)| {
                *interval <= SECOND
                    || *interval > 86_400 * SECOND
                    || !interval.is_multiple_of(SECOND)
                    || !recovery.start_ns.is_multiple_of(*interval)
                    || !recovery.end_ns.is_multiple_of(*interval)
                    || series.interval_ns() != *interval
                    || series
                        .completed()
                        .iter()
                        .any(|bar| bar.bar.end_ns > recovery.observed_at_ns)
            })
        {
            return Err(Error::Conflict(
                "additional timeframe recovery scope or clock".into(),
            ));
        }
        let structure_at_ns = structure
            .as_of
            .checked_mul(SECOND)
            .ok_or_else(|| Error::Conflict("recovered V7 clock overflow".into()))?;
        if recovery.maximum_levels == 0
            || recovery.maximum_levels > 100_000
            || recovery.prior_levels.len() > recovery.maximum_levels
            || recovery.prior_levels_at_ns < recovery.start_ns
            || recovery.prior_levels_at_ns > structure_at_ns
        {
            return Err(Error::Conflict("prior level recovery boundary".into()));
        }
        let mut level_ids = std::collections::BTreeSet::new();
        for level in &recovery.prior_levels {
            crate::strategy_targets::valid_level(level)?;
            if level.geometry.confirmed_at_ns > recovery.prior_levels_at_ns
                || !level_ids.insert(&level.geometry.id)
            {
                return Err(Error::Conflict(
                    "prior level recovery contains future or duplicate levels".into(),
                ));
            }
        }
        let applied_count = recovery.applied.len();
        let applied: BTreeMap<_, _> = recovery.applied.into_iter().collect();
        if recovery.provider == 0
            || applied.len() != applied_count
            || applied.keys().any(|key| {
                key.provider != recovery.provider
                    || key.instrument != structure.instrument
                    || key.session != structure.session
            })
        {
            return Err(Error::Conflict(
                "restored market identity scope differs".into(),
            ));
        }
        if recovery.maximum_market_events == 0
            || recovery.maximum_market_events > 10_000_000
            || applied.len() > recovery.maximum_market_events
        {
            return Err(Error::Capacity("restored market identity budget".into()));
        }
        if structure.start.checked_mul(SECOND) != Some(recovery.start_ns)
            || structure.end.checked_mul(SECOND) != Some(recovery.end_ns)
            || recovery.observed_at_ns < recovery.start_ns
            || structure.as_of > recovery.observed_at_ns / SECOND
            || structure.bars_processed != recovery.market.completed().len()
            || recovery
                .market
                .completed()
                .last()
                .is_some_and(|bar| bar.bar.end_ns / SECOND != structure.as_of)
        {
            return Err(Error::Conflict(
                "combined recovery component clocks differ".into(),
            ));
        }
        Ok(Self {
            provider: recovery.provider,
            market: recovery.market,
            additional: recovery.additional,
            structure,
            start_ns: recovery.start_ns,
            end_ns: recovery.end_ns,
            observed_at_ns: recovery.observed_at_ns,
            failed: None,
            configuration_hash: recovery.configuration_hash,
            applied,
            maximum_market_events: recovery.maximum_market_events,
            prior_levels: recovery.prior_levels,
            prior_levels_at_ns: recovery.prior_levels_at_ns,
            maximum_levels: recovery.maximum_levels,
        })
    }
}

/// Ordered single-instrument calculation owner. Freshness and certified watermark
/// generation are external responsibilities; this type cannot authorize orders.
pub struct Ordered {
    runtime: Runtime,
    buffer: crate::event_order::Buffer,
    eligibility: BTreeMap<EventKey, bool>,
    newest_receipt_ns: u64,
    failed: bool,
}
#[derive(Serialize, Deserialize)]
struct OrderedRecovery {
    version: String,
    runtime_json: String,
    runtime_hash: String,
    pending: Vec<(Observation, bool)>,
    watermark_ns: u64,
    maximum_pending: usize,
    newest_receipt_ns: u64,
}
impl Ordered {
    pub fn scope(&self) -> crate::event_order::Scope {
        self.runtime.source_scope()
    }
    pub fn watermark_ns(&self) -> u64 {
        self.buffer.watermark_ns()
    }
    pub fn new(runtime: Runtime, maximum_pending: usize) -> Result<Self> {
        runtime.available()?;
        if !runtime.applied.is_empty()
            || runtime.structure.bars_processed != 0
            || runtime.market.developing().is_some()
        {
            return Err(Error::Invalid(
                "ordered owner requires an unwarmed runtime; restore must include its queue".into(),
            ));
        }
        let buffer = crate::event_order::Buffer::new(
            crate::event_order::Scope {
                provider: runtime.provider,
                instrument: runtime.structure.instrument,
                session: runtime.structure.session,
            },
            maximum_pending,
            runtime.start_ns,
        )?;
        let newest_receipt_ns = runtime.start_ns;
        Ok(Self {
            runtime,
            buffer,
            eligibility: BTreeMap::new(),
            newest_receipt_ns,
            failed: false,
        })
    }
    fn available(&self) -> Result<()> {
        if self.failed {
            return Err(Error::Unready(
                "ordered market owner requires repair".into(),
            ));
        }
        self.runtime.available()
    }
    /// Caller retains original input for persistence and audit, including rejects.
    pub fn enqueue(&mut self, event: &Observation, eligible: bool) -> Result<bool> {
        self.available()?;
        let result = (|| {
            event.validate()?;
            if event.key.provider != self.runtime.provider
                || event.key.instrument != self.runtime.structure.instrument
                || event.key.session != self.runtime.structure.session
                || !matches!(event.payload, Payload::Trade { .. })
            {
                return Err(Error::Conflict("ordered market trade scope differs".into()));
            }
            let hash = crate::content_hash(&(&event.key, &event.payload, event.sip, eligible))?;
            if let Some(previous) = self.runtime.applied.get(&event.key) {
                if previous == &hash {
                    return Ok(false);
                }
                return Err(Error::Conflict("released source identity changed".into()));
            }
            if self
                .eligibility
                .get(&event.key)
                .is_some_and(|previous| *previous != eligible)
            {
                return Err(Error::Conflict("pending source eligibility changed".into()));
            }
            let added = self.buffer.push(event)?;
            if added {
                self.eligibility.insert(event.key.clone(), eligible);
                self.newest_receipt_ns = self.newest_receipt_ns.max(event.available_at_ns);
            }
            Ok(added)
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    pub fn advance(&mut self, watermark_ns: u64, processed_at_ns: u64) -> Result<usize> {
        self.available()?;
        self.runtime.clock(watermark_ns, processed_at_ns)?;
        if processed_at_ns < self.newest_receipt_ns {
            return Err(Error::Invalid(
                "ordered processing precedes received input".into(),
            ));
        }
        let result = (|| {
            let runtime = &mut self.runtime;
            let eligibility = &mut self.eligibility;
            let count = self.buffer.release(watermark_ns, |event| {
                let eligible = *eligibility
                    .get(&event.key)
                    .ok_or_else(|| Error::Unready("ordered event eligibility missing".into()))?;
                runtime.observe_ordered_trade(event, eligible, processed_at_ns)?;
                eligibility.remove(&event.key);
                Ok(())
            })?;
            self.runtime.advance(watermark_ns, processed_at_ns)?;
            Ok(count)
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    pub fn market(&self) -> Result<&Series> {
        self.available()?;
        self.runtime.market()
    }
    pub fn levels(&self) -> Result<impl Iterator<Item = &Level>> {
        self.available()?;
        self.runtime.levels()
    }
    pub fn strategy_levels(
        &self,
        at_ns: u64,
        maximum: usize,
    ) -> Result<Vec<crate::strategy_targets::TargetLevel>> {
        self.available()?;
        self.runtime.strategy_levels(at_ns, maximum)
    }
    pub fn prior_strategy_levels(&self) -> Result<(u64, &[crate::strategy_targets::TargetLevel])> {
        self.available()?;
        self.runtime.prior_strategy_levels()
    }
    pub fn pending(&self) -> usize {
        self.buffer.pending()
    }
    pub fn checkpoint(&self) -> Result<Checkpoint> {
        self.available()?;
        let runtime = self.runtime.checkpoint()?;
        let pending =
            self.buffer
                .pending_events()
                .map(|event| {
                    let eligible = self.eligibility.get(&event.key).ok_or_else(|| {
                        Error::Unready("pending recovery eligibility missing".into())
                    })?;
                    Ok((event.clone(), *eligible))
                })
                .collect::<Result<Vec<_>>>()?;
        let recovery = OrderedRecovery {
            version: "ordered-market-recovery-v1".into(),
            runtime_json: String::from_utf8(runtime.bytes)
                .map_err(|_| Error::Invalid("runtime recovery encoding".into()))?,
            runtime_hash: runtime.hash,
            pending,
            watermark_ns: self.buffer.watermark_ns(),
            maximum_pending: self.buffer.maximum(),
            newest_receipt_ns: self.newest_receipt_ns,
        };
        let bytes =
            serde_json::to_vec(&recovery).map_err(|e| Error::Serialization(e.to_string()))?;
        if bytes.len() > MAX_RECOVERY_BYTES {
            return Err(Error::Capacity(
                "ordered market recovery byte budget".into(),
            ));
        }
        let hash = format!("{:x}", Sha256::digest(&bytes));
        Ok(Checkpoint { bytes, hash })
    }
    /// Expected identities must come from the approved durable recovery manifest.
    /// Restoring calculations never restores permission to trade or feed freshness.
    pub fn restore(
        bytes: &[u8],
        expected_hash: &str,
        seed_hash: &str,
        configuration_hash: &str,
        maximum_pending: usize,
    ) -> Result<Self> {
        if bytes.len() > MAX_RECOVERY_BYTES
            || format!("{:x}", Sha256::digest(bytes)) != expected_hash
        {
            return Err(Error::Invalid(
                "ordered recovery content hash or size".into(),
            ));
        }
        let recovery: OrderedRecovery =
            serde_json::from_slice(bytes).map_err(|e| Error::Serialization(e.to_string()))?;
        if recovery.version != "ordered-market-recovery-v1"
            || recovery.maximum_pending != maximum_pending
            || recovery.pending.len() > maximum_pending
        {
            return Err(Error::Conflict(
                "ordered recovery version or queue budget".into(),
            ));
        }
        let runtime = Runtime::restore(
            recovery.runtime_json.as_bytes(),
            &recovery.runtime_hash,
            seed_hash,
            configuration_hash,
        )?;
        if recovery.watermark_ns < runtime.start_ns
            || recovery.watermark_ns > runtime.end_ns
            || recovery.watermark_ns > runtime.observed_at_ns
            || recovery.newest_receipt_ns < runtime.start_ns
        {
            return Err(Error::Conflict("ordered recovery clock boundary".into()));
        }
        let mut buffer = crate::event_order::Buffer::new(
            crate::event_order::Scope {
                provider: runtime.provider,
                instrument: runtime.structure.instrument,
                session: runtime.structure.session,
            },
            maximum_pending,
            recovery.watermark_ns,
        )?;
        let mut eligibility = BTreeMap::new();
        for (event, eligible) in recovery.pending {
            if runtime.applied.contains_key(&event.key)
                || event.available_at_ns > recovery.newest_receipt_ns
                || !matches!(event.payload, Payload::Trade { .. })
                || !buffer.push(&event)?
            {
                return Err(Error::Conflict(
                    "ordered recovery duplicate or invalid pending input".into(),
                ));
            }
            eligibility.insert(event.key, eligible);
        }
        Ok(Self {
            runtime,
            buffer,
            eligibility,
            newest_receipt_ns: recovery.newest_receipt_ns,
            failed: false,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::v7_seed::{build, input_hash, SeedPolicy, SourceCertificate};
    pub(super) fn runtime(maximum_bars: usize) -> Runtime {
        runtime_with_timeframes(maximum_bars, vec![])
    }
    pub(super) fn runtime_with_timeframes(
        maximum_bars: usize,
        additional_timeframes: Vec<Timeframe>,
    ) -> Runtime {
        try_runtime(maximum_bars, additional_timeframes).unwrap()
    }
    fn try_runtime(maximum_bars: usize, additional_timeframes: Vec<Timeframe>) -> Result<Runtime> {
        let bars: Vec<_> = (100..118)
            .map(|t| Candle {
                t,
                open: 10.,
                high: 11.,
                low: 9.,
                close: 10.,
                volume: 1.,
            })
            .collect();
        let source = SourceCertificate {
            instrument: 1,
            ticker: "TEST".into(),
            session: 20260914,
            start_second: 100,
            end_second: 120,
            source_generation: "history".into(),
            input_hash: input_hash(&bars, &[]).unwrap(),
            certified_at_second: 121,
        };
        let seed = build(
            &bars,
            &[],
            source,
            None,
            &SeedPolicy::default(),
            &SplitAdjustment::default(),
            121,
        )
        .unwrap();
        Runtime::new(
            &seed,
            Config {
                provider: 1,
                instrument: 1,
                session: 20260915,
                start_second: 200,
                end_second: 300,
                macd_periods: (2, 3, 2),
                maximum_bars,
                maximum_market_events: 100,
                additional_timeframes,
                structure: StreamPolicy {
                    input_generation: "live".into(),
                    ..StreamPolicy::default()
                },
            },
            &SplitAdjustment::default(),
        )
    }
    #[test]
    fn additional_timeframe_declarations_reject_duplicates_alignment_and_capacity() {
        let timeframe = |interval_ns, maximum_bars| Timeframe {
            interval_ns,
            maximum_bars,
            macd_periods: (12, 26, 9),
        };
        for interval in [SECOND, 5 * SECOND + 1, 7 * SECOND, 86_401 * SECOND] {
            assert!(try_runtime(10, vec![timeframe(interval, 10)]).is_err());
        }
        assert!(try_runtime(10, vec![timeframe(5 * SECOND, 0)]).is_err());
        assert!(try_runtime(10, vec![timeframe(5 * SECOND, 1_000_000)]).is_err());
        assert!(try_runtime(
            10,
            vec![timeframe(5 * SECOND, 10), timeframe(5 * SECOND, 10)]
        )
        .is_err());
        assert!(try_runtime(10, (0..17).map(|_| timeframe(5 * SECOND, 10)).collect()).is_err());
    }
    #[test]
    fn completed_seconds_advance_v7_and_failure_hides_all_projections() {
        let mut runtime = runtime(1);
        runtime
            .trade(200 * SECOND + 1, 200 * SECOND + 1, 10., 1., true)
            .unwrap();
        assert_eq!(runtime.structure.bars_processed, 0);
        runtime.advance(201 * SECOND, 201 * SECOND).unwrap();
        assert_eq!(runtime.structure.bars_processed, 1);
        assert_eq!(runtime.structure.as_of, 201);
        runtime
            .trade(201 * SECOND, 201 * SECOND, 11., 1., true)
            .unwrap();
        assert!(runtime.advance(202 * SECOND, 202 * SECOND).is_err());
        assert!(runtime.failure().is_some());
        assert!(runtime.levels().is_err());
        assert!(runtime.market().is_err());
        assert!(runtime.checkpoint().is_err());
    }
    #[test]
    fn combined_recovery_continues_exactly_and_binds_seed_and_configuration() {
        let mut original = runtime_with_timeframes(
            10,
            vec![Timeframe {
                interval_ns: 5 * SECOND,
                macd_periods: (12, 26, 9),
                maximum_bars: 10,
            }],
        );
        original
            .trade(200 * SECOND + 1, 200 * SECOND + 1, 10., 1., true)
            .unwrap();
        original.advance(201 * SECOND, 201 * SECOND).unwrap();
        assert_eq!(original.prior_strategy_levels().unwrap().0, 200 * SECOND);
        original
            .trade(201 * SECOND + 1, 201 * SECOND + 1, 11., 2., true)
            .unwrap();
        let checkpoint = original.checkpoint().unwrap();
        let seed = original.structure.seed_hash.clone();
        let config = original.configuration_hash().to_owned();
        let mut restored =
            Runtime::restore(&checkpoint.bytes, &checkpoint.hash, &seed, &config).unwrap();
        assert_eq!(
            restored
                .timeframe(5 * SECOND)
                .unwrap()
                .developing()
                .unwrap()
                .trades,
            2
        );
        assert!(restored.timeframe(10 * SECOND).is_err());
        assert_eq!(restored.prior_strategy_levels().unwrap().0, 200 * SECOND);
        assert!(Runtime::restore(&checkpoint.bytes, "wrong", &seed, &config).is_err());
        assert!(Runtime::restore(&checkpoint.bytes, &checkpoint.hash, "wrong", &config).is_err());
        assert!(Runtime::restore(&checkpoint.bytes, &checkpoint.hash, &seed, "wrong").is_err());
        for second in 202..207 {
            for state in [&mut original, &mut restored] {
                state
                    .trade(
                        second * SECOND,
                        second * SECOND,
                        10. + (second % 2) as f64,
                        1.,
                        true,
                    )
                    .unwrap();
            }
            assert_eq!(
                original.prior_strategy_levels().unwrap().0,
                (second - 1) * SECOND
            );
            assert_eq!(
                original.checkpoint().unwrap().hash,
                restored.checkpoint().unwrap().hash
            );
        }
        assert_eq!(restored.timeframe(5 * SECOND).unwrap().completed().len(), 1);
    }
    #[test]
    fn canonical_trade_retry_is_deduplicated_across_recovery_and_conflicts_block() {
        use crate::events::{Decimal, EventKind, SourceTime};
        let mut runtime = runtime(10);
        let mut event = Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Trade,
                sequence: 1,
            },
            payload: Payload::Trade {
                price: Decimal {
                    atoms: 1001,
                    scale: 2,
                },
                size: Decimal { atoms: 2, scale: 0 },
                exchange: 1,
                trade_id: "trade-1".into(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns: 200 * SECOND + 1,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: 200 * SECOND + 2,
            receipt: None,
        };
        assert!(matches!(
            runtime.observe_trade(&event, true).unwrap(),
            ObservationUpdate::Applied(_)
        ));
        let checkpoint = runtime.checkpoint().unwrap();
        let mut restored = Runtime::restore(
            &checkpoint.bytes,
            &checkpoint.hash,
            &runtime.structure.seed_hash,
            runtime.configuration_hash(),
        )
        .unwrap();
        event.available_at_ns += 100;
        assert_eq!(
            restored.observe_trade(&event, true).unwrap(),
            ObservationUpdate::Duplicate
        );
        assert_eq!(restored.market().unwrap().developing().unwrap().trades, 1);
        assert!(restored.observe_trade(&event, false).is_err());
        assert!(restored.market().is_err());

        let mut ordered = Ordered::new(self::runtime(10), 10).unwrap();
        let mut later = event.clone();
        later.key.sequence = 2;
        later.sip.ns += 1;
        // The later source event arrived first. Neither receipt is rewritten.
        later.available_at_ns = event.available_at_ns - 1;
        ordered.enqueue(&later, true).unwrap();
        ordered.enqueue(&event, true).unwrap();
        let pending = ordered.checkpoint().unwrap();
        let mut recovered = Ordered::restore(
            &pending.bytes,
            &pending.hash,
            &ordered.runtime.structure.seed_hash,
            ordered.runtime.configuration_hash(),
            10,
        )
        .unwrap();
        assert!(Ordered::restore(
            &pending.bytes,
            &pending.hash,
            &ordered.runtime.structure.seed_hash,
            ordered.runtime.configuration_hash(),
            9
        )
        .is_err());
        assert_eq!(recovered.pending(), 2);
        recovered.advance(201 * SECOND, 201 * SECOND).unwrap();
        assert_eq!(ordered.advance(201 * SECOND, 201 * SECOND).unwrap(), 2);
        assert_eq!(ordered.market().unwrap().completed()[0].bar.trades, 2);
        assert_eq!(ordered.pending(), 0);
        assert_eq!(
            ordered.checkpoint().unwrap().hash,
            recovered.checkpoint().unwrap().hash
        );
        assert!(!ordered.enqueue(&event, true).unwrap());
        assert!(ordered.enqueue(&event, false).is_err());
        assert!(ordered.levels().is_err());
    }
}
