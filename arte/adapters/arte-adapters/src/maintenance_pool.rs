//! Bounded concurrent job admission. Workers own their leases and durable recovery.
use crate::maintenance::{Job, Phase, Status};
use arte_core::{Error, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeSet, HashMap, VecDeque},
    future::Future,
};
use tokio::{sync::watch, task::JoinSet};
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Limits {
    pub workers: usize,
    pub maximum_jobs: usize,
    pub estimated_worker_bytes: u64,
    pub memory_budget_bytes: u64,
    pub stop_on_failure: bool,
}
impl Limits {
    fn validate(&self, count: usize) -> Result<()> {
        if self.workers == 0
            || self.maximum_jobs == 0
            || self.maximum_jobs > 4096
            || self.workers > self.maximum_jobs
            || count > self.maximum_jobs
            || self.estimated_worker_bytes == 0
            || self.memory_budget_bytes == 0
            || (self.workers as u64)
                .checked_mul(self.estimated_worker_bytes)
                .is_none_or(|n| n > self.memory_budget_bytes)
        {
            return Err(Error::Capacity(
                "maintenance admission exceeds worker/job/memory plan".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Progress {
    pub active: usize,
    pub queued: usize,
    pub completed: usize,
    pub failed: usize,
    pub stopped: usize,
    pub not_started: usize,
}
#[derive(Debug)]
pub enum Outcome {
    Complete(Status),
    Stopped(Status),
    Failed(Error),
    NotStarted,
}
#[derive(Debug)]
pub struct JobResult {
    pub job_name: String,
    pub job_key: String,
    pub outcome: Outcome,
}
#[derive(Debug)]
pub struct Report {
    pub progress: Progress,
    pub jobs: Vec<JobResult>,
}
fn classify(result: Result<Status>) -> Outcome {
    match result {
        Ok(status) if status.phase == Phase::Complete => {
            if status.completed_pages == 0
                || status.coverage_id.as_deref().is_none_or(|id| {
                    id.len() != 64
                        || !id
                            .bytes()
                            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                })
            {
                Outcome::Failed(Error::Invalid(
                    "worker completion has no valid coverage identity".into(),
                ))
            } else {
                Outcome::Complete(status)
            }
        }
        Ok(status) if status.coverage_id.is_none() => Outcome::Stopped(status),
        Ok(_) => Outcome::Failed(Error::Invalid("unfinished worker reported coverage".into())),
        Err(error) => Outcome::Failed(error),
    }
}
/// Work only constructs an owned future; it must acquire its job lease before I/O.
/// Stop halts admission and signals running workers, then joins them. Workers must
/// cooperate at safe checkpoints. Dropping this entire future aborts tasks and
/// requires durable-head recovery, not reuse of volatile state.
pub async fn execute<F, Fut>(
    jobs: Vec<Job>,
    limits: Limits,
    mut stop: watch::Receiver<bool>,
    progress: watch::Sender<Progress>,
    mut work: F,
) -> Result<Report>
where
    F: FnMut(Job, watch::Receiver<bool>) -> Fut,
    Fut: Future<Output = Result<Status>> + Send + 'static,
{
    limits.validate(jobs.len())?;
    let mut unique = BTreeSet::new();
    let mut result = Vec::with_capacity(jobs.len());
    for job in &jobs {
        let key = job.ownership_key()?;
        if !unique.insert(key.clone()) {
            return Err(Error::Conflict(
                "duplicate maintenance job in one campaign".into(),
            ));
        }
        result.push(JobResult {
            job_name: job.name.clone(),
            job_key: key,
            outcome: Outcome::NotStarted,
        });
    }
    let mut queued: VecDeque<_> = jobs.into_iter().enumerate().collect();
    let mut state = Progress {
        queued: queued.len(),
        ..Progress::default()
    };
    let mut tasks = JoinSet::new();
    let mut active = HashMap::new();
    let (worker_stop, worker_stopping) = watch::channel(false);
    let mut closed = *stop.borrow();
    if closed {
        worker_stop.send_replace(true);
    }
    loop {
        if *stop.borrow() {
            closed = true;
            worker_stop.send_replace(true);
        }
        while !closed && tasks.len() < limits.workers {
            if *stop.borrow() {
                closed = true;
                worker_stop.send_replace(true);
                break;
            }
            let Some((index, job)) = queued.pop_front() else {
                break;
            };
            let future = work(job, worker_stopping.clone());
            let task = tasks.spawn(async move { (index, future.await) });
            active.insert(task.id(), index);
            state.active += 1;
            state.queued -= 1;
        }
        progress.send_replace(state.clone());
        if tasks.is_empty() {
            break;
        }
        tokio::select! {
            changed=stop.changed(), if !closed=> {
                if changed.is_err() || *stop.borrow() {closed=true;worker_stop.send_replace(true);}
            }
            joined=tasks.join_next_with_id()=> {
                let (index,outcome)=match joined.ok_or_else(||Error::Unready("maintenance task ledger empty".into()))? {
                    Ok((id,(index,output)))=> {
                        if active.remove(&id)!=Some(index) {return Err(Error::Conflict("maintenance task ownership mismatch".into()));}
                        (index,classify(output))
                    }
                    Err(error)=> {
                        let index=active.remove(&error.id()).ok_or_else(||Error::Conflict("unknown failed maintenance task".into()))?;
                        (index,Outcome::Failed(Error::Unready("maintenance worker panicked or was cancelled; recover durable head".into())))
                    }
                };
                state.active-=1;
                match &outcome {
                    Outcome::Complete(_)=>state.completed+=1,
                    Outcome::Stopped(_)=>state.stopped+=1,
                    Outcome::Failed(_)=> {state.failed+=1;if limits.stop_on_failure {closed=true;worker_stop.send_replace(true);}}
                    Outcome::NotStarted=>unreachable!(),
                }
                result[index].outcome=outcome;
            }
        }
    }
    state.not_started = queued.len();
    state.queued = 0;
    progress.send_replace(state.clone());
    Ok(Report {
        progress: state,
        jobs: result,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{acquisition::Authority, coverage::Interval, events::EventKind};
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };
    fn job(i: usize) -> Job {
        Job {
            name: format!("job_{i}"),
            symbol: "AAPL".into(),
            authority: Authority {
                provider: 1,
                instrument: 1,
                kind: EventKind::Trade,
                source_revision: "r".into(),
                contract_hash: "a".repeat(64),
                capabilities_hash: "b".repeat(64),
            },
            interval: Interval { start: 1, end: 2 },
            maximum_pages: 1,
            recovery_bytes: 1000,
        }
    }
    fn limits(workers: usize) -> Limits {
        Limits {
            workers,
            maximum_jobs: 10,
            estimated_worker_bytes: 100,
            memory_budget_bytes: 1000,
            stop_on_failure: false,
        }
    }
    fn complete() -> Status {
        Status {
            phase: Phase::Complete,
            completed_pages: 1,
            progress_head: None,
            coverage_id: Some("a".repeat(64)),
        }
    }
    #[tokio::test]
    async fn concurrency_bound_and_every_job_accounted_for() {
        let active = Arc::new(AtomicUsize::new(0));
        let maximum = Arc::new(AtomicUsize::new(0));
        let a = active.clone();
        let m = maximum.clone();
        let (_stop, stopping) = watch::channel(false);
        let (progress, _) = watch::channel(Progress::default());
        let report = execute(
            (0..6).map(job).collect(),
            limits(2),
            stopping,
            progress,
            move |_, _| {
                let a = a.clone();
                let m = m.clone();
                async move {
                    let n = a.fetch_add(1, Ordering::SeqCst) + 1;
                    m.fetch_max(n, Ordering::SeqCst);
                    tokio::task::yield_now().await;
                    a.fetch_sub(1, Ordering::SeqCst);
                    Ok(complete())
                }
            },
        )
        .await
        .unwrap();
        assert_eq!(report.progress.completed, 6);
        assert_eq!(report.progress.active, 0);
        assert_eq!(active.load(Ordering::SeqCst), 0);
        assert!(maximum.load(Ordering::SeqCst) <= 2);
        assert!(maximum.load(Ordering::SeqCst) > 1);
    }
    #[tokio::test]
    async fn failure_stops_admission_without_hiding_unstarted_jobs() {
        let (_stop, stopping) = watch::channel(false);
        let (progress, _) = watch::channel(Progress::default());
        let mut policy = limits(1);
        policy.stop_on_failure = true;
        let report = execute(
            vec![job(0), job(1)],
            policy,
            stopping,
            progress,
            |_, _| async { Err(Error::Unready("injected failure".into())) },
        )
        .await
        .unwrap();
        assert_eq!(report.progress.failed, 1);
        assert_eq!(report.progress.not_started, 1);
        assert!(matches!(report.jobs[1].outcome, Outcome::NotStarted));
    }
    #[tokio::test]
    async fn duplicate_admission_fails_before_work_is_constructed() {
        let (_stop, stopping) = watch::channel(false);
        let (progress, _) = watch::channel(Progress::default());
        assert!(execute(
            vec![job(0), job(0)],
            limits(1),
            stopping,
            progress,
            |_, _| {
                panic!("must not dispatch");
                #[allow(unreachable_code)]
                async {
                    Ok(complete())
                }
            }
        )
        .await
        .is_err());
    }
    #[tokio::test]
    async fn worker_panic_is_reported_and_other_workers_are_joined() {
        let (_stop, stopping) = watch::channel(false);
        let (progress, _) = watch::channel(Progress::default());
        let report = execute(
            vec![job(0), job(1)],
            limits(2),
            stopping,
            progress,
            |job, _| async move {
                assert_ne!(job.name, "job_0", "offline injected worker panic");
                Ok(complete())
            },
        )
        .await
        .unwrap();
        assert_eq!(report.progress.failed, 1);
        assert_eq!(report.progress.completed, 1);
        assert_eq!(report.progress.active, 0);
        assert!(matches!(report.jobs[0].outcome, Outcome::Failed(_)));
    }
    #[tokio::test]
    async fn already_stopped_campaign_never_dispatches() {
        let (_stop, stopping) = watch::channel(true);
        let (progress, _) = watch::channel(Progress::default());
        let report = execute(vec![job(0)], limits(1), stopping, progress, |_, _| async {
            panic!("stopped work must not execute")
        })
        .await
        .unwrap();
        assert_eq!(report.progress.not_started, 1);
        assert_eq!(report.progress.active, 0);
    }
}
