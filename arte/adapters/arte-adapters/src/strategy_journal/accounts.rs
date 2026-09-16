//! Bounded concurrent journal writes, with owned receipts across cancellation.
use super::{commit, Prepared, Publisher};
use arte_core::{account_boundary::Barrier, strategy_transaction::Committed, Error, Result};
use futures_util::{stream, StreamExt};
use std::collections::BTreeSet;

/// Preflight and receipt registration share the same boundary owner. This avoids
/// extracting or replacing a manifest-bound playback controller's private barrier.
pub trait Boundary {
    fn validate_decision(&self, decision: &arte_core::strategy_dispatch::Decision) -> Result<()>;
    fn record(&mut self, committed: &Committed) -> Result<bool>;
}
impl Boundary for Barrier {
    fn validate_decision(&self, decision: &arte_core::strategy_dispatch::Decision) -> Result<()> {
        self.validate_decision(decision)
    }
    fn record(&mut self, committed: &Committed) -> Result<bool> {
        self.record(committed)
    }
}
impl Boundary for arte_core::market_structure::scheduler::playback::accounts::Run {
    fn validate_decision(&self, decision: &arte_core::strategy_dispatch::Decision) -> Result<()> {
        self.validate_decision(decision)
    }
    fn record(&mut self, committed: &Committed) -> Result<bool> {
        self.record(committed)
    }
}

/// Keep these slots alive across cancellation and retry. A successful account is
/// never submitted again. Dropping a slot is not a durable recovery mechanism.
pub struct Write<'a, R, P> {
    runtime: &'a mut R,
    publisher: &'a mut P,
    receipt: Option<Committed>,
}
impl<'a, R: Prepared, P: Publisher> Write<'a, R, P> {
    pub fn new(runtime: &'a mut R, publisher: &'a mut P) -> Self {
        Self {
            runtime,
            publisher,
            receipt: None,
        }
    }
    pub fn receipt(&self) -> Option<&Committed> {
        self.receipt.as_ref()
    }
}
pub struct Outcome {
    pub index: usize,
    pub result: Result<()>,
}
/// All supplied writes are preflighted before the first I/O. Independent failures
/// do not cancel other accounts. Results include failures; the barrier still owns
/// the full consumer set and cannot release an omitted or failed consumer.
pub async fn commit_accounts<R: Prepared, P: Publisher>(
    writes: &mut [Write<'_, R, P>],
    barrier: &mut impl Boundary,
    concurrency: usize,
) -> Result<Vec<Outcome>> {
    if writes.is_empty() || writes.len() > 4096 || concurrency == 0 || concurrency > 64 {
        return Err(Error::Capacity(
            "account journal concurrency or slot budget".into(),
        ));
    }
    let mut scopes = BTreeSet::new();
    for write in writes.iter() {
        let decision = match &write.receipt {
            Some(receipt) => receipt.decision().clone(),
            None => {
                let records = write
                    .runtime
                    .pending()
                    .ok_or_else(|| {
                        Error::Unready(
                            "account has no prepared decision or retained receipt".into(),
                        )
                    })?
                    .records();
                if records.len() != 1 {
                    return Err(Error::Invalid(
                        "account boundary requires one decision".into(),
                    ));
                }
                records[0].decode()?
            }
        };
        barrier.validate_decision(&decision)?;
        if !scopes.insert(arte_core::content_hash(&decision.scope)?) {
            return Err(Error::Conflict("duplicate account journal slot".into()));
        }
    }
    let mut pending = stream::iter(writes.iter_mut().enumerate())
        .map(|(index, write)| async move {
            let result = if write.receipt.is_some() {
                Ok(())
            } else {
                match commit(write.runtime, write.publisher).await {
                    Ok(receipt) => {
                        write.receipt = Some(receipt);
                        Ok(())
                    }
                    Err(error) => Err(error),
                }
            };
            (index, write, result)
        })
        .buffer_unordered(concurrency);
    let mut outcomes = Vec::new();
    while let Some((index, write, result)) = pending.next().await {
        // No await between retaining the receipt and updating the barrier.
        let result =
            result.and_then(|()| barrier.record(write.receipt.as_ref().unwrap()).map(|_| ()));
        outcomes.push(Outcome { index, result });
    }
    outcomes.sort_by_key(|outcome| outcome.index);
    Ok(outcomes)
}
