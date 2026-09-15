//! Serial publication ownership with bounded input and retry-stable pending data.
use crate::clickhouse::ClickHouse;
use arte_core::config::Acceptance;
use arte_core::event_storage::Batch;
use arte_core::{Error, Result};
use std::{collections::BTreeSet, future::Future, sync::Arc};
use tokio::sync::{mpsc, watch};

pub trait Publisher {
    fn publish(&mut self, batch: &Batch) -> impl Future<Output = Result<String>> + Send;
}
pub struct DatabasePublisher<'a> {
    pub database: &'a ClickHouse,
    /// Supervisor-verified release acceptance; never default to all passed.
    pub passed: &'a BTreeSet<Acceptance>,
}
impl Publisher for DatabasePublisher<'_> {
    async fn publish(&mut self, batch: &Batch) -> Result<String> {
        self.database.publish_event_batch(batch, self.passed).await
    }
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Progress {
    pub acknowledged_batches: u64,
    /// Batch acknowledgment is not interval/source coverage certification.
    pub last_acknowledged_batch: Option<String>,
    pub pending_batch: Option<String>,
    pub pending_attempts: u64,
}
#[derive(Default)]
pub struct Worker {
    pending: Option<Arc<Batch>>,
    acknowledged: u64,
    last_id: Option<String>,
    attempts: u64,
}
impl Worker {
    pub fn pending(&self) -> Option<&Arc<Batch>> {
        self.pending.as_ref()
    }
    pub fn progress(&self) -> Result<Progress> {
        Ok(Progress {
            acknowledged_batches: self.acknowledged,
            last_acknowledged_batch: self.last_id.clone(),
            pending_batch: self.pending.as_ref().map(|b| b.id()).transpose()?,
            pending_attempts: self.attempts,
        })
    }
    /// Never removes pending data until the publisher returns its exact batch ID.
    /// Dropping this future preserves pending data in the borrowed Worker. Process
    /// loss still requires a durable acquisition catalog; memory is not a WAL.
    pub async fn step(
        &mut self,
        publisher: &mut impl Publisher,
        input: &mut mpsc::Receiver<Arc<Batch>>,
    ) -> Result<bool> {
        self.step_notified(publisher, input, None).await
    }
    async fn step_notified(
        &mut self,
        publisher: &mut impl Publisher,
        input: &mut mpsc::Receiver<Arc<Batch>>,
        progress: Option<&watch::Sender<Progress>>,
    ) -> Result<bool> {
        if self.pending.is_none() {
            self.pending = input.recv().await;
            self.attempts = 0;
        }
        let Some(batch) = self.pending.as_ref() else {
            return Ok(false);
        };
        let id = batch.id()?;
        let next = self
            .acknowledged
            .checked_add(1)
            .ok_or_else(|| Error::Capacity("event acknowledgment counter overflow".into()))?;
        self.attempts = self
            .attempts
            .checked_add(1)
            .ok_or_else(|| Error::Capacity("event retry counter overflow".into()))?;
        if let Some(progress) = progress {
            progress.send_replace(self.progress()?);
        }
        let acknowledged = publisher.publish(batch).await?;
        if acknowledged != id {
            return Err(Error::Conflict(
                "event publisher acknowledged wrong batch".into(),
            ));
        }
        self.acknowledged = next;
        self.last_id = Some(id);
        self.pending = None;
        self.attempts = 0;
        Ok(true)
    }
    /// Stop preserves queued and pending data; graceful drain uses a closed input
    /// channel instead. Failure returns to the supervisor without blind retries.
    pub async fn run(
        &mut self,
        publisher: &mut impl Publisher,
        input: &mut mpsc::Receiver<Arc<Batch>>,
        mut stop: watch::Receiver<bool>,
        progress: watch::Sender<Progress>,
    ) -> Result<()> {
        loop {
            progress.send_replace(self.progress()?);
            if *stop.borrow() {
                return Ok(());
            }
            let result = tokio::select! {
                changed=stop.changed()=> {
                    if changed.is_err() || *stop.borrow() {
                        progress.send_replace(self.progress()?);
                        return Ok(());
                    }
                    continue;
                }
                result=self.step_notified(publisher,input,Some(&progress))=>result,
            };
            progress.send_replace(self.progress()?);
            if !result? {
                return Ok(());
            }
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::events::*;
    fn batch() -> Arc<Batch> {
        Arc::new(
            Batch::prepare(&[Observation {
                key: EventKey {
                    provider: 1,
                    instrument: 1,
                    session: 20260915,
                    kind: EventKind::Trade,
                    sequence: 1,
                },
                payload: Payload::Trade {
                    price: Decimal::parse("10").unwrap(),
                    size: Decimal::parse("1").unwrap(),
                    exchange: 1,
                    trade_id: "t".into(),
                    trf: None,
                    conditions: vec![],
                    correction: None,
                },
                sip: SourceTime {
                    ns: 1,
                    precision_ns: 1,
                },
                participant: None,
                available_at_ns: 2,
                receipt: None,
            }])
            .unwrap(),
        )
    }
    struct Fake {
        fail: bool,
        wrong: bool,
        ids: Vec<String>,
    }
    impl Publisher for Fake {
        async fn publish(&mut self, batch: &Batch) -> Result<String> {
            let id = batch.id()?;
            self.ids.push(id.clone());
            if self.fail {
                self.fail = false;
                return Err(Error::Unready("ambiguous write".into()));
            }
            Ok(if self.wrong { "wrong".into() } else { id })
        }
    }
    #[tokio::test]
    async fn ambiguous_publication_retries_exact_pending_before_next_batch() {
        let mut worker = Worker::default();
        let mut publisher = Fake {
            fail: true,
            wrong: false,
            ids: vec![],
        };
        let (send, mut input) = mpsc::channel(2);
        let b = batch();
        send.try_send(b.clone()).unwrap();
        send.try_send(b.clone()).unwrap();
        assert!(worker.step(&mut publisher, &mut input).await.is_err());
        assert_eq!(input.len(), 1);
        assert!(Arc::ptr_eq(worker.pending().unwrap(), &b));
        assert!(worker.step(&mut publisher, &mut input).await.unwrap());
        assert_eq!(publisher.ids[0], publisher.ids[1]);
        assert_eq!(input.len(), 1);
        assert_eq!(worker.progress().unwrap().acknowledged_batches, 1);
    }
    #[tokio::test]
    async fn wrong_acknowledgment_retains_pending() {
        let mut worker = Worker::default();
        let mut publisher = Fake {
            fail: false,
            wrong: true,
            ids: vec![],
        };
        let (send, mut input) = mpsc::channel(1);
        send.try_send(batch()).unwrap();
        assert!(worker.step(&mut publisher, &mut input).await.is_err());
        assert!(worker.pending().is_some());
        assert_eq!(worker.progress().unwrap().acknowledged_batches, 0);
    }
    struct Never;
    impl Publisher for Never {
        async fn publish(&mut self, _: &Batch) -> Result<String> {
            std::future::pending().await
        }
    }
    #[tokio::test]
    async fn cancelled_publication_future_keeps_owned_batch() {
        let mut worker = Worker::default();
        let mut publisher = Never;
        let (send, mut input) = mpsc::channel(1);
        send.try_send(batch()).unwrap();
        tokio::select! { biased;
            _=worker.step(&mut publisher,&mut input)=>panic!("publisher cannot finish"),
            _=async {}=>{},
        }
        assert!(worker.pending().is_some());
        assert_eq!(worker.progress().unwrap().pending_attempts, 1);
    }
    #[tokio::test]
    async fn in_flight_progress_is_visible_before_acknowledgment() {
        let mut worker = Worker::default();
        let mut publisher = Never;
        let (send, mut input) = mpsc::channel(1);
        send.try_send(batch()).unwrap();
        let (_stop, stopping) = watch::channel(false);
        let (progress, observe) = watch::channel(worker.progress().unwrap());
        tokio::select! {biased;
            _=worker.run(&mut publisher,&mut input,stopping,progress)=>panic!("publication cannot finish"),
            _=async {}=>{},
        }
        assert!(observe.borrow().pending_batch.is_some());
        assert_eq!(observe.borrow().acknowledged_batches, 0);
        assert_eq!(observe.borrow().pending_attempts, 1);
    }
}
