use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Interval {
    pub start: u64,
    pub end: u64,
}
impl Interval {
    pub fn validate(self) -> Result<()> {
        if self.start >= self.end {
            Err(Error::Invalid("empty/reversed interval".into()))
        } else {
            Ok(())
        }
    }
}

/// Half-open interval subtraction. A provider sequence gap is never an input here.
pub fn missing(requested: Interval, certified: &[Interval]) -> Result<Vec<Interval>> {
    requested.validate()?;
    let mut spans = certified.to_vec();
    for s in &spans {
        s.validate()?;
    }
    spans.sort_by_key(|s| s.start);
    let mut cursor = requested.start;
    let mut result = vec![];
    for span in spans {
        if span.end <= cursor || span.start >= requested.end {
            continue;
        }
        if span.start > cursor {
            result.push(Interval {
                start: cursor,
                end: span.start.min(requested.end),
            });
        }
        cursor = cursor.max(span.end).min(requested.end);
    }
    if cursor < requested.end {
        result.push(Interval {
            start: cursor,
            end: requested.end,
        });
    }
    Ok(result)
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum Dependency {
    Trades,
    Quotes,
    Bars(u64),
    Indicator(String),
    HistoricalSeed(String),
    StreamingStructure,
    Reference,
    PreviousClose,
    OfficialLuld,
    MarketSignal(String),
    Watchlist(String),
    HistoricalLevelBook(String),
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Requirements {
    pub instruments: BTreeSet<u64>,
    pub dependencies: BTreeSet<Dependency>,
}
impl Requirements {
    pub fn merge(items: &[Self]) -> Result<Self> {
        let mut result = Self {
            instruments: BTreeSet::new(),
            dependencies: BTreeSet::new(),
        };
        for item in items {
            result.instruments.extend(&item.instruments);
            result
                .dependencies
                .extend(item.dependencies.iter().cloned());
        }
        if result.instruments.is_empty()
            || result.dependencies.is_empty()
            || result.instruments.contains(&0)
        {
            return Err(Error::Invalid("empty dependency plan".into()));
        }
        Ok(result)
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Readiness {
    pub required: BTreeSet<Dependency>,
    pub certified: BTreeMap<Dependency, Interval>,
    pub broker_reconciled: bool,
    pub fresh: bool,
}
impl Readiness {
    pub fn blocked(&self, through: Interval) -> Result<Vec<String>> {
        through.validate()?;
        let mut reasons = vec![];
        for dependency in &self.required {
            if !self
                .certified
                .get(dependency)
                .is_some_and(|c| c.start <= through.start && c.end >= through.end)
            {
                reasons.push(format!("uncertified {dependency:?}"));
            }
        }
        if !self.broker_reconciled {
            reasons.push("broker not reconciled".into());
        }
        if !self.fresh {
            reasons.push("required inputs not fresh".into());
        }
        Ok(reasons)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn intervals_merge_without_false_holes() {
        assert_eq!(
            missing(
                Interval { start: 0, end: 10 },
                &[Interval { start: 2, end: 5 }, Interval { start: 4, end: 8 }]
            )
            .unwrap(),
            vec![
                Interval { start: 0, end: 2 },
                Interval { start: 8, end: 10 }
            ]
        );
    }
    #[test]
    fn missing_derived_blocks_ready() {
        let r = Readiness {
            required: BTreeSet::from([Dependency::Trades, Dependency::StreamingStructure]),
            certified: BTreeMap::from([(Dependency::Trades, Interval { start: 1, end: 10 })]),
            broker_reconciled: true,
            fresh: true,
        };
        assert_eq!(r.blocked(Interval { start: 1, end: 10 }).unwrap().len(), 1);
    }
}
