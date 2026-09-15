//! Resumable single-job orchestration. No task, connection or service starts here.
use crate::{
    clickhouse::ClickHouse,
    event_writer::Publisher,
    rest_acquisition::{Acquisition, Fetcher},
};
use arte_core::{
    acquisition::{Authority, Certificate},
    config::Acceptance,
    coverage::Interval,
    event_storage::Batch,
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, future::Future};
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Job {
    pub name: String,
    pub symbol: String,
    pub authority: Authority,
    pub interval: Interval,
    pub maximum_pages: usize,
    pub recovery_bytes: usize,
}
impl Job {
    pub fn ownership_key(&self) -> Result<String> {
        crate::ownership::job_hash(&self.name, &self.acquisition()?.plan_hash()?)
    }
    fn acquisition(&self) -> Result<Acquisition> {
        if self.name.is_empty()
            || self.name.len() > 128
            || !self
                .name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
            || self.recovery_bytes == 0
            || self.recovery_bytes > 1024 * 1024 * 1024
        {
            return Err(Error::Invalid(
                "invalid maintenance job or recovery budget".into(),
            ));
        }
        Acquisition::new(
            self.authority.clone(),
            self.interval,
            &self.symbol,
            self.maximum_pages,
        )
    }
}
pub trait Backend: Publisher {
    fn recover(
        &mut self,
        job: &Job,
        acquisition: Acquisition,
    ) -> impl Future<Output = Result<Acquisition>> + Send;
    fn checkpoint(
        &mut self,
        job: &Job,
        acquisition: &mut Acquisition,
    ) -> impl Future<Output = Result<String>> + Send;
    fn coverage(
        &mut self,
        certificate: Certificate,
        now_ns: u64,
    ) -> impl Future<Output = Result<String>> + Send;
}
pub struct DatabaseBackend<'a> {
    database: &'a ClickHouse,
    passed: &'a BTreeSet<Acceptance>,
    lease: &'a mut crate::ownership::Lease,
    scope: String,
    authority: Authority,
    interval: Interval,
}
impl<'a> DatabaseBackend<'a> {
    pub fn new(
        database: &'a ClickHouse,
        passed: &'a BTreeSet<Acceptance>,
        lease: &'a mut crate::ownership::Lease,
        job: &Job,
    ) -> Result<Self> {
        let scope = job.ownership_key()?;
        lease.require(&scope)?;
        Ok(Self {
            database,
            passed,
            lease,
            scope,
            authority: job.authority.clone(),
            interval: job.interval,
        })
    }
    fn require_job(&self, job: &Job) -> Result<()> {
        if job.ownership_key()? != self.scope {
            return Err(Error::Conflict(
                "maintenance backend belongs to another job".into(),
            ));
        }
        self.lease.require(&self.scope)
    }
    fn require_acquisition(&self, job: &Job, acquisition: &Acquisition) -> Result<()> {
        self.require_job(job)?;
        if acquisition.plan_hash()? != job.acquisition()?.plan_hash()? {
            return Err(Error::Conflict(
                "acquisition differs from owned maintenance plan".into(),
            ));
        }
        Ok(())
    }
}
impl Publisher for DatabaseBackend<'_> {
    async fn publish(&mut self, batch: &Batch) -> Result<String> {
        self.lease.require(&self.scope)?;
        if batch.observations().iter().any(|o| {
            o.key.provider != self.authority.provider
                || o.key.instrument != self.authority.instrument
                || o.key.kind != self.authority.kind
                || o.sip.ns < self.interval.start
                || o.sip.ns >= self.interval.end
                || o.receipt.is_some()
        }) {
            return Err(Error::Conflict(
                "maintenance batch outside owned job".into(),
            ));
        }
        self.database.publish_event_batch(batch, self.passed).await
    }
}
impl Backend for DatabaseBackend<'_> {
    async fn recover(&mut self, job: &Job, acquisition: Acquisition) -> Result<Acquisition> {
        self.require_acquisition(job, &acquisition)?;
        self.database
            .recover_acquisition_job(
                acquisition,
                &job.name,
                job.maximum_pages,
                job.recovery_bytes,
            )
            .await
    }
    async fn checkpoint(&mut self, job: &Job, acquisition: &mut Acquisition) -> Result<String> {
        self.require_acquisition(job, acquisition)?;
        self.database
            .checkpoint_acquisition(acquisition, &job.name, self.passed)
            .await
    }
    async fn coverage(&mut self, certificate: Certificate, now_ns: u64) -> Result<String> {
        self.lease.require(&self.scope)?;
        if certificate.authority != self.authority || certificate.interval != self.interval {
            return Err(Error::Conflict(
                "coverage outside owned maintenance job".into(),
            ));
        }
        self.database
            .publish_acquisition(certificate, self.passed, now_ns)
            .await
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Phase {
    Recovering,
    Acquiring,
    Checkpointing,
    Verifying,
    Complete,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Status {
    pub phase: Phase,
    pub completed_pages: usize,
    pub progress_head: Option<String>,
    pub coverage_id: Option<String>,
}
pub struct Runner {
    job: Job,
    acquisition: Option<Acquisition>,
    pending_coverage: Option<Certificate>,
    status: Status,
}
impl Runner {
    pub fn new(job: Job) -> Result<Self> {
        job.acquisition()?;
        Ok(Self {
            job,
            acquisition: None,
            pending_coverage: None,
            status: Status {
                phase: Phase::Recovering,
                completed_pages: 0,
                progress_head: None,
                coverage_id: None,
            },
        })
    }
    pub fn status(&self) -> &Status {
        &self.status
    }
    /// Caller must hold exclusive fenced job ownership. Errors preserve in-process
    /// pending work. Retry scheduling is explicit; this method never blindly retries.
    /// notify must be bounded/nonblocking. Only coverage publication completes a job.
    pub async fn advance(
        &mut self,
        fetcher: &mut impl Fetcher,
        backend: &mut impl Backend,
        mut now: impl FnMut() -> Result<u64>,
        mut notify: impl FnMut(&Status),
    ) -> Result<bool> {
        if self.status.phase == Phase::Complete {
            return Ok(false);
        }
        if self.acquisition.is_none() {
            notify(&self.status);
            self.acquisition = Some(backend.recover(&self.job, self.job.acquisition()?).await?);
            self.status.completed_pages = self.acquisition.as_ref().unwrap().completed_pages();
        }
        let acquisition = self.acquisition.as_mut().unwrap();
        if acquisition.progress_record().is_some() {
            self.status.phase = Phase::Checkpointing;
            notify(&self.status);
            let expected = acquisition.progress_record().unwrap().id()?;
            let acknowledged = backend.checkpoint(&self.job, acquisition).await?;
            if acknowledged != expected || acquisition.progress_record().is_some() {
                return Err(Error::Conflict(
                    "maintenance checkpoint acknowledgment differs".into(),
                ));
            }
            self.status.progress_head = Some(acknowledged);
            self.status.phase = Phase::Acquiring;
            notify(&self.status);
            return Ok(true);
        }
        self.status.phase = Phase::Acquiring;
        notify(&self.status);
        if acquisition.step(fetcher, backend).await? {
            self.status.completed_pages = acquisition.completed_pages();
            self.status.phase = Phase::Checkpointing;
            notify(&self.status);
            return Ok(true);
        }
        self.status.phase = Phase::Verifying;
        notify(&self.status);
        if self.pending_coverage.is_none() {
            self.pending_coverage = Some(acquisition.certificate(now()?)?);
        }
        let certificate = self.pending_coverage.as_ref().unwrap().clone();
        let at = certificate.published_at_ns;
        let expected = certificate.id()?;
        let id = backend.coverage(certificate, at).await?;
        if id != expected {
            return Err(Error::Conflict(
                "maintenance coverage acknowledgment differs".into(),
            ));
        }
        self.status.coverage_id = Some(id);
        self.pending_coverage = None;
        self.status.phase = Phase::Complete;
        notify(&self.status);
        Ok(false)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::massive::FetchedPage;
    use arte_core::events::EventKind;
    use std::collections::BTreeMap;
    struct Source {
        calls: usize,
    }
    impl Fetcher for Source {
        async fn fetch(&mut self, url: &str, _: &str) -> Result<FetchedPage> {
            self.calls += 1;
            Ok(FetchedPage {
                rows: vec![
                    serde_json::json!({"price":10,"size":1,"exchange":1,"id":"t","sequence_number":1,"sip_timestamp":15}),
                ],
                next_url: None,
                request_hash: arte_core::content_hash(&url)?,
                response_hash: "b".repeat(64),
                acquired_at_ns: 30,
            })
        }
    }
    struct Store {
        batches: BTreeMap<String, Batch>,
        fail_checkpoint: bool,
        fail_coverage: bool,
        checkpoints: usize,
        coverage: usize,
    }
    impl Publisher for Store {
        async fn publish(&mut self, batch: &Batch) -> Result<String> {
            let id = batch.id()?;
            self.batches.insert(id.clone(), batch.clone());
            Ok(id)
        }
    }
    impl Backend for Store {
        async fn recover(&mut self, _: &Job, acquisition: Acquisition) -> Result<Acquisition> {
            Ok(acquisition)
        }
        async fn checkpoint(&mut self, _: &Job, acquisition: &mut Acquisition) -> Result<String> {
            self.checkpoints += 1;
            if self.fail_checkpoint {
                self.fail_checkpoint = false;
                return Err(Error::Unready("checkpoint interrupted".into()));
            }
            let id = acquisition.progress_record().unwrap().id()?;
            acquisition.acknowledge_progress(&id)?;
            Ok(id)
        }
        async fn coverage(&mut self, certificate: Certificate, _: u64) -> Result<String> {
            let id = certificate.id()?;
            let mut verifier = arte_core::acquisition::Verifier::new(certificate)?;
            while let Some(id) = verifier.next_batch() {
                verifier.observe(&self.batches[id])?;
            }
            verifier.finish()?;
            if self.fail_coverage {
                self.fail_coverage = false;
                return Err(Error::Unready("coverage publication ambiguous".into()));
            }
            self.coverage += 1;
            Ok(id)
        }
    }
    #[tokio::test]
    async fn runner_orders_checkpoint_retry_before_coverage_without_refetching() {
        let job = Job {
            name: "job".into(),
            symbol: "AAPL".into(),
            authority: Authority {
                provider: 1,
                instrument: 1,
                kind: EventKind::Trade,
                source_revision: "r".into(),
                contract_hash: "c".repeat(64),
                capabilities_hash: "d".repeat(64),
            },
            interval: Interval { start: 10, end: 20 },
            maximum_pages: 4,
            recovery_bytes: 1024 * 1024,
        };
        let mut runner = Runner::new(job).unwrap();
        let mut source = Source { calls: 0 };
        let mut store = Store {
            batches: Default::default(),
            fail_checkpoint: true,
            fail_coverage: true,
            checkpoints: 0,
            coverage: 0,
        };
        let mut phases = Vec::new();
        assert!(runner
            .advance(&mut source, &mut store, || Ok(40), |s| phases.push(s.phase))
            .await
            .unwrap());
        assert_eq!(runner.status().phase, Phase::Checkpointing);
        assert!(runner
            .advance(&mut source, &mut store, || Ok(40), |_| {})
            .await
            .is_err());
        assert_eq!(store.coverage, 0);
        assert_eq!(source.calls, 1);
        assert!(runner
            .advance(&mut source, &mut store, || Ok(40), |_| {})
            .await
            .unwrap());
        assert!(runner
            .advance(&mut source, &mut store, || Ok(40), |_| {})
            .await
            .is_err());
        assert!(!runner
            .advance(
                &mut source,
                &mut store,
                || panic!("coverage time must remain pinned"),
                |s| phases.push(s.phase)
            )
            .await
            .unwrap());
        assert_eq!(runner.status().phase, Phase::Complete);
        assert_eq!(source.calls, 1);
        assert_eq!(store.coverage, 1);
        assert!(phases.contains(&Phase::Verifying));
        assert!(!runner
            .advance(
                &mut source,
                &mut store,
                || panic!("already complete"),
                |_| {}
            )
            .await
            .unwrap());
    }
}
