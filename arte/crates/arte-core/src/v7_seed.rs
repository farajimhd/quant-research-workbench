//! Completed-session historical MLE producer. No streaming recovery input exists.
//! This is a new versioned historical algorithm, not the parent's streaming export.
use crate::v7_band::{partition, Band, Observation};
use crate::v7_encounters::{self, ActiveRole, Sample, SessionArrays};
use crate::v7_evidence::{annotate, Encounter, ExtremeBar, Outcome, Role};
use crate::v7_extraction::{extract, Candle, ProfileRow, Settings};
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
pub const VERSION: &str = "arte-historical-mle-seed-1";
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceCertificate {
    pub instrument: u64,
    pub ticker: String,
    pub session: u32,
    pub start_second: u64,
    pub end_second: u64,
    pub source_generation: String,
    pub input_hash: String,
    pub certified_at_second: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SeedObservation {
    pub id: String,
    pub session: u32,
    pub source_generation: String,
    pub role: Role,
    pub value: Observation,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SeedLevel {
    pub id: String,
    pub origin_session: u32,
    pub ancestry: BTreeSet<String>,
    pub observations: Vec<SeedObservation>,
    pub band: Band,
    pub association_center: f64,
    pub association_radius: f64,
    pub role: ActiveRole,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HistoricalSeed {
    pub version: String,
    pub source: SourceCertificate,
    pub previous_seed: Option<String>,
    pub available_at_second: u64,
    pub configuration_hash: String,
    pub levels: Vec<SeedLevel>,
    pub split_evidence: Vec<String>,
    pub split_factor: f64,
    pub hash: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SeedPolicy {
    pub extraction: Settings,
    pub coverage: f64,
    pub maximum_levels: usize,
    pub maximum_observations: usize,
}
impl Default for SeedPolicy {
    fn default() -> Self {
        Self {
            extraction: Settings::default(),
            coverage: 0.8,
            maximum_levels: 4096,
            maximum_observations: 1_000_000,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SplitAdjustment {
    pub factor: f64,
    pub evidence: Vec<String>,
}
impl Default for SplitAdjustment {
    fn default() -> Self {
        Self {
            factor: 1.,
            evidence: Vec::new(),
        }
    }
}
pub fn input_hash(bars: &[Candle], profile: &[ProfileRow]) -> Result<String> {
    content_hash(&(VERSION, bars, profile))
}
fn seed_hash(seed: &HistoricalSeed) -> Result<String> {
    content_hash(&(
        &seed.version,
        &seed.source,
        &seed.previous_seed,
        seed.available_at_second,
        &seed.configuration_hash,
        &seed.levels,
        &seed.split_evidence,
        seed.split_factor,
    ))
}
impl HistoricalSeed {
    pub fn verify(&self) -> Result<()> {
        if self.version != VERSION || self.hash != seed_hash(self)? {
            return Err(Error::Invalid(
                "historical seed integrity or version mismatch".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        for level in &self.levels {
            if !ids.insert(&level.id)
                || !level.association_center.is_finite()
                || level.association_center <= 0.
                || !level.association_radius.is_finite()
                || level.association_radius <= 0.
            {
                return Err(Error::Invalid(
                    "invalid historical level identity or association geometry".into(),
                ));
            }
            let mut observations = BTreeSet::new();
            if level
                .observations
                .iter()
                .any(|o| !observations.insert(&o.id) || o.session > self.source.session)
            {
                return Err(Error::Invalid(
                    "duplicate or future historical observation".into(),
                ));
            }
        }
        Ok(())
    }
    pub fn require_available(
        &self,
        instrument: u64,
        session: u32,
        start_second: u64,
    ) -> Result<()> {
        self.verify()?;
        if self.source.instrument != instrument
            || self.source.session >= session
            || self.available_at_second > start_second
        {
            return Err(Error::Unready(
                "historical seed not available for requested session".into(),
            ));
        }
        Ok(())
    }
}
/// Caller provides an ingestion-owned certificate. The builder independently
/// verifies its content hash and bounds; no market/network access occurs here.
pub fn build(
    bars: &[Candle],
    profile: &[ProfileRow],
    source: SourceCertificate,
    prior: Option<&HistoricalSeed>,
    policy: &SeedPolicy,
    split: &SplitAdjustment,
    now_second: u64,
) -> Result<HistoricalSeed> {
    if source.instrument == 0
        || source.ticker.is_empty()
        || source.source_generation.is_empty()
        || source.start_second >= source.end_second
        || source.end_second > u64::MAX / 1_000_000_000
        || source.certified_at_second < source.end_second
        || now_second < source.certified_at_second
        || source.input_hash != input_hash(bars, profile)?
        || bars.is_empty()
        || bars[0].t < source.start_second
        || bars.last().unwrap().t > source.end_second
    {
        return Err(Error::Unready(
            "complete certified session evidence required".into(),
        ));
    }
    if !split.factor.is_finite()
        || split.factor <= 0.
        || (split.factor != 1. && split.evidence.is_empty())
        || policy.maximum_levels == 0
        || policy.maximum_observations == 0
    {
        return Err(Error::Invalid(
            "invalid split evidence or seed capacity".into(),
        ));
    }
    let configuration_hash = content_hash(policy)?;
    if let Some(previous) = prior {
        previous.require_available(source.instrument, source.session, source.start_second)?;
        if previous.configuration_hash != configuration_hash {
            return Err(Error::Invalid(
                "seed configuration changed; explicit rebuild required".into(),
            ));
        }
    }
    let session = source.session.to_string();
    let extraction = extract(
        bars,
        profile,
        &source.ticker,
        &session,
        source.end_second,
        &source.source_generation,
        policy.extraction.clone(),
    )?;
    let extremes: Vec<_> = bars
        .iter()
        .map(|b| ExtremeBar {
            at_ns: b.t * 1_000_000_000,
            high: b.high,
            low: b.low,
        })
        .collect();
    // Annotation owns a nanosecond contract; convert the completed-second input
    // explicitly. The source end was checked against nanosecond overflow above.
    let arrays = SessionArrays::new(
        bars.iter()
            .map(|b| Sample {
                second: b.t,
                high: b.high,
                low: b.low,
                close: b.close,
                volume: b.volume,
            })
            .collect(),
    )?;
    let mut levels = prior.map_or_else(Vec::new, |p| p.levels.clone());
    for level in &mut levels {
        level.association_center *= split.factor;
        level.association_radius *= split.factor;
        for observation in &mut level.observations {
            observation.value.price *= split.factor;
            observation.value.resolution *= split.factor;
        }
    }
    let prior_count = levels.len();
    let mut geometries: Vec<(usize, f64, f64)> = Vec::new();
    for candidate in extraction.levels.iter().chain(&extraction.rejected) {
        let mut matches: Vec<_> = levels[..prior_count]
            .iter()
            .enumerate()
            .filter(|(_, l)| {
                (l.association_center - candidate.price).abs()
                    <= l.association_radius.max(extraction.geometry.half_width)
            })
            .map(|(i, l)| {
                (
                    i,
                    (l.association_center - candidate.price).abs(),
                    l.id.clone(),
                )
            })
            .collect();
        matches.sort_by(|a, b| a.1.total_cmp(&b.1).then(a.2.cmp(&b.2)));
        if let Some((index, _, _)) = matches.first() {
            levels[*index].ancestry.insert(candidate.id.clone());
            continue;
        }
        let band = crate::v7_band::estimate(&[], policy.coverage)?;
        let index = levels.len();
        levels.push(SeedLevel {
            id: content_hash(&(VERSION, source.instrument, source.session, &candidate.id))?,
            origin_session: source.session,
            ancestry: BTreeSet::from([candidate.id.clone()]),
            observations: Vec::new(),
            band,
            association_center: candidate.price,
            association_radius: extraction.geometry.half_width,
            role: ActiveRole::Transition,
        });
        geometries.push((index, candidate.lower, candidate.upper));
    }
    for (index, level) in levels.iter().enumerate().take(prior_count) {
        let (lower, upper) = if level.band.estimated() {
            (
                level.band.lower.unwrap() * split.factor,
                level.band.upper.unwrap() * split.factor,
            )
        } else {
            (
                level.association_center - level.association_radius,
                level.association_center + level.association_radius,
            )
        };
        geometries.push((index, lower, upper));
    }
    if levels.len() > policy.maximum_levels {
        return Err(Error::Capacity(
            "historical seed candidate capacity exceeded; no truncation".into(),
        ));
    }
    let mut observation_count: usize = levels.iter().map(|l| l.observations.len()).sum();
    for (index, lower, upper) in geometries {
        let encounters = arrays.encounters(
            lower,
            upper,
            extraction.geometry.prominence,
            ((upper - lower) / 2.).max(policy.extraction.tick),
            v7_encounters::Policy {
                reaction_seconds: policy.extraction.reaction_seconds,
                maximum_gap_seconds: policy.extraction.maximum_gap_seconds,
            },
        )?;
        let annotated = annotate(
            &encounters
                .iter()
                .map(|e| Encounter {
                    at_ns: e.at * 1_000_000_000,
                    resolved_at_ns: e.resolved_at * 1_000_000_000,
                    role: e.role,
                    outcome: e.outcome,
                    reaction_price: None,
                    reaction_at_ns: None,
                })
                .collect::<Vec<_>>(),
            &extremes,
        )?;
        let mut last = [None, None];
        for event in annotated {
            if event.outcome != Outcome::Rejection {
                continue;
            }
            let role_index = usize::from(event.role == Role::Resistance);
            if last[role_index].is_some_and(|end| event.at_ns <= end) {
                continue;
            }
            last[role_index] = Some(event.resolved_at_ns);
            observation_count += 1;
            if observation_count > policy.maximum_observations {
                return Err(Error::Capacity(
                    "historical observations exceed capacity; no eviction".into(),
                ));
            }
            let id = content_hash(&(
                source.instrument,
                source.session,
                &source.source_generation,
                event.role,
                event.at_ns,
                event.resolved_at_ns,
                event.reaction_at_ns,
            ))?;
            levels[index].observations.push(SeedObservation {
                id,
                session: source.session,
                source_generation: source.source_generation.clone(),
                role: event.role,
                value: Observation {
                    price: event.reaction_price.unwrap(),
                    resolution: policy.extraction.tick,
                    at: event.at_ns / 1_000_000_000,
                    resolved_at: event.resolved_at_ns / 1_000_000_000,
                },
            });
        }
        let segments = v7_encounters::role_timeline(
            &encounters,
            source.end_second,
            Some((source.start_second, levels[index].role)),
        )?;
        if let Some(last) = segments.last() {
            levels[index].role = last.role;
        }
    }
    let mut fitted = Vec::new();
    for level in levels {
        let values: Vec<_> = level.observations.iter().map(|o| o.value.clone()).collect();
        let mut components = partition(&values, policy.coverage)?;
        if level.band.estimated() && components.iter().any(|c| !c.band.estimated()) {
            return Err(Error::Unready(
                "previously qualified historical fit failed; no stale-band fallback".into(),
            ));
        }
        components.sort_by(|a, b| {
            (a.band.fit.center.unwrap_or(level.association_center) - level.association_center)
                .abs()
                .total_cmp(
                    &(b.band.fit.center.unwrap_or(level.association_center)
                        - level.association_center)
                        .abs(),
                )
        });
        let key = |value: &Observation| {
            (
                value.at,
                value.resolved_at,
                value.price.to_bits(),
                value.resolution.to_bits(),
            )
        };
        let mut remaining: BTreeMap<_, VecDeque<SeedObservation>> = BTreeMap::new();
        for observation in &level.observations {
            remaining
                .entry(key(&observation.value))
                .or_default()
                .push_back(observation.clone());
        }
        for (ordinal, component) in components.into_iter().enumerate() {
            let mut observations = Vec::new();
            for value in &component.observations {
                let observation = remaining
                    .get_mut(&key(value))
                    .and_then(VecDeque::pop_front)
                    .ok_or_else(|| Error::Invalid("partition lost observation identity".into()))?;
                observations.push(observation);
            }
            let id = if ordinal == 0 {
                level.id.clone()
            } else {
                content_hash(&(VERSION, &level.id, source.session, ordinal, &component.band))?
            };
            fitted.push(SeedLevel {
                id,
                origin_session: level.origin_session,
                ancestry: level.ancestry.clone(),
                observations,
                association_center: component
                    .band
                    .fit
                    .center
                    .unwrap_or(level.association_center),
                association_radius: level.association_radius,
                band: component.band,
                role: level.role,
            });
        }
    }
    if fitted.len() > policy.maximum_levels {
        return Err(Error::Capacity("fitted component capacity exceeded".into()));
    }
    fitted.sort_by(|a, b| {
        a.association_center
            .total_cmp(&b.association_center)
            .then(a.id.cmp(&b.id))
    });
    let mut seed = HistoricalSeed {
        version: VERSION.into(),
        source,
        previous_seed: prior.map(|p| p.hash.clone()),
        available_at_second: now_second,
        configuration_hash,
        levels: fitted,
        split_evidence: split.evidence.clone(),
        split_factor: split.factor,
        hash: String::new(),
    };
    seed.hash = seed_hash(&seed)?;
    seed.verify()?;
    Ok(seed)
}
#[cfg(test)]
mod tests {
    use super::*;
    fn inputs(session: u32, start: u64) -> (Vec<Candle>, SourceCertificate) {
        let bars: Vec<_> = (0..18)
            .map(|i| {
                let p = if i % 2 == 0 { 10. } else { 11. };
                Candle {
                    t: start + i,
                    open: p,
                    high: p + 0.01,
                    low: p - 0.01,
                    close: p,
                    volume: 100.,
                }
            })
            .collect();
        let source = SourceCertificate {
            instrument: 1,
            ticker: "TEST".into(),
            session,
            start_second: start,
            end_second: start + 20,
            source_generation: format!("source-{session}"),
            input_hash: input_hash(&bars, &[]).unwrap(),
            certified_at_second: start + 21,
        };
        (bars, source)
    }
    #[test]
    fn completed_seed_is_deterministic_and_not_intraday_available() {
        let (bars, source) = inputs(20260914, 100);
        let policy = SeedPolicy::default();
        let a = build(
            &bars,
            &[],
            source.clone(),
            None,
            &policy,
            &SplitAdjustment::default(),
            121,
        )
        .unwrap();
        let b = build(
            &bars,
            &[],
            source,
            None,
            &policy,
            &SplitAdjustment::default(),
            121,
        )
        .unwrap();
        assert_eq!(a.hash, b.hash);
        assert!(!a.levels.is_empty());
        assert!(a.levels.iter().any(|l| l.band.estimated()));
        assert!(a.require_available(1, 20260914, 121).is_err());
        assert!(a.require_available(1, 20260915, 200).is_ok());
    }
    #[test]
    fn evidence_and_time_are_checked_before_build() {
        let (bars, mut source) = inputs(20260914, 100);
        assert!(build(
            &bars,
            &[],
            source.clone(),
            None,
            &SeedPolicy::default(),
            &SplitAdjustment::default(),
            119
        )
        .is_err());
        source.input_hash = "wrong".into();
        assert!(build(
            &bars,
            &[],
            source,
            None,
            &SeedPolicy::default(),
            &SplitAdjustment::default(),
            121
        )
        .is_err());
    }
    #[test]
    fn next_day_preserves_prior_and_integrity() {
        let (bars, source) = inputs(20260914, 100);
        let policy = SeedPolicy::default();
        let first = build(
            &bars,
            &[],
            source,
            None,
            &policy,
            &SplitAdjustment::default(),
            121,
        )
        .unwrap();
        let before = content_hash(&first).unwrap();
        let (bars, source) = inputs(20260915, 200);
        let next = build(
            &bars,
            &[],
            source,
            Some(&first),
            &policy,
            &SplitAdjustment::default(),
            221,
        )
        .unwrap();
        assert_eq!(next.previous_seed, Some(first.hash.clone()));
        assert_eq!(before, content_hash(&first).unwrap());
        let mut corrupt = next;
        corrupt.available_at_second += 1;
        assert!(corrupt.verify().is_err());
    }
    #[test]
    fn explicit_split_keeps_original_observation_ids_and_prior_bytes() {
        let policy = SeedPolicy::default();
        let (bars, source) = inputs(20260914, 100);
        let first = build(
            &bars,
            &[],
            source,
            None,
            &policy,
            &SplitAdjustment::default(),
            121,
        )
        .unwrap();
        let before = content_hash(&first).unwrap();
        let (mut bars, mut source) = inputs(20260915, 200);
        for bar in &mut bars {
            bar.open *= 0.5;
            bar.high *= 0.5;
            bar.low *= 0.5;
            bar.close *= 0.5;
        }
        source.input_hash = input_hash(&bars, &[]).unwrap();
        assert!(build(
            &bars,
            &[],
            source.clone(),
            Some(&first),
            &policy,
            &SplitAdjustment {
                factor: 0.5,
                evidence: vec![]
            },
            221
        )
        .is_err());
        let second = build(
            &bars,
            &[],
            source,
            Some(&first),
            &policy,
            &SplitAdjustment {
                factor: 0.5,
                evidence: vec!["known-split".into()],
            },
            221,
        )
        .unwrap();
        for old in first.levels.iter().flat_map(|l| &l.observations) {
            let carried = second
                .levels
                .iter()
                .flat_map(|l| &l.observations)
                .find(|o| o.id == old.id)
                .unwrap();
            assert_eq!(carried.value.price, old.value.price * 0.5);
            assert_eq!(carried.value.at, old.value.at);
            assert!(carried.value.at < 200);
        }
        assert_eq!(before, content_hash(&first).unwrap());
        assert_eq!(second.split_factor, 0.5);
    }
    #[test]
    fn observation_capacity_is_a_failure_not_truncation() {
        let (bars, source) = inputs(20260914, 100);
        let policy = SeedPolicy {
            maximum_observations: 1,
            ..SeedPolicy::default()
        };
        assert!(matches!(
            build(
                &bars,
                &[],
                source,
                None,
                &policy,
                &SplitAdjustment::default(),
                121
            ),
            Err(Error::Capacity(_))
        ));
    }
}
