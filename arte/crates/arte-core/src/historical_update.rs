//! Offline dependency plan for one historical update invocation.
//! Execution and publication require separate connected adapters after extraction.
use crate::{content_hash, estimated_luld::CONTRACT as LULD_CONTRACT, Error, Result};
use chrono::NaiveDate;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub const CONTRACT: &str = "arte.historical-update-plan.v1";

fn hash_ok(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    CertifiedCompactReadOnly,
    ArteFlatfile,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Day {
    pub session: u32,
    pub source: Source,
    /// Existing compact certificate or pinned remote flatfile inventory identity.
    /// A new flatfile certificate is produced only after verified ingestion.
    pub source_identity_hash: String,
    pub reporting_revision_hash: String,
    pub universe_hash: String,
    pub condition_rules_hash: String,
    pub split_reference_hash: String,
    pub estimated_luld_policy_hash: String,
    /// Absent means halt coverage must be published as unknown, not open.
    pub halt_source_hash: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub days: Vec<Day>,
    pub maximum_days: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    Source,
    EligibleTrades500ms,
    Liquidity100ms,
    Bars,
    Indicators,
    HistoricalV7,
    HaltEpisodes,
    EstimatedLuld500ms,
    Readback,
    Publish,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Unit {
    pub session: u32,
    pub stage: Stage,
    pub depends_on: Vec<Stage>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Plan {
    pub contract: String,
    pub plan_hash: String,
    pub estimated_luld_contract: String,
    pub units: Vec<Unit>,
    pub unknown_halt_sessions: Vec<u32>,
    pub connected_execution_enabled: bool,
}

impl Request {
    pub fn plan(&self) -> Result<Plan> {
        if self.days.is_empty() || self.maximum_days == 0 || self.days.len() > self.maximum_days {
            return Err(Error::Invalid("historical update day budget".into()));
        }
        let mut seen = BTreeSet::new();
        for day in &self.days {
            let year = (day.session / 10_000) as i32;
            let month = day.session / 100 % 100;
            let date = day.session % 100;
            if !(1900..=2999).contains(&year)
                || NaiveDate::from_ymd_opt(year, month, date).is_none()
                || !seen.insert(day.session)
                || ![
                    &day.source_identity_hash,
                    &day.reporting_revision_hash,
                    &day.universe_hash,
                    &day.condition_rules_hash,
                    &day.split_reference_hash,
                    &day.estimated_luld_policy_hash,
                ]
                .iter()
                .all(|hash| hash_ok(hash))
                || day
                    .halt_source_hash
                    .as_deref()
                    .is_some_and(|hash| !hash_ok(hash))
            {
                return Err(Error::Invalid("historical update day contract".into()));
            }
        }
        let plan_hash = content_hash(&(CONTRACT, self))?;
        let mut units = Vec::with_capacity(self.days.len() * 10);
        let mut unknown_halt_sessions = Vec::new();
        for day in &self.days {
            use Stage::*;
            let stages = [
                (Source, vec![]),
                (EligibleTrades500ms, vec![Source]),
                (Liquidity100ms, vec![Source]),
                (Bars, vec![Liquidity100ms]),
                (Indicators, vec![Bars]),
                (HistoricalV7, vec![Source, Bars]),
                (HaltEpisodes, vec![Source]),
                (EstimatedLuld500ms, vec![EligibleTrades500ms]),
                (
                    Readback,
                    vec![
                        Source,
                        Bars,
                        Indicators,
                        HistoricalV7,
                        HaltEpisodes,
                        EstimatedLuld500ms,
                    ],
                ),
                (Publish, vec![Readback]),
            ];
            units.extend(stages.into_iter().map(|(stage, depends_on)| Unit {
                session: day.session,
                stage,
                depends_on,
            }));
            if day.halt_source_hash.is_none() {
                unknown_halt_sessions.push(day.session);
            }
        }
        Ok(Plan {
            contract: CONTRACT.into(),
            plan_hash,
            estimated_luld_contract: LULD_CONTRACT.into(),
            units,
            unknown_halt_sessions,
            connected_execution_enabled: false,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn includes_all_required_products_without_granting_execution() {
        let hash = "a".repeat(64);
        let request = Request {
            maximum_days: 2,
            days: vec![Day {
                session: 20260924,
                source: Source::CertifiedCompactReadOnly,
                source_identity_hash: hash.clone(),
                reporting_revision_hash: hash.clone(),
                universe_hash: hash.clone(),
                condition_rules_hash: hash.clone(),
                split_reference_hash: hash.clone(),
                estimated_luld_policy_hash: hash,
                halt_source_hash: None,
            }],
        };
        let plan = request.plan().unwrap();
        assert_eq!(plan.units.len(), 10);
        assert_eq!(plan.unknown_halt_sessions, vec![20260924]);
        assert!(!plan.connected_execution_enabled);
        assert_eq!(plan.units.last().unwrap().stage, Stage::Publish);
    }

    #[test]
    fn invalid_calendar_day_and_duplicate_day_fail_before_planning() {
        let hash = "a".repeat(64);
        let day = Day {
            session: 20260230,
            source: Source::ArteFlatfile,
            source_identity_hash: hash.clone(),
            reporting_revision_hash: hash.clone(),
            universe_hash: hash.clone(),
            condition_rules_hash: hash.clone(),
            split_reference_hash: hash.clone(),
            estimated_luld_policy_hash: hash,
            halt_source_hash: None,
        };
        assert!(Request {
            days: vec![day.clone()],
            maximum_days: 2
        }
        .plan()
        .is_err());
        let valid = Day {
            session: 20260227,
            ..day
        };
        assert!(Request {
            days: vec![valid.clone(), valid],
            maximum_days: 2
        }
        .plan()
        .is_err());
    }
}
