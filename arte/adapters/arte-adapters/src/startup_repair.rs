//! Translate declared source requirements into resumable maintenance jobs.
//! A source-complete report does not certify derived state, warming or trading.
use crate::maintenance::Job;
use arte_core::{
    acquisition::{Authority, Catalog},
    content_hash,
    coverage::Dependency,
    dependency_plan::{Key, Plan},
    events::EventKind,
    Error, Result,
};
use serde::Serialize;
use std::collections::BTreeMap;

#[derive(Clone)]
pub struct Binding {
    pub key: Key,
    pub implementation_hash: String,
    pub authority: Authority,
    pub symbol: String,
}
pub struct Limits {
    pub maximum_jobs: usize,
    pub maximum_pages: usize,
    pub recovery_bytes: usize,
}
#[derive(Debug, Serialize)]
pub struct Report {
    pub dependency_plan_hash: String,
    pub checked_at_ns: u64,
    pub jobs: Vec<Job>,
    /// These dependencies require their own verified materialization/warming.
    pub unresolved_derived: Vec<Key>,
}

pub fn plan(
    dependencies: &Plan,
    bindings: Vec<Binding>,
    catalog: &Catalog,
    as_of_ns: u64,
    limits: &Limits,
) -> Result<Report> {
    if as_of_ns == 0 || limits.maximum_jobs == 0 || limits.maximum_jobs > 100_000 {
        return Err(Error::Invalid("startup repair clock or job bound".into()));
    }
    let mut registry = BTreeMap::new();
    for binding in bindings {
        let kind = match binding.key.dependency {
            Dependency::Trades => EventKind::Trade,
            Dependency::Quotes => EventKind::Quote,
            _ => {
                return Err(Error::Invalid(
                    "REST binding is not a market event source".into(),
                ))
            }
        };
        if binding.authority.instrument != binding.key.instrument || binding.authority.kind != kind
        {
            return Err(Error::Conflict(
                "source binding instrument or channel differs".into(),
            ));
        }
        if registry.insert(binding.key.clone(), binding).is_some() {
            return Err(Error::Conflict("duplicate source binding".into()));
        }
    }
    let mut report = Report {
        dependency_plan_hash: dependencies.id()?,
        checked_at_ns: as_of_ns,
        jobs: vec![],
        unresolved_derived: vec![],
    };
    for node in &dependencies.nodes {
        if !matches!(node.key.dependency, Dependency::Trades | Dependency::Quotes) {
            report.unresolved_derived.push(node.key.clone());
            continue;
        }
        let binding = registry
            .remove(&node.key)
            .ok_or_else(|| Error::Unready("source binding missing".into()))?;
        if binding.implementation_hash != node.implementation_hash {
            return Err(Error::Conflict(
                "source implementation differs from dependency plan".into(),
            ));
        }
        for interval in &node.intervals {
            // Validate even a fully covered binding through the real acquisition contract.
            let template = Job {
                name: "startup-validation".into(),
                symbol: binding.symbol.clone(),
                authority: binding.authority.clone(),
                interval: *interval,
                maximum_pages: limits.maximum_pages,
                recovery_bytes: limits.recovery_bytes,
            };
            template.ownership_key()?;
            if interval.end > as_of_ns {
                return Err(Error::Invalid(
                    "historical repair extends beyond observation clock".into(),
                ));
            }
            for missing in catalog.missing(&binding.authority, *interval, as_of_ns)? {
                if report.jobs.len() == limits.maximum_jobs {
                    return Err(Error::Capacity("startup repair job budget".into()));
                }
                let id = content_hash(&(
                    "startup-repair-v1",
                    &report.dependency_plan_hash,
                    &node.key,
                    &binding.authority,
                    &binding.symbol,
                    missing,
                ))?;
                let job = Job {
                    name: format!("startup-{id}"),
                    interval: missing,
                    ..template.clone()
                };
                job.ownership_key()?;
                report.jobs.push(job);
            }
        }
    }
    if !registry.is_empty() {
        return Err(Error::Invalid(
            "unused source binding outside dependency plan".into(),
        ));
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{
        acquisition::{Certificate, Page, Verifier},
        coverage::Interval,
        dependency_plan::{self, Definition, Input, Request},
        execution_interval::ExecutionInterval,
    };
    fn fixture() -> (Plan, Binding, Catalog, Limits) {
        let plan = dependency_plan::build(
            vec![
                Definition {
                    dependency: Dependency::Trades,
                    implementation_hash: "a".repeat(64),
                    execution_interval: ExecutionInterval::Events,
                    inputs: vec![],
                },
                Definition {
                    dependency: Dependency::Bars(60),
                    implementation_hash: "b".repeat(64),
                    execution_interval: ExecutionInterval::Fixed(100_000_000),
                    inputs: vec![Input {
                        dependency: Dependency::Trades,
                        extra_history_ns: 10,
                    }],
                },
            ],
            &[Request {
                strategy_instance: "s".into(),
                instrument: 1,
                dependency: Dependency::Bars(60),
                interval: Interval {
                    start: 100,
                    end: 200,
                },
            }],
            2,
        )
        .unwrap();
        let authority = Authority {
            provider: 1,
            instrument: 1,
            kind: EventKind::Trade,
            source_revision: "revision".into(),
            contract_hash: "c".repeat(64),
            capabilities_hash: "d".repeat(64),
        };
        let mut catalog = Catalog::new(10).unwrap();
        let certificate = Certificate {
            schema_version: 1,
            authority: authority.clone(),
            interval: Interval {
                start: 120,
                end: 160,
            },
            first_request_hash: "e".repeat(64),
            published_at_ns: 300,
            pages: vec![Page {
                request_hash: "e".repeat(64),
                response_hash: "f".repeat(64),
                next_request_hash: None,
                acquired_at_ns: 250,
                source_rows: 0,
                accepted_rows: 0,
                rejected_rows: 0,
                deduplicated_rows: 0,
                batches: vec![],
                identity_checked: true,
                ordering_checked: true,
                interval_checked: true,
            }],
        };
        catalog
            .publish(Verifier::new(certificate).unwrap().finish().unwrap())
            .unwrap();
        (
            plan,
            Binding {
                key: Key {
                    instrument: 1,
                    dependency: Dependency::Trades,
                },
                implementation_hash: "a".repeat(64),
                authority,
                symbol: "TEST".into(),
            },
            catalog,
            Limits {
                maximum_jobs: 10,
                maximum_pages: 10,
                recovery_bytes: 1024 * 1024,
            },
        )
    }
    #[test]
    fn verified_coverage_produces_only_missing_resumable_source_jobs() {
        let (dependencies, binding, catalog, limits) = fixture();
        let report = plan(&dependencies, vec![binding], &catalog, 300, &limits).unwrap();
        assert_eq!(
            report.jobs.iter().map(|j| j.interval).collect::<Vec<_>>(),
            vec![
                Interval {
                    start: 90,
                    end: 120
                },
                Interval {
                    start: 160,
                    end: 200
                }
            ]
        );
        assert_eq!(
            report.unresolved_derived,
            vec![Key {
                instrument: 1,
                dependency: Dependency::Bars(60)
            }]
        );
        let (dependencies, binding, catalog, limits) = fixture();
        let retry = plan(&dependencies, vec![binding], &catalog, 301, &limits).unwrap();
        assert_eq!(
            report.jobs[0].ownership_key().unwrap(),
            retry.jobs[0].ownership_key().unwrap()
        );
    }
    #[test]
    fn future_or_other_revision_coverage_cannot_hide_repair() {
        for other_revision in [false, true] {
            let (dependencies, mut binding, catalog, limits) = fixture();
            if other_revision {
                binding.authority.source_revision = "new-revision".into();
            }
            let report = plan(
                &dependencies,
                vec![binding],
                &catalog,
                if other_revision { 300 } else { 299 },
                &limits,
            )
            .unwrap();
            assert_eq!(report.jobs.len(), 1);
            assert_eq!(
                report.jobs[0].interval,
                Interval {
                    start: 90,
                    end: 200
                }
            );
        }
    }
    #[test]
    fn invalid_binding_or_budget_rejects_the_whole_plan() {
        let (dependencies, mut binding, catalog, limits) = fixture();
        binding.implementation_hash = "f".repeat(64);
        assert!(matches!(
            plan(&dependencies, vec![binding], &catalog, 300, &limits),
            Err(Error::Conflict(_))
        ));
        let (dependencies, binding, catalog, mut limits) = fixture();
        limits.maximum_jobs = 1;
        assert!(matches!(
            plan(&dependencies, vec![binding], &catalog, 300, &limits),
            Err(Error::Capacity(_))
        ));
        let (dependencies, binding, catalog, limits) = fixture();
        assert!(plan(&dependencies, vec![binding], &catalog, 199, &limits).is_err());
        assert!(plan(&dependencies, vec![], &catalog, 300, &limits).is_err());
    }
}
