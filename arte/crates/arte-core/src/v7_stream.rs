//! Causal completed-second V7 state. Recovery snapshots cannot publish daily seeds.
use crate::v7_band::{partition, Band, Observation};
use crate::v7_encounters::ActiveRole;
use crate::v7_evidence::{Outcome, Role};
use crate::v7_extraction::{Candle, Settings};
use crate::v7_seed::{HistoricalSeed, SeedObservation, SplitAdjustment};
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use std::cmp::{Ordering, Reverse};
use std::collections::{BTreeMap, BinaryHeap, VecDeque};
pub const VERSION: &str = "arte-causal-v7-1";
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
struct Number(f64);
impl Eq for Number {}
impl PartialOrd for Number {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Number {
    fn cmp(&self, other: &Self) -> Ordering {
        self.0.total_cmp(&other.0)
    }
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct Median {
    small: BinaryHeap<Number>,
    large: BinaryHeap<Reverse<Number>>,
}
impl Median {
    fn update(&mut self, value: f64, fallback: f64) -> f64 {
        if value > 0. {
            if self.small.peek().is_none_or(|x| value <= x.0) {
                self.small.push(Number(value));
            } else {
                self.large.push(Reverse(Number(value)));
            }
            if self.small.len() > self.large.len() + 1 {
                self.large.push(Reverse(self.small.pop().unwrap()));
            }
            if self.large.len() > self.small.len() {
                self.small.push(self.large.pop().unwrap().0);
            }
        }
        if self.small.is_empty() {
            fallback
        } else if self.small.len() > self.large.len() {
            self.small.peek().unwrap().0
        } else {
            self.small.peek().unwrap().0 / 2. + self.large.peek().unwrap().0 .0 / 2.
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamPolicy {
    pub input_generation: String,
    pub settings: Settings,
    pub coverage: f64,
    pub discovery_prominence: Option<f64>,
    pub maximum_levels: usize,
    pub maximum_observations: usize,
    pub maximum_bars: usize,
    pub maximum_events: usize,
}
impl Default for StreamPolicy {
    fn default() -> Self {
        Self {
            input_generation: String::new(),
            settings: Settings::default(),
            coverage: 0.8,
            discovery_prominence: None,
            maximum_levels: 4096,
            maximum_observations: 1_000_000,
            maximum_bars: 86400,
            maximum_events: 1_000_000,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Segment {
    pub at: u64,
    pub role: ActiveRole,
    pub band: Band,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Resolved {
    pub at: u64,
    pub resolved_at: u64,
    pub role: Role,
    pub outcome: Outcome,
    pub reason: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Pending {
    at: u64,
    role: Role,
    extreme: f64,
    prominence: f64,
    lower: f64,
    upper: f64,
    half: f64,
    beyond: bool,
    last_t: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Level {
    pub id: String,
    pub historical: bool,
    pub origin_session: u32,
    pub observations: Vec<SeedObservation>,
    pub band: Band,
    pub price: f64,
    pub association_radius: f64,
    pub role: ActiveRole,
    pub segments: Vec<Segment>,
    pub events: Vec<Resolved>,
    armed: bool,
    side: Option<Role>,
    last_contact: Option<u64>,
    pending: Vec<Pending>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Stream {
    version: String,
    pub seed_hash: String,
    pub instrument: u64,
    pub session: u32,
    pub start: u64,
    pub end: u64,
    pub as_of: u64,
    pub levels: Vec<Level>,
    policy: StreamPolicy,
    median: Median,
    previous: Option<Candle>,
    high: Option<(f64, u64)>,
    low: Option<(f64, u64)>,
    hod: Option<f64>,
    lod: Option<f64>,
    trend: i8,
    pub bars_processed: usize,
    pub proposals: usize,
    pub merged: usize,
    pub failed: bool,
    split_factor: f64,
    split_evidence: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Recovery {
    pub version: String,
    pub state: Stream,
    pub hash: String,
}
impl Stream {
    pub fn new(
        seed: &HistoricalSeed,
        instrument: u64,
        session: u32,
        start: u64,
        end: u64,
        policy: StreamPolicy,
        split: &SplitAdjustment,
    ) -> Result<Self> {
        seed.require_available(instrument, session, start)?;
        let s = &policy.settings;
        if end <= start
            || policy.input_generation.is_empty()
            || !split.factor.is_finite()
            || split.factor <= 0.
            || (split.factor != 1. && split.evidence.is_empty())
            || policy.maximum_levels == 0
            || policy.maximum_observations == 0
            || policy.maximum_bars == 0
            || policy.maximum_events == 0
            || !policy.coverage.is_finite()
            || policy.coverage <= 0.
            || policy.coverage >= 1.
            || [s.tick, s.noise_multiple, s.range_fraction]
                .iter()
                .any(|v| !v.is_finite() || *v <= 0.)
            || s.reaction_seconds == 0
            || s.maximum_gap_seconds == 0
            || s.maximum_candidates == 0
            || policy
                .discovery_prominence
                .is_some_and(|v| !v.is_finite() || v <= 0.)
        {
            return Err(Error::Invalid(
                "invalid streaming policy, session or split".into(),
            ));
        }
        let mut levels = Vec::new();
        for original in &seed.levels {
            if original.band.coverage != policy.coverage {
                return Err(Error::Invalid(
                    "stream coverage differs from seed fit".into(),
                ));
            }
            let mut observations = original.observations.clone();
            for o in &mut observations {
                o.value.price *= split.factor;
                o.value.resolution *= split.factor;
            }
            let mut band = original.band.clone();
            for value in [
                &mut band.fit.center,
                &mut band.fit.scale,
                &mut band.lower,
                &mut band.upper,
                &mut band.resolution,
            ]
            .into_iter()
            .flatten()
            {
                *value *= split.factor;
            }
            let historical = band.estimated();
            let segments = if historical {
                vec![Segment {
                    at: start,
                    role: original.role,
                    band: band.clone(),
                }]
            } else {
                vec![]
            };
            levels.push(Level {
                id: original.id.clone(),
                historical,
                origin_session: original.origin_session,
                observations,
                band,
                price: original.association_center * split.factor,
                association_radius: original.association_radius * split.factor,
                role: original.role,
                segments,
                events: vec![],
                armed: true,
                side: None,
                last_contact: None,
                pending: vec![],
            });
        }
        let result = Self {
            version: VERSION.into(),
            seed_hash: seed.hash.clone(),
            instrument,
            session,
            start,
            end,
            as_of: start,
            levels,
            policy,
            median: Median::default(),
            previous: None,
            high: None,
            low: None,
            hod: None,
            lod: None,
            trend: 0,
            bars_processed: 0,
            proposals: 0,
            merged: 0,
            failed: false,
            split_factor: split.factor,
            split_evidence: split.evidence.clone(),
        };
        result.check_capacity()?;
        Ok(result)
    }
    fn check_capacity(&self) -> Result<()> {
        if self.levels.len() > self.policy.maximum_levels
            || self.bars_processed > self.policy.maximum_bars
            || self
                .levels
                .iter()
                .map(|l| l.observations.len())
                .sum::<usize>()
                > self.policy.maximum_observations
            || self
                .levels
                .iter()
                .map(|l| l.events.len() + l.pending.len())
                .sum::<usize>()
                > self.policy.maximum_events
        {
            return Err(Error::Capacity(
                "streaming state capacity exceeded; no eviction".into(),
            ));
        }
        Ok(())
    }
    fn segment(level: &mut Level, at: u64) {
        if level.segments.last().is_some_and(|s| s.at == at) {
            level.segments.pop();
        }
        level.segments.push(Segment {
            at,
            role: level.role,
            band: level.band.clone(),
        });
    }
    fn resolve(level: &mut Level, event: Pending, outcome: Outcome, at: u64, reason: Option<&str>) {
        level.events.push(Resolved {
            at: event.at,
            resolved_at: at,
            role: event.role,
            outcome,
            reason: reason.map(str::to_owned),
        });
        if outcome == Outcome::Unresolved || level.last_contact.is_some_and(|t| event.at < t) {
            return;
        }
        level.last_contact = Some(event.at);
        let role = if outcome == Outcome::Rejection {
            ActiveRole::from(event.role)
        } else if level.role == ActiveRole::from(event.role) {
            ActiveRole::Transition
        } else {
            return;
        };
        if level.role != role {
            level.role = role;
            if level.band.estimated() {
                Self::segment(level, at);
            }
        }
    }
    fn propose(
        &mut self,
        price: f64,
        pivot: u64,
        role: Role,
        bar: &Candle,
        prominence: f64,
    ) -> Result<()> {
        if bar.t - pivot > self.policy.settings.reaction_seconds {
            return Ok(());
        }
        self.proposals += 1;
        let radius = prominence / 2.;
        let best = self
            .levels
            .iter()
            .enumerate()
            .filter(|(_, l)| (l.price - price).abs() <= radius.max(l.association_radius))
            .min_by(|(_, a), (_, b)| {
                (a.price - price)
                    .abs()
                    .total_cmp(&(b.price - price).abs())
                    .then((!a.historical).cmp(&(!b.historical)))
                    .then(a.id.cmp(&b.id))
            })
            .map(|(i, _)| i);
        let observation = SeedObservation {
            id: content_hash(&(
                VERSION,
                self.instrument,
                self.session,
                pivot,
                bar.t,
                role,
                price,
            ))?,
            session: self.session,
            source_generation: self.policy.input_generation.clone(),
            role,
            value: Observation {
                price,
                resolution: if price < 1. { 0.0001 } else { 0.01 },
                at: pivot,
                resolved_at: bar.t,
            },
        };
        if let Some(index) = best {
            self.merged += 1;
            if self.levels[index]
                .observations
                .iter()
                .any(|o| o.session == self.session && o.value.resolved_at >= pivot)
            {
                return Ok(());
            }
            let mut observations = self.levels[index].observations.clone();
            observations.push(observation);
            let mut parts = partition(
                &observations
                    .iter()
                    .map(|o| o.value.clone())
                    .collect::<Vec<_>>(),
                self.policy.coverage,
            )?;
            if self.levels[index].band.estimated() && parts.iter().any(|c| !c.band.estimated()) {
                return Err(Error::Unready(
                    "qualified streaming fit failed; no stale fallback".into(),
                ));
            }
            let original = self.levels[index].clone();
            parts.sort_by(|a, b| {
                (a.band.fit.center.unwrap_or(original.price) - original.price)
                    .abs()
                    .total_cmp(
                        &(b.band.fit.center.unwrap_or(original.price) - original.price).abs(),
                    )
            });
            let key = |v: &Observation| {
                (
                    v.at,
                    v.resolved_at,
                    v.price.to_bits(),
                    v.resolution.to_bits(),
                )
            };
            let mut map: BTreeMap<_, VecDeque<_>> = BTreeMap::new();
            for o in observations {
                map.entry(key(&o.value)).or_default().push_back(o);
            }
            for (part_index, part) in parts.into_iter().enumerate() {
                let mut target = original.clone();
                target.observations.clear();
                for v in &part.observations {
                    target.observations.push(
                        map.get_mut(&key(v))
                            .and_then(VecDeque::pop_front)
                            .ok_or_else(|| {
                                Error::Invalid("stream partition observation mismatch".into())
                            })?,
                    );
                }
                if part_index > 0 {
                    target.id =
                        content_hash(&(VERSION, &original.id, bar.t, part_index, &part.band))?;
                    target.segments.clear();
                    target.events.clear();
                    target.pending.clear();
                }
                target.band = part.band;
                if target.band.estimated() {
                    target.price = target.band.fit.center.unwrap();
                    target.role = ActiveRole::from(role);
                    Self::segment(&mut target, bar.t);
                }
                if part_index == 0 {
                    self.levels[index] = target;
                } else {
                    self.levels.push(target);
                }
            }
        } else {
            if self.levels.iter().filter(|l| !l.historical).count()
                >= self.policy.settings.maximum_candidates
            {
                return Err(Error::Capacity(
                    "stream discovery candidate limit reached".into(),
                ));
            }
            let band = crate::v7_band::estimate(
                std::slice::from_ref(&observation.value),
                self.policy.coverage,
            )?;
            self.levels.push(Level {
                id: content_hash(&(VERSION, self.instrument, self.session, price, bar.t))?,
                historical: false,
                origin_session: self.session,
                observations: vec![observation],
                band,
                price,
                association_radius: radius,
                role: ActiveRole::from(role),
                segments: vec![],
                events: vec![Resolved {
                    at: pivot,
                    resolved_at: bar.t,
                    role,
                    outcome: Outcome::Rejection,
                    reason: None,
                }],
                armed: false,
                side: None,
                last_contact: Some(pivot),
                pending: vec![],
            });
        }
        self.check_capacity()
    }
    pub fn update(&mut self, bar: Candle, observed_at: u64) -> Result<()> {
        if self.failed {
            return Err(Error::Unready(
                "stream failed; restore from verified recovery or replay".into(),
            ));
        }
        if bar.t <= self.as_of
            || bar.t > self.end
            || bar.t > observed_at
            || [bar.open, bar.high, bar.low, bar.close, bar.volume]
                .iter()
                .any(|v| !v.is_finite())
            || bar.low <= 0.
            || bar.volume < 0.
            || bar.high < bar.open.max(bar.close)
            || bar.low > bar.open.min(bar.close)
        {
            return Err(Error::Invalid(
                "invalid, future or unordered completed bar".into(),
            ));
        }
        let result = self.advance(bar);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn advance(&mut self, bar: Candle) -> Result<()> {
        let s = &self.policy.settings;
        let gap = self
            .previous
            .is_some_and(|b| bar.t - b.t > s.maximum_gap_seconds);
        let noise = self.median.update(bar.high - bar.low, s.tick);
        self.hod = Some(self.hod.unwrap_or(bar.high).max(bar.high));
        self.lod = Some(self.lod.unwrap_or(bar.low).min(bar.low));
        let prominence = self.policy.discovery_prominence.unwrap_or(
            (3. * s.tick)
                .max(s.noise_multiple * noise)
                .max((self.hod.unwrap() - self.lod.unwrap()) * s.range_fraction),
        );
        if !prominence.is_finite() {
            return Err(Error::Invalid("stream prominence overflow".into()));
        }
        for level in &mut self.levels {
            let mut pending = std::mem::take(&mut level.pending);
            for mut event in pending.drain(..) {
                if gap || bar.t - event.at > s.reaction_seconds {
                    Self::resolve(
                        level,
                        event,
                        Outcome::Unresolved,
                        self.previous.unwrap().t,
                        Some(if gap { "data_gap" } else { "reaction_timeout" }),
                    );
                    continue;
                }
                let resistance = event.role == Role::Resistance;
                event.extreme = if resistance {
                    event.extreme.max(bar.high)
                } else {
                    event.extreme.min(bar.low)
                };
                let reject = if resistance {
                    bar.close < event.lower - event.prominence
                } else {
                    bar.close > event.upper + event.prominence
                };
                let beyond = if resistance {
                    bar.close > event.upper + event.half
                } else {
                    bar.close < event.lower - event.half
                };
                let accept = beyond && event.beyond && bar.t - event.last_t == 1;
                if reject {
                    let inside = event.extreme >= event.lower - 1e-10
                        && event.extreme <= event.upper + 1e-10;
                    Self::resolve(
                        level,
                        event,
                        if inside {
                            Outcome::Rejection
                        } else {
                            Outcome::Unresolved
                        },
                        bar.t,
                        if inside {
                            None
                        } else {
                            Some("turning_extreme_outside_band")
                        },
                    );
                } else if accept {
                    Self::resolve(level, event, Outcome::Acceptance, bar.t, None);
                } else {
                    event.beyond = beyond;
                    event.last_t = bar.t;
                    level.pending.push(event);
                }
            }
            if gap {
                level.armed = true;
                level.side = None;
            } else if let Some(previous) = self.previous {
                let lower = level.band.lower.unwrap_or(level.price);
                let upper = level.band.upper.unwrap_or(level.price);
                if previous.close < lower - prominence {
                    level.armed = true;
                    level.side = Some(Role::Resistance);
                }
                if previous.close > upper + prominence {
                    level.armed = true;
                    level.side = Some(Role::Support);
                }
                if level.band.estimated() && level.armed && bar.high >= lower && bar.low <= upper {
                    let role = level.side.or(if previous.close < lower {
                        Some(Role::Resistance)
                    } else if previous.close > upper {
                        Some(Role::Support)
                    } else {
                        None
                    });
                    if let Some(role) = role {
                        level.armed = false;
                        level.pending.push(Pending {
                            at: bar.t,
                            role,
                            extreme: if role == Role::Resistance {
                                bar.high
                            } else {
                                bar.low
                            },
                            prominence,
                            lower,
                            upper,
                            half: (upper - lower) / 2.,
                            beyond: false,
                            last_t: bar.t,
                        });
                    }
                }
            }
        }
        if gap {
            self.high = None;
            self.low = None;
            self.trend = 0;
        }
        if self.high.is_none_or(|(p, _)| bar.high > p) {
            self.high = Some((bar.high, bar.t));
        }
        if self.low.is_none_or(|(p, _)| bar.low < p) {
            self.low = Some((bar.low, bar.t));
        }
        let high = self.high.unwrap();
        let low = self.low.unwrap();
        if self.trend >= 0 && bar.close <= high.0 - prominence {
            self.propose(high.0, high.1, Role::Resistance, &bar, prominence)?;
            self.trend = -1;
            self.high = Some((bar.high, bar.t));
            self.low = Some((bar.low, bar.t));
        } else if self.trend <= 0 && bar.close >= low.0 + prominence {
            self.propose(low.0, low.1, Role::Support, &bar, prominence)?;
            self.trend = 1;
            self.high = Some((bar.high, bar.t));
            self.low = Some((bar.low, bar.t));
        }
        self.previous = Some(bar);
        self.as_of = bar.t;
        self.bars_processed += 1;
        self.check_capacity()
    }
    pub fn checkpoint(&self) -> Result<Recovery> {
        if self.failed {
            return Err(Error::Unready(
                "failed stream is audit-only, not a recovery checkpoint".into(),
            ));
        }
        Ok(Recovery {
            version: VERSION.into(),
            state: self.clone(),
            hash: content_hash(&(VERSION, self))?,
        })
    }
    /// Strategy-facing projection: insufficient candidates and failed state are
    /// never supplied as actionable fitted levels. Freshness is a separate gate.
    pub fn qualified_levels(&self) -> Result<impl Iterator<Item = &Level>> {
        if self.failed {
            return Err(Error::Unready(
                "failed stream has no actionable projection".into(),
            ));
        }
        Ok(self.levels.iter().filter(|level| level.band.estimated()))
    }
    pub fn restore(checkpoint: Recovery, seed_hash: &str) -> Result<Self> {
        if checkpoint.version != VERSION
            || checkpoint.state.version != VERSION
            || checkpoint.state.seed_hash != seed_hash
            || checkpoint.state.failed
            || checkpoint.hash != content_hash(&(VERSION, &checkpoint.state))?
        {
            return Err(Error::Invalid(
                "stream recovery hash, version or seed mismatch".into(),
            ));
        }
        checkpoint.state.check_capacity()?;
        Ok(checkpoint.state)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::v7_seed::{build, input_hash, SeedPolicy, SourceCertificate};
    fn seed() -> HistoricalSeed {
        let bars: Vec<_> = (0..18)
            .map(|i| candle(100 + i, 10. + (i % 2) as f64))
            .collect();
        let source = SourceCertificate {
            instrument: 1,
            ticker: "TEST".into(),
            session: 20260914,
            start_second: 100,
            end_second: 120,
            source_generation: "historical".into(),
            input_hash: input_hash(&bars, &[]).unwrap(),
            certified_at_second: 121,
        };
        build(
            &bars,
            &[],
            source,
            None,
            &SeedPolicy::default(),
            &SplitAdjustment::default(),
            121,
        )
        .unwrap()
    }
    fn candle(t: u64, p: f64) -> Candle {
        Candle {
            t,
            open: p,
            close: p,
            high: p + 0.01,
            low: p - 0.01,
            volume: 100.,
        }
    }
    fn policy() -> StreamPolicy {
        StreamPolicy {
            input_generation: "recorded-live-test".into(),
            ..StreamPolicy::default()
        }
    }
    #[test]
    fn recovery_continues_exactly_without_changing_historical_seed() {
        let seed = seed();
        let seed_before = content_hash(&seed).unwrap();
        let mut a = Stream::new(
            &seed,
            1,
            20260915,
            200,
            500,
            policy(),
            &SplitAdjustment::default(),
        )
        .unwrap();
        for i in 1..12 {
            a.update(candle(200 + i, 10. + (i % 2) as f64), 200 + i)
                .unwrap();
        }
        let encoded = serde_json::to_string(&a.checkpoint().unwrap()).unwrap();
        let mut b = Stream::restore(serde_json::from_str(&encoded).unwrap(), &seed.hash).unwrap();
        for i in 12..30 {
            let bar = candle(200 + i, 10. + (i % 2) as f64);
            a.update(bar, bar.t).unwrap();
            b.update(bar, bar.t).unwrap();
            assert_eq!(content_hash(&a).unwrap(), content_hash(&b).unwrap());
        }
        assert_eq!(seed_before, content_hash(&seed).unwrap());
        assert!(a
            .levels
            .iter()
            .flat_map(|l| &l.segments)
            .all(|s| s.at <= a.as_of));
        assert!(a
            .levels
            .iter()
            .flat_map(|l| &l.observations)
            .filter(|o| o.session == 20260915)
            .all(|o| o.source_generation == "recorded-live-test"));
    }
    #[test]
    fn invalid_input_does_not_mutate_but_capacity_failure_blocks() {
        let seed = seed();
        let mut p = policy();
        p.maximum_bars = 1;
        let mut stream =
            Stream::new(&seed, 1, 20260915, 200, 500, p, &SplitAdjustment::default()).unwrap();
        let before = content_hash(&stream).unwrap();
        assert!(stream.update(candle(201, 10.), 200).is_err());
        assert_eq!(before, content_hash(&stream).unwrap());
        stream.update(candle(201, 10.), 201).unwrap();
        assert!(stream.update(candle(202, 10.), 202).is_err());
        assert!(stream.failed);
        assert!(stream.checkpoint().is_err());
        assert!(stream.qualified_levels().is_err());
        assert!(stream.update(candle(203, 10.), 203).is_err());
    }
    #[test]
    fn gap_resolves_pending_without_awarding_rejection() {
        let seed = seed();
        let mut stream = Stream::new(
            &seed,
            1,
            20260915,
            200,
            500,
            policy(),
            &SplitAdjustment::default(),
        )
        .unwrap();
        let index = stream
            .levels
            .iter()
            .position(|l| l.band.estimated())
            .unwrap();
        let price = stream.levels[index].price;
        stream.levels[index].pending.push(Pending {
            at: 201,
            role: Role::Support,
            extreme: price,
            prominence: 1.,
            lower: price - 0.01,
            upper: price + 0.01,
            half: 0.01,
            beyond: false,
            last_t: 201,
        });
        stream.previous = Some(candle(201, price));
        stream.as_of = 201;
        stream.update(candle(300, price + 2.), 300).unwrap();
        let event = stream.levels[index]
            .events
            .iter()
            .find(|e| e.at == 201)
            .unwrap();
        assert_eq!(event.outcome, Outcome::Unresolved);
        assert_eq!(event.reason.as_deref(), Some("data_gap"));
        assert_eq!(event.resolved_at, 201);
    }
    #[test]
    fn recovery_rejects_wrong_seed_and_corruption() {
        let seed = seed();
        let stream = Stream::new(
            &seed,
            1,
            20260915,
            200,
            500,
            policy(),
            &SplitAdjustment::default(),
        )
        .unwrap();
        assert!(Stream::restore(stream.checkpoint().unwrap(), "wrong").is_err());
        let mut recovery = stream.checkpoint().unwrap();
        recovery.state.as_of += 1;
        assert!(Stream::restore(recovery, &seed.hash).is_err());
    }
}
