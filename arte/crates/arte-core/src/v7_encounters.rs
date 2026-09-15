//! Fixed-band historical encounters from historical-session-reaction-zones-2.
//! Input timestamps are completed-bar epoch seconds, matching the frozen source.
use crate::v7_evidence::{Outcome, Role};
use crate::{Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Sample {
    pub second: u64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Policy {
    pub reaction_seconds: u64,
    pub maximum_gap_seconds: u64,
}
impl Default for Policy {
    fn default() -> Self {
        Self {
            reaction_seconds: 180,
            maximum_gap_seconds: 60,
        }
    }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Evidence {
    pub at: u64,
    pub resolved_at: u64,
    pub role: Role,
    pub outcome: Outcome,
    pub volume: f64,
    pub relative_volume: Option<f64>,
    pub reason: Option<String>,
}
/// Canonical conversion and prefix volumes are shared by every candidate band.
pub struct SessionArrays {
    bars: Vec<Sample>,
    cumulative_volume: Vec<f64>,
}
impl SessionArrays {
    pub fn new(bars: Vec<Sample>) -> Result<Self> {
        if bars.is_empty()
            || bars.windows(2).any(|w| w[0].second >= w[1].second)
            || bars.iter().any(|b| {
                !b.high.is_finite()
                    || !b.low.is_finite()
                    || !b.close.is_finite()
                    || !b.volume.is_finite()
                    || b.low <= 0.
                    || b.low > b.close
                    || b.high < b.close
                    || b.volume < 0.
            })
        {
            return Err(Error::Invalid("invalid historical encounter bars".into()));
        }
        let mut cumulative_volume = Vec::with_capacity(bars.len() + 1);
        cumulative_volume.push(0.);
        let mut total = 0.;
        for bar in &bars {
            total += bar.volume;
            if !total.is_finite() {
                return Err(Error::Invalid("session volume overflow".into()));
            }
            cumulative_volume.push(total);
        }
        Ok(Self {
            bars,
            cumulative_volume,
        })
    }
    fn volume(&self, start: usize, end_exclusive: usize) -> f64 {
        self.cumulative_volume[end_exclusive] - self.cumulative_volume[start]
    }
    pub fn encounters(
        &self,
        lower: f64,
        upper: f64,
        prominence: f64,
        half: f64,
        policy: Policy,
    ) -> Result<Vec<Evidence>> {
        if !lower.is_finite()
            || !upper.is_finite()
            || !prominence.is_finite()
            || !half.is_finite()
            || lower <= 0.
            || upper < lower
            || prominence <= 0.
            || half <= 0.
            || policy.reaction_seconds == 0
            || policy.maximum_gap_seconds == 0
        {
            return Err(Error::Invalid(
                "invalid encounter geometry or policy".into(),
            ));
        }
        let bars = &self.bars;
        let mut result = Vec::new();
        let (mut gap_index, mut far_index, mut last_trigger) = (None, None, None);
        for i in 1..bars.len() {
            let gap = bars[i].second - bars[i - 1].second > policy.maximum_gap_seconds;
            if gap {
                gap_index = Some(i);
                continue;
            }
            if bars[i - 1].close < lower - prominence || bars[i - 1].close > upper + prominence {
                far_index = Some(i);
            }
            if bars[i].high < lower || bars[i].low > upper {
                continue;
            }
            if last_trigger.is_some() && last_trigger >= gap_index.max(far_index) {
                continue;
            }
            let role = if far_index > gap_index {
                let previous = bars[far_index.unwrap() - 1].close;
                Some(if previous < lower {
                    Role::Resistance
                } else {
                    Role::Support
                })
            } else if bars[i - 1].close < lower {
                Some(Role::Resistance)
            } else if bars[i - 1].close > upper {
                Some(Role::Support)
            } else {
                None
            };
            let Some(role) = role else {
                continue;
            };
            last_trigger = Some(i);
            let window_end = bars[i]
                .second
                .checked_add(policy.reaction_seconds)
                .ok_or_else(|| Error::Invalid("reaction window overflow".into()))?;
            let mut end = bars.partition_point(|b| b.second <= window_end);
            for k in i + 1..end {
                if bars[k].second - bars[k - 1].second > policy.maximum_gap_seconds {
                    end = k;
                    break;
                }
            }
            let mut resolved = end - 1;
            let mut outcome = Outcome::Unresolved;
            let mut previous_beyond = false;
            for k in i + 1..end {
                let (rejection, beyond) = match role {
                    Role::Resistance => (
                        bars[k].close < lower - prominence,
                        bars[k].close > upper + half,
                    ),
                    Role::Support => (
                        bars[k].close > upper + prominence,
                        bars[k].close < lower - half,
                    ),
                };
                let acceptance =
                    previous_beyond && beyond && bars[k].second - bars[k - 1].second == 1;
                if rejection || acceptance {
                    resolved = k;
                    outcome = if rejection {
                        Outcome::Rejection
                    } else {
                        Outcome::Acceptance
                    };
                    break;
                }
                previous_beyond = beyond;
            }
            let mut reason = None;
            if outcome == Outcome::Rejection {
                let turning = match role {
                    Role::Resistance => bars[i..=resolved]
                        .iter()
                        .map(|b| b.high)
                        .fold(f64::NEG_INFINITY, f64::max),
                    Role::Support => bars[i..=resolved]
                        .iter()
                        .map(|b| b.low)
                        .fold(f64::INFINITY, f64::min),
                };
                let tolerance = 1_f64.max(lower.abs()).max(upper.abs()) * 1e-12;
                if turning < lower - tolerance || turning > upper + tolerance {
                    outcome = Outcome::Unresolved;
                    reason = Some("turning_extreme_outside_band".into());
                }
            }
            let prior_second = bars[i].second.saturating_sub(60);
            let prior = bars.partition_point(|b| b.second < prior_second);
            let baseline_duration = (bars[i].second - bars[0].second.max(prior_second)).max(1);
            let baseline = self.volume(prior, i) / baseline_duration as f64;
            let volume = self.volume(i, resolved + 1);
            let duration = (bars[resolved].second - bars[i].second + 1).max(1);
            result.push(Evidence {
                at: bars[i].second,
                resolved_at: bars[resolved].second,
                role,
                outcome,
                volume,
                relative_volume: (baseline > 0.).then_some(volume / duration as f64 / baseline),
                reason,
            });
        }
        Ok(result)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActiveRole {
    Support,
    Resistance,
    Transition,
}
impl From<Role> for ActiveRole {
    fn from(role: Role) -> Self {
        match role {
            Role::Support => Self::Support,
            Role::Resistance => Self::Resistance,
        }
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Segment {
    pub start: u64,
    pub end: u64,
    pub role: ActiveRole,
    pub contact_at: Option<u64>,
    pub reason: String,
}
/// Crossings remove confirmation; they never establish the opposite role.
pub fn role_timeline(
    events: &[Evidence],
    session_end: u64,
    carried: Option<(u64, ActiveRole)>,
) -> Result<Vec<Segment>> {
    let mut segments = Vec::<Segment>::new();
    if let Some((start, role)) = carried {
        if start > session_end {
            return Err(Error::Invalid(
                "carried role starts after session end".into(),
            ));
        }
        segments.push(Segment {
            start,
            end: session_end,
            role,
            contact_at: None,
            reason: "carried_checkpoint".into(),
        });
    }
    let mut ordered: Vec<_> = events.iter().collect();
    ordered.sort_by_key(|e| (e.resolved_at, e.at));
    let mut latest_contact = None;
    for event in ordered {
        if event.at > event.resolved_at
            || event.resolved_at > session_end
            || carried.is_some_and(|(start, _)| event.at < start)
        {
            return Err(Error::Invalid("encounter outside role timeline".into()));
        }
        if event.outcome == Outcome::Unresolved || latest_contact.is_some_and(|t| event.at < t) {
            continue;
        }
        let current = segments.last().map(|s| s.role);
        let role = if event.outcome == Outcome::Rejection {
            ActiveRole::from(event.role)
        } else if current == Some(ActiveRole::from(event.role)) {
            ActiveRole::Transition
        } else {
            continue;
        };
        latest_contact = Some(event.at);
        if current == Some(role) {
            continue;
        }
        if let Some(previous) = segments.last_mut() {
            previous.end = event.resolved_at;
            if previous.start == previous.end {
                segments.pop();
            }
        }
        segments.push(Segment {
            start: event.resolved_at,
            end: session_end,
            role,
            contact_at: Some(event.at),
            reason: if role == ActiveRole::Transition {
                "accepted_crossing_awaiting_retest"
            } else {
                "confirmed_rejection"
            }
            .into(),
        });
    }
    Ok(segments)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn sample(second: u64, low: f64, high: f64, close: f64) -> Sample {
        Sample {
            second,
            low,
            high,
            close,
            volume: 10.,
        }
    }
    #[test]
    fn rejection_requires_turn_inside_band() {
        let bars = vec![
            sample(1, 7., 8., 8.),
            sample(2, 9., 10., 9.5),
            sample(3, 7., 8., 7.),
        ];
        let first = SessionArrays::new(bars.clone())
            .unwrap()
            .encounters(9., 11., 1., 1., Policy::default())
            .unwrap();
        assert_eq!(first[0].outcome, Outcome::Rejection);
        let mut outside = bars;
        outside[1].high = 12.;
        let second = SessionArrays::new(outside)
            .unwrap()
            .encounters(9., 11., 1., 1., Policy::default())
            .unwrap();
        assert_eq!(
            second[0].reason.as_deref(),
            Some("turning_extreme_outside_band")
        );
    }
    #[test]
    fn acceptance_requires_adjacent_seconds_and_gap_ends_encounter() {
        let a = SessionArrays::new(vec![
            sample(1, 7., 8., 8.),
            sample(2, 9., 11., 10.),
            sample(3, 12., 14., 13.),
            sample(5, 12., 14., 13.),
        ])
        .unwrap();
        assert_eq!(
            a.encounters(9., 11., 1., 1., Policy::default()).unwrap()[0].outcome,
            Outcome::Unresolved
        );
        let b = SessionArrays::new(vec![
            sample(1, 7., 8., 8.),
            sample(2, 9., 11., 10.),
            sample(3, 12., 14., 13.),
            sample(4, 12., 14., 13.),
        ])
        .unwrap();
        assert_eq!(
            b.encounters(9., 11., 1., 1., Policy::default()).unwrap()[0].outcome,
            Outcome::Acceptance
        );
        let c = SessionArrays::new(vec![
            sample(1, 7., 8., 8.),
            sample(2, 9., 11., 10.),
            sample(100, 6., 7., 6.),
        ])
        .unwrap();
        assert_eq!(
            c.encounters(9., 11., 1., 1., Policy::default()).unwrap()[0].resolved_at,
            2
        );
    }
    #[test]
    fn crossing_does_not_confirm_other_role() {
        let e = Evidence {
            at: 2,
            resolved_at: 3,
            role: Role::Support,
            outcome: Outcome::Acceptance,
            volume: 1.,
            relative_volume: None,
            reason: None,
        };
        let segments = role_timeline(&[e], 10, Some((1, ActiveRole::Support))).unwrap();
        assert_eq!(segments[0].end, 3);
        assert_eq!(segments[1].role, ActiveRole::Transition);
    }
}
