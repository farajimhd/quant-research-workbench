//! Reaction evidence semantics from the frozen reaction-center reference.
//! Historical annotation is retrospective and must never feed that session's live decisions.
use crate::{Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    Support,
    Resistance,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Rejection,
    Acceptance,
    Unresolved,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Encounter {
    pub at_ns: u64,
    pub resolved_at_ns: u64,
    pub role: Role,
    pub outcome: Outcome,
    pub reaction_price: Option<f64>,
    pub reaction_at_ns: Option<u64>,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct ExtremeBar {
    pub at_ns: u64,
    pub high: f64,
    pub low: f64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Contribution {
    pub session: u32,
    pub source_hash: String,
    pub reaction_price_factor: f64,
    pub encounters: Vec<Encounter>,
}
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct RoleEvidence {
    pub prices: Vec<f64>,
    pub overlapping_excluded: usize,
    pub missing_prices: usize,
    pub accepted_crossings_excluded: usize,
    pub unresolved_excluded: usize,
}

fn valid_encounter(event: &Encounter) -> Result<()> {
    if event.resolved_at_ns < event.at_ns
        || event
            .reaction_price
            .is_some_and(|p| !p.is_finite() || p <= 0.)
        || event.reaction_price.is_some() != event.reaction_at_ns.is_some()
        || event
            .reaction_at_ns
            .is_some_and(|t| t < event.at_ns || t > event.resolved_at_ns)
    {
        return Err(Error::Invalid("invalid reaction encounter".into()));
    }
    Ok(())
}

/// Inclusive contact-through-resolution extremes, retaining the earliest tied extreme.
/// Input is unchanged on error. Existing annotations are recomputed, never trusted.
pub fn annotate(events: &[Encounter], bars: &[ExtremeBar]) -> Result<Vec<Encounter>> {
    if bars.windows(2).any(|w| w[0].at_ns >= w[1].at_ns)
        || bars
            .iter()
            .any(|b| !b.high.is_finite() || !b.low.is_finite() || b.low <= 0. || b.high < b.low)
    {
        return Err(Error::Invalid(
            "reaction bars must have valid geometry and unique ordered timestamps".into(),
        ));
    }
    let mut annotated = events.to_vec();
    for event in &mut annotated {
        if event.resolved_at_ns < event.at_ns {
            return Err(Error::Invalid("encounter resolves before contact".into()));
        }
        event.reaction_price = None;
        event.reaction_at_ns = None;
        if event.outcome != Outcome::Rejection {
            continue;
        }
        let start = bars
            .binary_search_by_key(&event.at_ns, |b| b.at_ns)
            .map_err(|_| Error::Unready("reaction contact timestamp missing from bars".into()))?;
        let end = bars
            .binary_search_by_key(&event.resolved_at_ns, |b| b.at_ns)
            .map_err(|_| {
                Error::Unready("reaction resolution timestamp missing from bars".into())
            })?;
        let mut turning = bars[start];
        for &bar in &bars[start + 1..end + 1] {
            let better = match event.role {
                Role::Support => bar.low < turning.low,
                Role::Resistance => bar.high > turning.high,
            };
            if better {
                turning = bar;
            }
        }
        event.reaction_price = Some(match event.role {
            Role::Support => turning.low,
            Role::Resistance => turning.high,
        });
        event.reaction_at_ns = Some(turning.at_ns);
    }
    Ok(annotated)
}

/// First nonoverlapping resolved rejection per role and session, as in the source.
/// Missing evidence does not consume an overlap interval. Split factors affect only
/// fit coordinates, not the original retained encounter records.
pub fn collect(contributions: &[Contribution], role: Role) -> Result<RoleEvidence> {
    if contributions
        .windows(2)
        .any(|w| w[0].session >= w[1].session)
    {
        return Err(Error::Invalid(
            "reaction contributions must be unique increasing sessions".into(),
        ));
    }
    let mut evidence = RoleEvidence::default();
    for day in contributions {
        if day.source_hash.is_empty()
            || !day.reaction_price_factor.is_finite()
            || day.reaction_price_factor <= 0.
        {
            return Err(Error::Invalid(
                "reaction contribution lacks source or valid price factor".into(),
            ));
        }
        let mut ordered: Vec<_> = day.encounters.iter().collect();
        ordered.sort_by_key(|e| (e.at_ns, e.resolved_at_ns));
        let mut last_resolution = None;
        for event in ordered {
            valid_encounter(event)?;
            if event.role != role {
                continue;
            }
            match event.outcome {
                Outcome::Acceptance => {
                    evidence.accepted_crossings_excluded += 1;
                    continue;
                }
                Outcome::Unresolved => {
                    evidence.unresolved_excluded += 1;
                    continue;
                }
                Outcome::Rejection => {}
            }
            let Some(price) = event.reaction_price else {
                evidence.missing_prices += 1;
                continue;
            };
            if last_resolution.is_some_and(|end| event.at_ns <= end) {
                evidence.overlapping_excluded += 1;
                continue;
            }
            let adjusted = price * day.reaction_price_factor;
            if !adjusted.is_finite() || adjusted <= 0. {
                return Err(Error::Invalid("reaction price adjustment overflow".into()));
            }
            evidence.prices.push(adjusted);
            last_resolution = Some(event.resolved_at_ns);
        }
    }
    Ok(evidence)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn event(at: u64, end: u64, role: Role, price: Option<f64>) -> Encounter {
        Encounter {
            at_ns: at,
            resolved_at_ns: end,
            role,
            outcome: Outcome::Rejection,
            reaction_price: price,
            reaction_at_ns: price.map(|_| at),
        }
    }
    fn day(events: Vec<Encounter>) -> Contribution {
        Contribution {
            session: 20260914,
            source_hash: "source".into(),
            reaction_price_factor: 1.,
            encounters: events,
        }
    }
    #[test]
    fn includes_resolution_and_earliest_tie() {
        let bars = [
            ExtremeBar {
                at_ns: 1,
                high: 11.,
                low: 9.,
            },
            ExtremeBar {
                at_ns: 2,
                high: 12.,
                low: 8.,
            },
            ExtremeBar {
                at_ns: 3,
                high: 12.,
                low: 7.,
            },
        ];
        let values = annotate(
            &[
                event(1, 3, Role::Resistance, None),
                event(1, 3, Role::Support, None),
            ],
            &bars,
        )
        .unwrap();
        assert_eq!(
            (values[0].reaction_price, values[0].reaction_at_ns),
            (Some(12.), Some(2))
        );
        assert_eq!(
            (values[1].reaction_price, values[1].reaction_at_ns),
            (Some(7.), Some(3))
        );
    }
    #[test]
    fn single_bar_and_missing_boundary() {
        let bars = [ExtremeBar {
            at_ns: 1,
            high: 11.,
            low: 9.,
        }];
        assert_eq!(
            annotate(&[event(1, 1, Role::Support, None)], &bars).unwrap()[0].reaction_price,
            Some(9.)
        );
        assert!(annotate(&[event(1, 2, Role::Support, None)], &bars).is_err());
    }
    #[test]
    fn overlap_is_inclusive_missing_does_not_consume_interval() {
        let input = day(vec![
            event(1, 4, Role::Support, None),
            event(2, 3, Role::Support, Some(10.)),
            event(3, 5, Role::Support, Some(11.)),
            event(4, 6, Role::Support, Some(12.)),
        ]);
        let result = collect(&[input], Role::Support).unwrap();
        assert_eq!(result.prices, vec![10., 12.]);
        assert_eq!(result.missing_prices, 1);
        assert_eq!(result.overlapping_excluded, 1);
    }
    #[test]
    fn split_coordinates_and_role_filters_preserve_source() {
        let mut input = day(vec![
            event(1, 3, Role::Support, Some(10.)),
            event(1, 3, Role::Resistance, Some(12.)),
        ]);
        input.reaction_price_factor = 0.5;
        let before = serde_json::to_string(&input).unwrap();
        assert_eq!(
            collect(std::slice::from_ref(&input), Role::Support)
                .unwrap()
                .prices,
            vec![5.]
        );
        assert_eq!(
            collect(std::slice::from_ref(&input), Role::Resistance)
                .unwrap()
                .prices,
            vec![6.]
        );
        assert_eq!(before, serde_json::to_string(&input).unwrap());
    }
    #[test]
    fn excluded_outcomes_and_invalid_order_are_explicit() {
        let mut accepted = event(1, 2, Role::Support, None);
        accepted.outcome = Outcome::Acceptance;
        let mut unresolved = event(3, 4, Role::Support, None);
        unresolved.outcome = Outcome::Unresolved;
        let input = day(vec![accepted, unresolved]);
        let evidence = collect(std::slice::from_ref(&input), Role::Support).unwrap();
        assert_eq!(evidence.accepted_crossings_excluded, 1);
        assert_eq!(evidence.unresolved_excluded, 1);
        assert!(collect(&[input.clone(), input], Role::Support).is_err());
    }
}
