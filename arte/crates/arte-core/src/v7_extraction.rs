//! Retrospective V7 session candidates. Never consume the current session's result intraday.
use crate::v7_encounters::{self, Evidence, Sample, Segment, SessionArrays};
use crate::v7_evidence::{Outcome, Role};
use crate::v7_peaks::find_peaks;
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;

pub const SOURCE_VERSION: &str = "historical-session-reaction-zones-2";
pub const CONTRACT_VERSION: &str = "arte-historical-extraction-1";
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    pub tick: f64,
    pub noise_multiple: f64,
    pub range_fraction: f64,
    pub band_noise_multiple: f64,
    pub reaction_seconds: u64,
    pub maximum_gap_seconds: u64,
    pub minimum_rejections: usize,
    pub minimum_role_rejection_fraction: f64,
    pub maximum_candidates: usize,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            tick: 0.01,
            noise_multiple: 6.,
            range_fraction: 0.05,
            band_noise_multiple: 1.,
            reaction_seconds: 180,
            maximum_gap_seconds: 60,
            minimum_rejections: 2,
            minimum_role_rejection_fraction: 0.6,
            maximum_candidates: 512,
        }
    }
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Candle {
    pub t: u64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct ProfileRow {
    pub price: f64,
    pub volume: f64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Geometry {
    pub noise: f64,
    pub prominence: f64,
    pub half_width: f64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Level {
    pub id: String,
    pub lower: f64,
    pub upper: f64,
    pub price: f64,
    pub support_rejections: usize,
    pub resistance_rejections: usize,
    pub accepted_crossings: usize,
    pub support_rejection_fraction: f64,
    pub resistance_rejection_fraction: f64,
    pub encounters: Vec<Evidence>,
    pub role_segments: Vec<Segment>,
    pub first_confirmed_at: Option<u64>,
    pub profile_volume: f64,
    pub proposal_sources: BTreeSet<String>,
    pub proposal_count: usize,
    pub proposal_min: f64,
    pub proposal_max: f64,
    pub closing_role: String,
    pub evidence_role: String,
    pub available_at: u64,
    pub rejection_reason: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Extraction {
    pub contract_version: String,
    pub source_version: String,
    pub ticker: String,
    pub session: String,
    pub available_at: u64,
    pub retrospective: bool,
    pub source_generation: String,
    pub input_hash: String,
    pub settings: Settings,
    pub geometry: Geometry,
    pub bar_count: usize,
    pub proposal_count: usize,
    pub levels: Vec<Level>,
    pub rejected: Vec<Level>,
}
#[derive(Debug, Clone)]
struct Proposal {
    price: f64,
    weight: f64,
    kind: &'static str,
}
fn round8(x: f64) -> f64 {
    (x * 1e8).round_ties_even() / 1e8
}
fn median(x: &mut [f64]) -> f64 {
    x.sort_by(f64::total_cmp);
    if x.len().is_multiple_of(2) {
        x[x.len() / 2 - 1] / 2. + x[x.len() / 2] / 2.
    } else {
        x[x.len() / 2]
    }
}
/// ARTE hashes its own typed input contract. It does not claim byte equivalence
/// with Python JSON hashes; origin lineage records that distinction explicitly.
pub fn extract(
    bars: &[Candle],
    profile: &[ProfileRow],
    ticker: &str,
    session: &str,
    available_at: u64,
    source_generation: &str,
    settings: Settings,
) -> Result<Extraction> {
    let s = &settings;
    if [
        s.tick,
        s.noise_multiple,
        s.range_fraction,
        s.band_noise_multiple,
    ]
    .iter()
    .any(|v| !v.is_finite() || *v <= 0.)
        || !s.minimum_role_rejection_fraction.is_finite()
        || !(0. ..=1.).contains(&s.minimum_role_rejection_fraction)
        || s.reaction_seconds == 0
        || s.maximum_gap_seconds == 0
        || s.minimum_rejections == 0
        || s.maximum_candidates == 0
    {
        return Err(Error::Invalid("invalid V7 extraction settings".into()));
    }
    if ticker.is_empty()
        || session.is_empty()
        || source_generation.is_empty()
        || bars.is_empty()
        || bars.windows(2).any(|w| w[0].t >= w[1].t)
        || bars.iter().any(|b| {
            [b.open, b.high, b.low, b.close, b.volume]
                .iter()
                .any(|v| !v.is_finite())
                || b.low <= 0.
                || b.volume < 0.
                || b.high < b.open.max(b.close)
                || b.low > b.open.min(b.close)
        })
        || profile.iter().any(|p| {
            !p.price.is_finite() || !p.volume.is_finite() || p.price <= 0. || p.volume < 0.
        })
    {
        return Err(Error::Invalid("invalid V7 extraction source".into()));
    }
    if available_at < bars.last().unwrap().t {
        return Err(Error::Unready(
            "historical extraction precedes session completion".into(),
        ));
    }
    let mut ranges: Vec<_> = bars
        .iter()
        .map(|b| b.high - b.low)
        .filter(|r| *r > 0.)
        .collect();
    let noise = if ranges.is_empty() {
        s.tick
    } else {
        median(&mut ranges)
    };
    let high = bars
        .iter()
        .map(|b| b.high)
        .fold(f64::NEG_INFINITY, f64::max);
    let low = bars.iter().map(|b| b.low).fold(f64::INFINITY, f64::min);
    let prominence = (3. * s.tick)
        .max(s.noise_multiple * noise)
        .max((high - low) * s.range_fraction);
    let half = s
        .tick
        .max((s.band_noise_multiple * noise / s.tick - 1e-9).ceil() * s.tick);
    if !prominence.is_finite() || !half.is_finite() || (high / s.tick).abs() > 1e15 {
        return Err(Error::Invalid("unrepresentable extraction geometry".into()));
    }
    let mut cuts = vec![0];
    cuts.extend((1..bars.len()).filter(|&i| bars[i].t - bars[i - 1].t > s.maximum_gap_seconds));
    cuts.push(bars.len());
    let mut proposals = Vec::new();
    for window in cuts.windows(2) {
        let section = &bars[window[0]..window[1]];
        for kind in ["high", "low"] {
            let values: Vec<_> = section
                .iter()
                .map(|b| if kind == "high" { b.high } else { -b.low })
                .collect();
            for peak in find_peaks(&values, prominence)? {
                proposals.push(Proposal {
                    price: values[peak.index].abs(),
                    weight: peak.prominence,
                    kind,
                });
            }
            proposals.push(Proposal {
                price: values
                    .iter()
                    .copied()
                    .fold(f64::NEG_INFINITY, f64::max)
                    .abs(),
                weight: prominence,
                kind,
            });
        }
    }
    if !profile.is_empty() {
        let first = (profile
            .iter()
            .map(|p| p.price)
            .fold(f64::INFINITY, f64::min)
            / half)
            .floor();
        let last = (profile
            .iter()
            .map(|p| p.price)
            .fold(f64::NEG_INFINITY, f64::max)
            / half)
            .ceil();
        if !first.is_finite() || !last.is_finite() || last - first > 100_000. || last > 1e15 {
            return Err(Error::Capacity(
                "V7 profile geometry exceeds exact bounded bins".into(),
            ));
        }
        let mut histogram = vec![0.; (last - first) as usize + 1];
        for row in profile {
            let index = ((row.price / half).floor() - first) as usize;
            histogram[index] += row.volume;
            if !histogram[index].is_finite() {
                return Err(Error::Invalid("profile volume overflow".into()));
            }
        }
        let smooth = if histogram.len() >= 3 {
            (0..histogram.len())
                .map(|i| {
                    (histogram[i] / 3.)
                        + if i > 0 { histogram[i - 1] / 3. } else { 0. }
                        + if i + 1 < histogram.len() {
                            histogram[i + 1] / 3.
                        } else {
                            0.
                        }
                })
                .collect::<Vec<_>>()
        } else {
            histogram
        };
        let threshold = (smooth.iter().copied().fold(0., f64::max) * 0.08).max(1.);
        for peak in find_peaks(&smooth, threshold)? {
            proposals.push(Proposal {
                price: (peak.index as f64 + first + 0.5) * half,
                weight: prominence,
                kind: "volume",
            });
        }
    }
    proposals.sort_by(|a, b| {
        a.price
            .total_cmp(&b.price)
            .then(a.weight.total_cmp(&b.weight))
            .then(a.kind.cmp(b.kind))
    });
    let proposal_count = proposals.len();
    let mut groups = Vec::<Vec<Proposal>>::new();
    for proposal in proposals {
        if groups
            .last()
            .is_none_or(|g| proposal.price - g[0].price > 2. * half)
        {
            groups.push(Vec::new());
        }
        groups.last_mut().unwrap().push(proposal);
    }
    if groups.len() > s.maximum_candidates {
        return Err(Error::Capacity(format!(
            "{} candidates exceed {}; no truncation",
            groups.len(),
            s.maximum_candidates
        )));
    }
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
    let mut levels = Vec::new();
    let mut rejected = Vec::new();
    for group in groups {
        let weight: f64 = group.iter().map(|p| p.weight).sum();
        if !weight.is_finite() {
            return Err(Error::Invalid("proposal weight overflow".into()));
        }
        let mut accumulated = 0.;
        let mut center = group.last().unwrap().price;
        for p in &group {
            accumulated += p.weight;
            if accumulated >= weight / 2. {
                center = p.price;
                break;
            }
        }
        center = round8((center / s.tick + 0.5).floor() * s.tick);
        let lower = round8(center - half);
        let upper = round8(center + half);
        if lower <= 0. || !upper.is_finite() || upper <= lower {
            return Err(Error::Invalid("invalid rounded band geometry".into()));
        }
        let encounters = arrays.encounters(
            lower,
            upper,
            prominence,
            half,
            v7_encounters::Policy {
                reaction_seconds: s.reaction_seconds,
                maximum_gap_seconds: s.maximum_gap_seconds,
            },
        )?;
        let wins = |role| {
            encounters
                .iter()
                .filter(|e| e.role == role && e.outcome == Outcome::Rejection)
                .count()
        };
        let losses = |role| {
            encounters
                .iter()
                .filter(|e| e.role == role && e.outcome == Outcome::Acceptance)
                .count()
        };
        let supports = wins(Role::Support);
        let resistances = wins(Role::Resistance);
        let support_quality = supports as f64 / (supports + losses(Role::Support)).max(1) as f64;
        let resistance_quality =
            resistances as f64 / (resistances + losses(Role::Resistance)).max(1) as f64;
        let selected = (supports >= s.minimum_rejections
            && support_quality >= s.minimum_role_rejection_fraction)
            || (resistances >= s.minimum_rejections
                && resistance_quality >= s.minimum_role_rejection_fraction);
        let role_segments = v7_encounters::role_timeline(&encounters, available_at, None)?;
        let identity = format!("{SOURCE_VERSION}|{ticker}|{session}|{lower:.8}|{upper:.8}");
        let profile_volume: f64 = profile
            .iter()
            .filter(|p| p.price >= lower && p.price <= upper)
            .map(|p| p.volume)
            .sum();
        if !profile_volume.is_finite() {
            return Err(Error::Invalid("level profile volume overflow".into()));
        }
        let level = Level {
            id: format!("{:x}", Sha256::digest(identity.as_bytes()))[..16].into(),
            lower,
            upper,
            price: center,
            support_rejections: supports,
            resistance_rejections: resistances,
            accepted_crossings: encounters
                .iter()
                .filter(|e| e.outcome == Outcome::Acceptance)
                .count(),
            support_rejection_fraction: support_quality,
            resistance_rejection_fraction: resistance_quality,
            encounters,
            first_confirmed_at: role_segments.first().map(|s| s.start),
            role_segments,
            profile_volume,
            proposal_sources: group.iter().map(|p| p.kind.into()).collect(),
            proposal_count: group.len(),
            proposal_min: group[0].price,
            proposal_max: group.last().unwrap().price,
            closing_role: if bars.last().unwrap().close > upper {
                "support"
            } else if bars.last().unwrap().close < lower {
                "resistance"
            } else {
                "within_band"
            }
            .into(),
            evidence_role: if supports > 0 && resistances > 0 {
                "both"
            } else if supports > 0 {
                "support"
            } else if resistances > 0 {
                "resistance"
            } else {
                "unconfirmed"
            }
            .into(),
            available_at,
            rejection_reason: (!selected)
                .then(|| "insufficient_repeated_role_rejection_evidence".into()),
        };
        if selected {
            levels.push(level);
        } else {
            rejected.push(level);
        }
    }
    Ok(Extraction {
        contract_version: CONTRACT_VERSION.into(),
        source_version: SOURCE_VERSION.into(),
        ticker: ticker.into(),
        session: session.into(),
        available_at,
        retrospective: true,
        source_generation: source_generation.into(),
        input_hash: content_hash(&(CONTRACT_VERSION, bars, profile))?,
        settings,
        geometry: Geometry {
            noise,
            prominence,
            half_width: half,
        },
        bar_count: bars.len(),
        proposal_count,
        levels,
        rejected,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn candle(t: u64, price: f64) -> Candle {
        Candle {
            t,
            open: price,
            high: price + 0.01,
            low: price - 0.01,
            close: price,
            volume: 1.,
        }
    }
    #[test]
    fn deterministic_retrospective_geometry_and_auditable_rejections() {
        let bars: Vec<_> = [10., 10.5, 10., 10.5, 10.]
            .iter()
            .enumerate()
            .map(|(i, &p)| candle(i as u64 + 1, p))
            .collect();
        let a = extract(
            &bars,
            &[],
            "TEST",
            "2026-09-14",
            5,
            "certified-source",
            Settings::default(),
        )
        .unwrap();
        let b = extract(
            &bars,
            &[],
            "TEST",
            "2026-09-14",
            5,
            "certified-source",
            Settings::default(),
        )
        .unwrap();
        assert_eq!(content_hash(&a).unwrap(), content_hash(&b).unwrap());
        assert!(a.retrospective);
        assert!(a.proposal_count >= 4);
        assert!(!a.rejected.is_empty());
        assert!(a
            .levels
            .iter()
            .chain(&a.rejected)
            .all(|l| l.available_at == 5));
        assert!(extract(
            &bars,
            &[],
            "TEST",
            "2026-09-14",
            4,
            "certified-source",
            Settings::default()
        )
        .is_err());
    }
    #[test]
    fn capacity_rejects_instead_of_truncating() {
        let bars = [candle(1, 10.), candle(2, 20.)];
        let settings = Settings {
            maximum_candidates: 1,
            ..Settings::default()
        };
        assert!(matches!(
            extract(&bars, &[], "TEST", "2026-09-14", 2, "source", settings),
            Err(Error::Capacity(_))
        ));
    }
    #[test]
    fn extreme_price_does_not_widen_noise_band() {
        let bars = [candle(1, 10.), candle(2, 100.), candle(3, 10.)];
        let result = extract(
            &bars,
            &[],
            "TEST",
            "2026-09-14",
            3,
            "source",
            Settings::default(),
        )
        .unwrap();
        assert!(result.geometry.half_width <= 0.03);
        assert!(result.geometry.prominence > 4.);
    }
}
