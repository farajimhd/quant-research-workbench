//! Bounded startup-only reference loading. No database access on cached reads.
use arte_core::{
    event_order::Scope,
    reference_data::{PreviousClose, PreviousCloseRequirement},
    Error, Result,
};
use futures_util::{stream, StreamExt};
use std::{
    collections::{BTreeMap, BTreeSet},
    future::Future,
};
use tokio::sync::watch;
pub mod plan;

#[derive(Clone)]
pub struct Request {
    pub scope: Scope,
    pub requirement: PreviousCloseRequirement,
}
pub trait Loader {
    fn load(
        &self,
        request: &Request,
        as_of_ns: u64,
    ) -> impl Future<Output = Result<PreviousClose>> + Send;
}
impl Loader for crate::clickhouse::ClickHouse {
    async fn load(&self, request: &Request, as_of_ns: u64) -> Result<PreviousClose> {
        self.load_previous_close(request.scope, &request.requirement, as_of_ns)
            .await
    }
}
pub struct Outcome {
    pub request: Request,
    pub result: Result<PreviousClose>,
}
pub struct Report {
    outcomes: Vec<Outcome>,
    as_of_ns: u64,
}
type Key = (u16, u64, u32);
fn key(scope: Scope) -> Key {
    (scope.provider, scope.instrument, scope.session)
}
pub struct Cache {
    records: BTreeMap<Key, (PreviousClose, PreviousCloseRequirement)>,
}
impl Cache {
    /// Lookup remains scoped to the startup target session; no latest fallback.
    pub fn get(
        &self,
        scope: Scope,
        as_of_ns: u64,
    ) -> Result<(&PreviousClose, &PreviousCloseRequirement)> {
        let (record, requirement) = self
            .records
            .get(&key(scope))
            .ok_or_else(|| Error::Unready("previous-close cache scope missing".into()))?;
        record.require(scope, requirement, as_of_ns)?;
        Ok((record, requirement))
    }
}
impl Report {
    pub fn outcomes(&self) -> &[Outcome] {
        &self.outcomes
    }
    pub fn complete(&self) -> bool {
        self.outcomes.iter().all(|row| row.result.is_ok())
    }
    /// Partial startup results cannot be promoted into the ready reference cache.
    pub fn into_cache(self) -> Result<Cache> {
        let mut records = BTreeMap::new();
        for row in self.outcomes {
            let record = row.result?;
            record.require(row.request.scope, &row.request.requirement, self.as_of_ns)?;
            if records
                .insert(key(row.request.scope), (record, row.request.requirement))
                .is_some()
            {
                return Err(Error::Conflict("duplicate reference cache scope".into()));
            }
        }
        Ok(Cache { records })
    }
}
/// Planning failure occurs before the loader is called. This resolves only the
/// previous-close branch; other dependency readiness remains independently required.
pub async fn load_planned(
    loader: &impl Loader,
    dependencies: &arte_core::dependency_plan::Plan,
    bindings: Vec<plan::Binding>,
    as_of_ns: u64,
    concurrency: usize,
    stop: watch::Receiver<bool>,
) -> Result<Report> {
    load(
        loader,
        plan::requests(dependencies, bindings)?,
        as_of_ns,
        concurrency,
        stop,
    )
    .await
}
pub async fn load(
    loader: &impl Loader,
    requests: Vec<Request>,
    as_of_ns: u64,
    concurrency: usize,
    stop: watch::Receiver<bool>,
) -> Result<Report> {
    if requests.len() > 4096 || concurrency == 0 || concurrency > 32 {
        return Err(Error::Capacity("reference startup bounds".into()));
    }
    let mut keys = BTreeSet::new();
    for request in &requests {
        request.requirement.validate(request.scope)?;
        if !keys.insert(key(request.scope)) {
            return Err(Error::Conflict("duplicate reference startup scope".into()));
        }
    }
    let mut work = stream::iter(requests.into_iter().enumerate()).map(|(index, request)| {
        let mut stop = stop.clone();
        async move {
            let result = if *stop.borrow() { Err(Error::Unready("reference startup stopped".into())) } else {
                tokio::select! {
                    biased;
                    _ = async { loop { if stop.changed().await.is_err() || *stop.borrow() { break; } } } => Err(Error::Unready("reference startup stopped".into())),
                    result = loader.load(&request, as_of_ns) => result.and_then(|record| {
                        record.require(request.scope, &request.requirement, as_of_ns)?;
                        Ok(record)
                    }),
                }
            };
            (index, Outcome { request, result })
        }
    }).buffer_unordered(concurrency);
    let mut outcomes = Vec::new();
    while let Some(row) = work.next().await {
        outcomes.push(row);
    }
    outcomes.sort_by_key(|(index, _)| *index);
    Ok(Report {
        outcomes: outcomes.into_iter().map(|(_, row)| row).collect(),
        as_of_ns,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    struct Source {
        calls: AtomicUsize,
        wrong: bool,
    }
    fn record(instrument: u64) -> PreviousClose {
        PreviousClose {
            provider: 1,
            instrument,
            session: 20260914,
            price: arte_core::events::Decimal {
                atoms: 10,
                scale: 0,
            },
            available_at_ns: 10,
            source_manifest_hash: "a".repeat(64),
        }
    }
    fn request(instrument: u64) -> Request {
        Request {
            scope: Scope {
                provider: 1,
                instrument,
                session: 20260915,
            },
            requirement: PreviousCloseRequirement {
                session: 20260914,
                record_hash: arte_core::content_hash(&record(instrument)).unwrap(),
            },
        }
    }
    impl Loader for Source {
        async fn load(&self, request: &Request, _: u64) -> Result<PreviousClose> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
            Ok(record(if self.wrong {
                99
            } else {
                request.scope.instrument
            }))
        }
    }
    #[tokio::test(start_paused = true)]
    async fn bounded_load_produces_scoped_memory_cache() {
        let source = Source {
            calls: AtomicUsize::new(0),
            wrong: false,
        };
        let (_tx, rx) = watch::channel(false);
        let start = tokio::time::Instant::now();
        let report = load(&source, vec![request(1), request(2), request(3)], 10, 2, rx)
            .await
            .unwrap();
        assert_eq!(start.elapsed(), std::time::Duration::from_secs(2));
        assert!(report.complete());
        let cache = report.into_cache().unwrap();
        assert_eq!(cache.get(request(2).scope, 10).unwrap().0.instrument, 2);
        assert!(cache.get(request(2).scope, 9).is_err());
        assert!(cache.get(request(4).scope, 10).is_err());
        assert_eq!(source.calls.load(Ordering::SeqCst), 3);
    }
    #[tokio::test(start_paused = true)]
    async fn failed_and_stopped_loads_cannot_publish_ready_cache() {
        let source = Source {
            calls: AtomicUsize::new(0),
            wrong: true,
        };
        let (tx, rx) = watch::channel(false);
        let report = load(&source, vec![request(1)], 10, 1, rx.clone())
            .await
            .unwrap();
        assert!(!report.complete());
        assert!(report.into_cache().is_err());
        tx.send(true).unwrap();
        let report = load(&source, vec![request(1), request(2)], 10, 2, rx)
            .await
            .unwrap();
        assert_eq!(report.outcomes.len(), 2);
        assert!(!report.complete());
        assert!(report.into_cache().is_err());
        assert_eq!(source.calls.load(Ordering::SeqCst), 1);
    }
    #[tokio::test]
    async fn duplicate_requests_fail_before_io() {
        let source = Source {
            calls: AtomicUsize::new(0),
            wrong: false,
        };
        let (_tx, rx) = watch::channel(false);
        assert!(load(&source, vec![request(1), request(1)], 10, 2, rx)
            .await
            .is_err());
        assert_eq!(source.calls.load(Ordering::SeqCst), 0);
    }
}
