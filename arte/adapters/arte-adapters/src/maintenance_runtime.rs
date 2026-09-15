//! Real maintenance worker binding. Constructors never start tasks or connections.
use crate::{
    clickhouse::ClickHouse,
    maintenance::{DatabaseBackend, Job, Phase, Runner, Status},
    maintenance_pool::{self, Limits, Outcome, Progress, Report},
    massive::RestClient,
    ownership::Lease,
    request_governor::{Governor, Policy},
    stoppable_fetcher::StoppableFetcher,
};
use arte_core::{config::Acceptance, Error, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::PathBuf,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::sync::watch;
fn gate(passed: &BTreeSet<Acceptance>) -> Result<()> {
    for required in [
        Acceptance::RepositoryExtracted,
        Acceptance::SourceIdentity,
        Acceptance::EventStorage,
        Acceptance::Durability,
        Acceptance::ResourceBudgets,
    ] {
        if !passed.contains(&required) {
            return Err(Error::Unready(format!(
                "maintenance acceptance missing: {required:?}"
            )));
        }
    }
    Ok(())
}
/// No Debug/serialization: provider credentials never enter status or manifests.
pub struct Context {
    database: Arc<ClickHouse>,
    passed: BTreeSet<Acceptance>,
    provider_key: String,
    lock_directory: PathBuf,
    governor: Arc<Governor>,
}
pub struct PreparedSources {
    pub catalog: arte_core::acquisition::Catalog,
    pub repair: crate::startup_repair::Report,
    pub observers: Observers,
}
impl Context {
    pub fn new(
        database: Arc<ClickHouse>,
        passed: BTreeSet<Acceptance>,
        provider_key: String,
        lock_directory: PathBuf,
        rate_policy: Policy,
    ) -> Result<Self> {
        gate(&passed)?;
        if provider_key.is_empty() || !lock_directory.is_absolute() || !lock_directory.is_dir() {
            return Err(Error::Invalid(
                "maintenance credentials and approved lock directory required".into(),
            ));
        }
        Ok(Self {
            database,
            passed,
            provider_key,
            lock_directory,
            governor: Arc::new(Governor::new(rate_policy)?),
        })
    }
    pub async fn rate_status(&self) -> crate::request_governor::Status {
        self.governor.status().await
    }
    /// Read-only startup discovery. Every interval is revalidated against stored
    /// acquisition batches before any maintenance job is dispatched.
    pub async fn prepare_startup_sources(
        &self,
        dependencies: &arte_core::dependency_plan::Plan,
        bindings: Vec<crate::startup_repair::Binding>,
        checked_at_ns: u64,
        maximum_certificates: usize,
        limits: &crate::startup_repair::Limits,
        mut stop: watch::Receiver<bool>,
    ) -> Result<PreparedSources> {
        gate(&self.passed)?;
        if maximum_certificates == 0 || maximum_certificates > 4096 {
            return Err(Error::Capacity("startup coverage catalog budget".into()));
        }
        let mut catalog = arte_core::acquisition::Catalog::new(maximum_certificates)?;
        let required = crate::startup_repair::plan(
            dependencies,
            bindings.clone(),
            &catalog,
            checked_at_ns,
            limits,
        )?;
        for job in required.jobs {
            if *stop.borrow() || stop.has_changed().is_err() {
                return Err(Error::Unready("startup coverage discovery stopped".into()));
            }
            let discovery = self.database.discover_acquisitions(
                &job.authority,
                job.interval,
                checked_at_ns,
                maximum_certificates,
            );
            tokio::pin!(discovery);
            let found = loop {
                tokio::select! {
                    result = &mut discovery => break result?,
                    changed = stop.changed() => {
                        if changed.is_err() || *stop.borrow() {
                            return Err(Error::Unready("startup coverage discovery stopped".into()));
                        }
                    }
                }
            };
            catalog.merge(found)?;
        }
        if *stop.borrow() || stop.has_changed().is_err() {
            return Err(Error::Unready("startup coverage discovery stopped".into()));
        }
        let repair =
            crate::startup_repair::plan(dependencies, bindings, &catalog, checked_at_ns, limits)?;
        let observers = Observers::new(&repair.jobs)?;
        Ok(PreparedSources {
            catalog,
            repair,
            observers,
        })
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Terminal {
    Complete,
    Stopped,
    Failed(String),
    NotStarted,
    Interrupted,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct JobView {
    pub status: Option<Status>,
    pub terminal: Option<Terminal>,
}
pub struct Observers {
    pool_sender: watch::Sender<Progress>,
    job_senders: Arc<BTreeMap<String, watch::Sender<JobView>>>,
    pub pool: watch::Receiver<Progress>,
    pub jobs: BTreeMap<String, watch::Receiver<JobView>>,
}
impl Observers {
    pub fn new(jobs: &[Job]) -> Result<Self> {
        if jobs.len() > 4096 {
            return Err(Error::Capacity("maintenance observer job bound".into()));
        }
        let (pool_sender, pool) = watch::channel(Progress::default());
        let mut senders = BTreeMap::new();
        let mut receivers = BTreeMap::new();
        for job in jobs {
            let key = job.ownership_key()?;
            let (sender, receiver) = watch::channel(JobView::default());
            if senders.insert(key.clone(), sender).is_some() {
                return Err(Error::Conflict(
                    "duplicate maintenance observer identity".into(),
                ));
            }
            receivers.insert(key, receiver);
        }
        Ok(Self {
            pool_sender,
            job_senders: Arc::new(senders),
            pool,
            jobs: receivers,
        })
    }
}
struct ActiveView {
    sender: watch::Sender<JobView>,
    finished: bool,
}
impl Drop for ActiveView {
    fn drop(&mut self) {
        if !self.finished {
            self.sender
                .send_modify(|view| view.terminal = Some(Terminal::Interrupted));
        }
    }
}
fn now() -> Result<u64> {
    u64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| Error::Invalid("maintenance clock before epoch".into()))?
            .as_nanos(),
    )
    .map_err(|_| Error::Invalid("maintenance clock overflow".into()))
}
async fn run_job(
    context: Arc<Context>,
    job: Job,
    stopping: watch::Receiver<bool>,
    senders: Arc<BTreeMap<String, watch::Sender<JobView>>>,
) -> Result<Status> {
    let key = job.ownership_key()?;
    let sender = senders
        .get(&key)
        .ok_or_else(|| Error::Invalid("maintenance observer missing".into()))?
        .clone();
    let mut view = ActiveView {
        sender: sender.clone(),
        finished: false,
    };
    let result: Result<Status> = async {
        let mut runner = Runner::new(job.clone())?;
        sender.send_modify(|view| view.status = Some(runner.status().clone()));
        if *stopping.borrow() {
            return Ok(runner.status().clone());
        }
        let mut lease = Lease::acquire(&context.lock_directory, &key)?;
        let client = RestClient::new(
            context.provider_key.clone(),
            job.maximum_pages,
            context.governor.clone(),
        )?;
        let mut fetcher = StoppableFetcher::new(client, stopping.clone());
        let mut backend =
            DatabaseBackend::new(&context.database, &context.passed, &mut lease, &job)?;
        loop {
            // Finish a completed page's progress checkpoint before stopping.
            if *stopping.borrow() && runner.status().phase != Phase::Checkpointing {
                return Ok(runner.status().clone());
            }
            let advanced = runner
                .advance(&mut fetcher, &mut backend, now, |status| {
                    sender.send_modify(|view| view.status = Some(status.clone()))
                })
                .await;
            if fetcher.cancelled() {
                return Ok(runner.status().clone());
            }
            if !advanced? {
                return Ok(runner.status().clone());
            }
        }
    }
    .await;
    sender.send_modify(|view| {
        view.terminal = Some(match &result {
            Ok(status) if status.phase == Phase::Complete => Terminal::Complete,
            Ok(_) => Terminal::Stopped,
            Err(error) => Terminal::Failed(error.to_string()),
        })
    });
    view.finished = true;
    result
}

/// Must run only on the designated maintenance owner host. Local locks do not
/// replace cross-host fencing. Observer channels coalesce; they never authorize work.
pub async fn execute(
    context: Arc<Context>,
    jobs: Vec<Job>,
    limits: Limits,
    stop: watch::Receiver<bool>,
    observers: &Observers,
) -> Result<Report> {
    gate(&context.passed)?;
    let expected: BTreeSet<_> = jobs.iter().map(Job::ownership_key).collect::<Result<_>>()?;
    if expected != observers.job_senders.keys().cloned().collect() {
        return Err(Error::Invalid("maintenance observer plan differs".into()));
    }
    if jobs
        .iter()
        .any(|j| j.recovery_bytes as u64 > limits.estimated_worker_bytes)
    {
        return Err(Error::Capacity(
            "recovery budget exceeds estimated worker memory".into(),
        ));
    }
    let senders = observers.job_senders.clone();
    let report = maintenance_pool::execute(
        jobs,
        limits,
        stop,
        observers.pool_sender.clone(),
        move |job, stopping| {
            let context = context.clone();
            let senders = senders.clone();
            run_job(context, job, stopping, senders)
        },
    )
    .await?;
    for row in &report.jobs {
        if let Some(sender) = observers.job_senders.get(&row.job_key) {
            sender.send_modify(|view| {
                view.terminal = Some(match &row.outcome {
                    Outcome::Complete(_) => Terminal::Complete,
                    Outcome::Stopped(_) => Terminal::Stopped,
                    Outcome::Failed(error) => Terminal::Failed(error.to_string()),
                    Outcome::NotStarted => Terminal::NotStarted,
                })
            });
        }
    }
    Ok(report)
}
/// Production source-startup binding. Requires extraction acceptance before any I/O.
/// Observers must be prepared from the current startup_repair plan's jobs.
pub async fn execute_startup_sources(
    context: Arc<Context>,
    request: crate::startup_sources::Request<'_>,
    stop: watch::Receiver<bool>,
    observers: &Observers,
) -> Result<crate::startup_sources::Report> {
    gate(&context.passed)?;
    let initial = crate::startup_repair::plan(
        request.dependencies,
        request.bindings.clone(),
        request.catalog,
        request.checked_at_ns,
        &request.repair_limits,
    )?;
    let expected: BTreeSet<_> = initial
        .jobs
        .iter()
        .map(Job::ownership_key)
        .collect::<Result<_>>()?;
    if expected != observers.job_senders.keys().cloned().collect() {
        return Err(Error::Invalid(
            "startup maintenance observer plan differs".into(),
        ));
    }
    if initial
        .jobs
        .iter()
        .any(|j| j.recovery_bytes as u64 > request.worker_limits.estimated_worker_bytes)
    {
        return Err(Error::Capacity(
            "startup recovery exceeds worker memory budget".into(),
        ));
    }
    let senders = observers.job_senders.clone();
    let database = context.database.clone();
    let report = crate::startup_sources::execute(
        request,
        stop,
        observers.pool_sender.clone(),
        move |job, stopping| run_job(context.clone(), job, stopping, senders.clone()),
        move |id| {
            let database = database.clone();
            async move { database.load_acquisition(&id).await }
        },
        now,
    )
    .await?;
    for row in &report.workers.jobs {
        if let Some(sender) = observers.job_senders.get(&row.job_key) {
            sender.send_modify(|view| {
                view.terminal = Some(match &row.outcome {
                    Outcome::Complete(_) => Terminal::Complete,
                    Outcome::Stopped(_) => Terminal::Stopped,
                    Outcome::Failed(error) => Terminal::Failed(error.to_string()),
                    Outcome::NotStarted => Terminal::NotStarted,
                })
            });
        }
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn no_default_acceptance_can_start_maintenance() {
        let mut passed = BTreeSet::new();
        for required in [
            Acceptance::RepositoryExtracted,
            Acceptance::SourceIdentity,
            Acceptance::EventStorage,
            Acceptance::Durability,
            Acceptance::ResourceBudgets,
        ] {
            assert!(gate(&passed).is_err());
            passed.insert(required);
        }
        gate(&passed).unwrap();
    }
    #[tokio::test]
    async fn shared_worker_stops_before_lease_or_external_io() {
        let context = Arc::new(Context {
            database: Arc::new(
                ClickHouse::new(
                    "http://127.0.0.1:1",
                    "arte_test",
                    String::new(),
                    String::new(),
                )
                .unwrap(),
            ),
            passed: BTreeSet::new(),
            provider_key: String::new(),
            lock_directory: PathBuf::new(),
            governor: Arc::new(
                Governor::new(Policy {
                    maximum_inflight: 1,
                    minimum_interval_ms: 1,
                    default_cooldown_ms: 1000,
                    maximum_cooldown_ms: 1000,
                })
                .unwrap(),
            ),
        });
        let job = Job {
            name: "stopped".into(),
            symbol: "TEST".into(),
            authority: arte_core::acquisition::Authority {
                provider: 1,
                instrument: 1,
                kind: arte_core::events::EventKind::Trade,
                source_revision: "v1".into(),
                contract_hash: "a".repeat(64),
                capabilities_hash: "b".repeat(64),
            },
            interval: arte_core::coverage::Interval { start: 10, end: 20 },
            maximum_pages: 10,
            recovery_bytes: 1024,
        };
        let key = job.ownership_key().unwrap();
        let observers = Observers::new(std::slice::from_ref(&job)).unwrap();
        let (_stop, stopping) = watch::channel(true);
        let status = run_job(context, job, stopping, observers.job_senders.clone())
            .await
            .unwrap();
        assert_eq!(status.phase, Phase::Recovering);
        assert!(status.coverage_id.is_none());
        assert!(matches!(
            observers.jobs[&key].borrow().terminal,
            Some(Terminal::Stopped)
        ));
    }
    #[test]
    fn interrupted_worker_marks_latest_observer_without_waiting_for_consumer() {
        let (sender, receiver) = watch::channel(JobView::default());
        {
            let _active = ActiveView {
                sender: sender.clone(),
                finished: false,
            };
            sender.send_modify(|view| {
                view.status = Some(Status {
                    phase: Phase::Checkpointing,
                    completed_pages: 1,
                    progress_head: None,
                    coverage_id: None,
                })
            });
        }
        assert!(matches!(
            receiver.borrow().terminal,
            Some(Terminal::Interrupted)
        ));
        assert_eq!(
            receiver.borrow().status.as_ref().unwrap().completed_pages,
            1
        );
    }
}
