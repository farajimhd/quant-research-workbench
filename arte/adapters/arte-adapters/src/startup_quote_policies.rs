//! Bounded startup loading. Immutable per-provider policies are shared by ticker books.
use arte_core::{coverage::Interval, quote_state::eligibility::Pinned, Error, Result};
use futures_util::{stream, StreamExt};
use std::{
    collections::{BTreeMap, BTreeSet},
    future::Future,
    sync::Arc,
};
use tokio::sync::watch;

#[derive(Clone)]
pub struct Request {
    pub provider: u16,
    pub hash: String,
    pub use_interval: Interval,
}
pub trait Loader {
    fn load(&self, request: &Request, as_of_ns: u64)
        -> impl Future<Output = Result<Pinned>> + Send;
}
impl Loader for crate::clickhouse::ClickHouse {
    async fn load(&self, request: &Request, as_of_ns: u64) -> Result<Pinned> {
        self.load_quote_policy(request.provider, &request.hash, as_of_ns)
            .await
    }
}
pub struct Outcome {
    pub request: Request,
    pub result: Result<Pinned>,
}
pub struct Report {
    outcomes: Vec<Outcome>,
    as_of_ns: u64,
}
struct Cached {
    request: Request,
    policy: Arc<Pinned>,
}
pub struct Cache {
    providers: BTreeMap<u16, Cached>,
}
impl Cache {
    /// A cheap Arc clone, not a copy of the condition/indicator sets.
    pub fn get(&self, provider: u16, at_ns: u64) -> Result<Arc<Pinned>> {
        let cached = self
            .providers
            .get(&provider)
            .ok_or_else(|| Error::Unready("quote policy provider not cached".into()))?;
        if at_ns < cached.request.use_interval.start || at_ns >= cached.request.use_interval.end {
            return Err(Error::Unready(
                "quote policy cache outside declared use interval".into(),
            ));
        }
        cached
            .policy
            .require_interval(cached.request.use_interval, at_ns)?;
        Ok(Arc::clone(&cached.policy))
    }
}
impl Report {
    pub fn outcomes(&self) -> &[Outcome] {
        &self.outcomes
    }
    pub fn into_cache(self) -> Result<Cache> {
        if self.outcomes.is_empty() {
            return Err(Error::Unready(
                "quote policy startup has no providers".into(),
            ));
        }
        let mut providers = BTreeMap::new();
        for row in self.outcomes {
            let policy = row.result?;
            require(&policy, &row.request, self.as_of_ns)?;
            if providers
                .insert(
                    row.request.provider,
                    Cached {
                        request: row.request,
                        policy: Arc::new(policy),
                    },
                )
                .is_some()
            {
                return Err(Error::Conflict(
                    "duplicate cached quote policy provider".into(),
                ));
            }
        }
        Ok(Cache { providers })
    }
}
fn require(policy: &Pinned, request: &Request, as_of_ns: u64) -> Result<()> {
    if policy.provider() != request.provider || policy.hash() != request.hash {
        return Err(Error::Conflict(
            "loaded quote policy differs from startup pin".into(),
        ));
    }
    policy.require_interval(request.use_interval, as_of_ns)
}
pub async fn load(
    loader: &impl Loader,
    requests: Vec<Request>,
    as_of_ns: u64,
    concurrency: usize,
    stop: watch::Receiver<bool>,
) -> Result<Report> {
    if requests.is_empty() || requests.len() > 256 || concurrency == 0 || concurrency > 32 {
        return Err(Error::Capacity("quote policy startup bounds".into()));
    }
    let mut providers = BTreeSet::new();
    for request in &requests {
        request.use_interval.validate()?;
        if request.provider == 0
            || request.hash.len() != 64
            || !request
                .hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("quote policy startup identity".into()));
        }
        if !providers.insert(request.provider) {
            return Err(Error::Conflict("duplicate quote policy provider".into()));
        }
    }
    let mut work = stream::iter(requests.into_iter().enumerate()).map(|(index, request)| {
        let mut stop = stop.clone();
        async move {
            let result = if *stop.borrow() { Err(Error::Unready("quote policy startup stopped".into())) } else {
                tokio::select! {
                    biased;
                    _ = async { loop { if stop.changed().await.is_err() || *stop.borrow() { break; } } } => Err(Error::Unready("quote policy startup stopped".into())),
                    result = loader.load(&request, as_of_ns) => result.and_then(|policy| { require(&policy, &request, as_of_ns)?; Ok(policy) }),
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
    use arte_core::quote_state::eligibility::Policy;
    use std::sync::atomic::{AtomicUsize, Ordering};
    fn policy(provider: u16) -> Policy {
        Policy {
            provider,
            valid_from_ns: 100,
            valid_to_ns: 200,
            available_at_ns: 10,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: Default::default(),
            allowed_indicators: Default::default(),
            allow_empty_conditions: true,
            allow_empty_indicators: true,
        }
    }
    fn request(provider: u16) -> Request {
        Request {
            provider,
            hash: arte_core::content_hash(&policy(provider)).unwrap(),
            use_interval: Interval {
                start: 100,
                end: 200,
            },
        }
    }
    struct Source {
        calls: AtomicUsize,
        wrong: bool,
    }
    impl Loader for Source {
        async fn load(&self, r: &Request, _: u64) -> Result<Pinned> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            let mut p = policy(r.provider);
            if self.wrong {
                p.allow_empty_conditions = false;
            }
            let hash = arte_core::content_hash(&p).unwrap();
            Pinned::new(p, &hash)
        }
    }
    #[tokio::test]
    async fn provider_policy_loads_once_and_is_shared_with_bounded_causal_access() {
        let source = Source {
            calls: AtomicUsize::new(0),
            wrong: false,
        };
        let (_sender, stop) = watch::channel(false);
        let report = load(&source, vec![request(1), request(2)], 10, 2, stop)
            .await
            .unwrap();
        assert_eq!(report.outcomes[0].request.provider, 1);
        let cache = report.into_cache().unwrap();
        assert_eq!(source.calls.load(Ordering::SeqCst), 2);
        assert!(Arc::ptr_eq(
            &cache.get(1, 100).unwrap(),
            &cache.get(1, 199).unwrap()
        ));
        assert!(cache.get(1, 99).is_err());
        assert!(cache.get(1, 200).is_err());
        assert!(cache.get(3, 100).is_err());
        let mut book = arte_core::quote_state::Book::new(arte_core::event_order::Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        })
        .unwrap();
        book.bind_shared_policy(cache.get(1, 100).unwrap()).unwrap();
    }
    #[tokio::test]
    async fn invalid_cancelled_and_partial_loads_cannot_become_ready() {
        let source = Source {
            calls: AtomicUsize::new(0),
            wrong: false,
        };
        let (_sender, stop) = watch::channel(false);
        assert!(
            load(&source, vec![request(1), request(1)], 10, 2, stop.clone())
                .await
                .is_err()
        );
        assert_eq!(source.calls.load(Ordering::SeqCst), 0);
        let mut too_wide = request(1);
        too_wide.use_interval.end = 201;
        assert!(load(&source, vec![too_wide], 10, 1, stop.clone())
            .await
            .unwrap()
            .into_cache()
            .is_err());
        assert!(load(&source, vec![request(1)], 9, 1, stop.clone())
            .await
            .unwrap()
            .into_cache()
            .is_err());
        let wrong = Source {
            calls: AtomicUsize::new(0),
            wrong: true,
        };
        assert!(load(&wrong, vec![request(1)], 10, 1, stop)
            .await
            .unwrap()
            .into_cache()
            .is_err());
        let (_sender, stop) = watch::channel(true);
        let calls = source.calls.load(Ordering::SeqCst);
        let report = load(&source, vec![request(1), request(2)], 10, 1, stop)
            .await
            .unwrap();
        assert_eq!(report.outcomes.len(), 2);
        assert!(report.into_cache().is_err());
        assert_eq!(source.calls.load(Ordering::SeqCst), calls);
    }
}
