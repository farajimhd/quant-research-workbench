//! Source-only startup orchestration. No derived readiness or trading authority.
use crate::{
    maintenance::{Job, Status},
    maintenance_pool::{self, Outcome, Progress},
    startup_repair::{self, Binding},
};
use arte_core::{
    acquisition::{Catalog, VerifiedCertificate},
    dependency_plan::Plan,
    Error, Result,
};
use std::future::Future;
use tokio::sync::watch;

pub struct Request<'a> {
    pub dependencies: &'a Plan,
    pub bindings: Vec<Binding>,
    pub catalog: &'a mut Catalog,
    pub checked_at_ns: u64,
    pub repair_limits: startup_repair::Limits,
    pub worker_limits: maintenance_pool::Limits,
}
pub struct Report {
    pub workers: maintenance_pool::Report,
    pub remaining: startup_repair::Report,
    pub verification_failures: Vec<(String, String)>,
}
impl Report {
    /// Only the source repair phase is complete. Derived checks remain mandatory.
    pub fn sources_complete(&self) -> bool {
        self.remaining.jobs.is_empty()
            && self.verification_failures.is_empty()
            && self
                .workers
                .jobs
                .iter()
                .all(|j| matches!(j.outcome, Outcome::Complete(_)))
    }
}
/// The caller supplies the real maintenance worker and durable certificate loader.
/// Loading must return a certificate verified against persisted event batches.
/// Worker failures remain in the report. No automatic retry or readiness promotion.
pub async fn execute<W, WF, L, LF, C>(
    request: Request<'_>,
    stop: watch::Receiver<bool>,
    progress: watch::Sender<Progress>,
    work: W,
    mut load: L,
    mut clock: C,
) -> Result<Report>
where
    W: FnMut(Job, watch::Receiver<bool>) -> WF,
    WF: Future<Output = Result<Status>> + Send + 'static,
    L: FnMut(String) -> LF,
    LF: Future<Output = Result<VerifiedCertificate>>,
    C: FnMut() -> Result<u64>,
{
    let initial = startup_repair::plan(
        request.dependencies,
        request.bindings.clone(),
        request.catalog,
        request.checked_at_ns,
        &request.repair_limits,
    )?;
    let expected: std::collections::BTreeMap<_, _> = initial
        .jobs
        .iter()
        .map(|j| Ok((j.ownership_key()?, j.clone())))
        .collect::<Result<_>>()?;
    let workers =
        maintenance_pool::execute(initial.jobs, request.worker_limits, stop, progress, work)
            .await?;
    let mut verification_failures = vec![];
    let mut checked_at = request.checked_at_ns;
    for row in &workers.jobs {
        if let Outcome::Complete(status) = &row.outcome {
            let result = async {
                let id = status
                    .coverage_id
                    .as_ref()
                    .ok_or_else(|| Error::Unready("worker coverage identity missing".into()))?;
                let verified = load(id.clone()).await?;
                let now = clock()?;
                if now < checked_at {
                    return Err(Error::Invalid(
                        "startup verification clock regressed".into(),
                    ));
                }
                checked_at = now;
                let certificate = verified.certificate();
                let job = expected
                    .get(&row.job_key)
                    .ok_or_else(|| Error::Conflict("unexpected startup job completion".into()))?;
                if certificate.id()? != *id
                    || certificate.authority != job.authority
                    || certificate.interval != job.interval
                    || certificate.published_at_ns > now
                {
                    return Err(Error::Conflict(
                        "startup certificate differs from completed job".into(),
                    ));
                }
                request.catalog.publish(verified)?;
                Ok(())
            }
            .await;
            if let Err(error) = result {
                verification_failures.push((row.job_key.clone(), error.to_string()));
            }
        }
    }
    let remaining = startup_repair::plan(
        request.dependencies,
        request.bindings,
        request.catalog,
        checked_at,
        &request.repair_limits,
    )?;
    Ok(Report {
        workers,
        remaining,
        verification_failures,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::maintenance::Phase;
    use arte_core::{
        acquisition::{Authority, Certificate, Page, Verifier},
        coverage::{Dependency, Interval},
        dependency_plan::{self, Definition, Key},
        events::EventKind,
    };

    #[tokio::test]
    async fn completion_requires_verified_matching_durable_certificate() {
        for wrong_authority in [false, true] {
            let authority = Authority {
                provider: 1,
                instrument: 1,
                kind: EventKind::Trade,
                source_revision: "v1".into(),
                contract_hash: "a".repeat(64),
                capabilities_hash: "b".repeat(64),
            };
            let interval = Interval { start: 10, end: 20 };
            let dependencies = dependency_plan::build(
                vec![Definition {
                    dependency: Dependency::Trades,
                    implementation_hash: "a".repeat(64),
                    inputs: vec![],
                }],
                &[dependency_plan::Request {
                    strategy_instance: "s".into(),
                    instrument: 1,
                    dependency: Dependency::Trades,
                    interval,
                }],
                1,
            )
            .unwrap();
            let mut certificate = Certificate {
                schema_version: 1,
                authority: authority.clone(),
                interval,
                first_request_hash: "c".repeat(64),
                published_at_ns: 40,
                pages: vec![Page {
                    request_hash: "c".repeat(64),
                    response_hash: "d".repeat(64),
                    next_request_hash: None,
                    acquired_at_ns: 35,
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
            if wrong_authority {
                certificate.authority.instrument = 2;
            }
            let id = certificate.id().unwrap();
            let mut catalog = Catalog::new(10).unwrap();
            let request = Request {
                dependencies: &dependencies,
                bindings: vec![Binding {
                    key: Key {
                        instrument: 1,
                        dependency: Dependency::Trades,
                    },
                    implementation_hash: "a".repeat(64),
                    authority,
                    symbol: "TEST".into(),
                }],
                catalog: &mut catalog,
                checked_at_ns: 30,
                repair_limits: startup_repair::Limits {
                    maximum_jobs: 10,
                    maximum_pages: 10,
                    recovery_bytes: 1024,
                },
                worker_limits: maintenance_pool::Limits {
                    workers: 1,
                    maximum_jobs: 10,
                    estimated_worker_bytes: 1024,
                    memory_budget_bytes: 1024,
                    stop_on_failure: true,
                },
            };
            let (_stop, stopping) = watch::channel(false);
            let (progress, _observer) = watch::channel(Progress::default());
            let report = execute(
                request,
                stopping,
                progress,
                move |_, _| {
                    let id = id.clone();
                    async move {
                        Ok(Status {
                            phase: Phase::Complete,
                            completed_pages: 1,
                            progress_head: None,
                            coverage_id: Some(id),
                        })
                    }
                },
                move |_| {
                    let certificate = certificate.clone();
                    async move { Verifier::new(certificate)?.finish() }
                },
                || Ok(50),
            )
            .await
            .unwrap();
            assert_eq!(report.sources_complete(), !wrong_authority);
            assert_eq!(
                report.verification_failures.len(),
                usize::from(wrong_authority)
            );
            assert_eq!(report.remaining.jobs.len(), usize::from(wrong_authority));
            assert_eq!(report.workers.progress.completed, 1);
        }
    }
}
