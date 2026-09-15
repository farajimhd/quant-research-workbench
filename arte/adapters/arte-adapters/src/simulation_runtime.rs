//! Historical execution lane: quote -> simulated fills -> journal -> projection.
//! This owns no live broker capability. Strategy/market scheduling stays upstream.
use crate::fill_journal::{Batch, Committer, Publisher};
use arte_core::{
    content_hash,
    execution_events::Fill,
    execution_positions::{Key, Position, Projection},
    orders::Bracket,
    simulated_execution::{Amendment, Quote, Simulator},
    Error, Result,
};
use serde::Serialize;
struct Pending {
    quote_hash: String,
    fills: Vec<Fill>,
    applied: usize,
    current: Option<(usize, Committer)>,
}
#[derive(Debug, Clone, Serialize)]
pub struct Status {
    pub pending_fills: usize,
    pub applied_in_pending_quote: usize,
}
pub struct Runtime {
    simulator: Simulator,
    projection: Projection,
    pending: Option<Pending>,
}
impl Runtime {
    pub fn new(
        simulator: Simulator,
        projection: Projection,
        maximum_pending_fills: usize,
    ) -> Result<Self> {
        if maximum_pending_fills == 0
            || maximum_pending_fills > 200000
            || simulator.maximum_quote_fills() > maximum_pending_fills
        {
            return Err(Error::Capacity(
                "simulation quote can exceed pending-fill budget".into(),
            ));
        }
        Ok(Self {
            simulator,
            projection,
            pending: None,
        })
    }
    fn ready(&self) -> Result<()> {
        if self.pending.is_some() {
            return Err(Error::Unready(
                "simulation fills await journal/projection; retain next input".into(),
            ));
        }
        Ok(())
    }
    pub fn status(&self) -> Status {
        match &self.pending {
            None => Status {
                pending_fills: 0,
                applied_in_pending_quote: 0,
            },
            Some(p) => {
                let applied = p.applied + p.current.as_ref().map_or(0, |(_, c)| c.projected());
                Status {
                    pending_fills: p.fills.len() - applied,
                    applied_in_pending_quote: applied,
                }
            }
        }
    }
    pub fn position(&self, key: &Key) -> Option<&Position> {
        self.projection.position(key)
    }
    pub fn submit(&mut self, bracket: Bracket, now_ns: u64, latency_ns: u64) -> Result<()> {
        self.ready()?;
        self.simulator.submit(bracket, now_ns, latency_ns)
    }
    pub fn amend(
        &mut self,
        command: &str,
        revision: u64,
        at_ns: u64,
        amendment: &Amendment,
    ) -> Result<()> {
        self.ready()?;
        self.simulator
            .acknowledge_amendment(command, revision, at_ns, amendment)
    }
    pub fn quote(&mut self, quote: &Quote) -> Result<()> {
        let hash = content_hash(quote)?;
        if let Some(p) = &self.pending {
            return if p.quote_hash == hash {
                Ok(())
            } else {
                Err(Error::Unready(
                    "next quote cannot overtake pending execution fills".into(),
                ))
            };
        }
        let fills = self.simulator.quote(quote)?;
        if !fills.is_empty() {
            self.pending = Some(Pending {
                quote_hash: hash,
                fills,
                applied: 0,
                current: None,
            });
        }
        Ok(())
    }
    /// Select the matching scope-owned publisher before committing a batch.
    pub fn next_scope_hash(&self) -> Result<Option<String>> {
        self.pending
            .as_ref()
            .map(|p| content_hash(&Key::from_fill(&p.fills[p.applied])?))
            .transpose()
    }
    /// One bounded contiguous-scope batch per call. False means no work remains.
    /// Cancellation preserves the committer and exact pending quote identity.
    pub async fn commit_next(&mut self, publisher: &mut impl Publisher) -> Result<bool> {
        let Some(p) = &mut self.pending else {
            return Ok(false);
        };
        if p.current.is_none() {
            let scope = Key::from_fill(&p.fills[p.applied])?;
            let mut end = p.applied + 1;
            while end < p.fills.len()
                && end - p.applied < 256
                && Key::from_fill(&p.fills[end])? == scope
            {
                end += 1;
            }
            let batch = Batch::new(p.fills[p.applied..end].to_vec())?;
            p.current = Some((end, Committer::new(batch)));
        }
        let (end, committer) = p.current.as_mut().unwrap();
        committer.commit(publisher, &mut self.projection).await?;
        p.applied = *end;
        p.current = None;
        if p.applied == p.fills.len() {
            self.pending = None;
        }
        Ok(true)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    fn bracket(account: &str) -> Bracket {
        Bracket {
            command_id: account.into(),
            account: account.into(),
            instrument: 1,
            side: arte_core::orders::Side::Long,
            quantity: 1,
            entry: 100,
            price_scale: 2,
            stop: Some(90),
            target: Some(110),
            tick: 1,
            deadline_ns: 100,
        }
    }
    struct Store {
        fail: bool,
        calls: usize,
        rows: BTreeMap<String, String>,
    }
    impl Publisher for Store {
        async fn publish(&mut self, batch: &Batch) -> Result<BTreeMap<String, String>> {
            self.calls += 1;
            self.rows.extend(batch.rows().clone());
            if self.fail {
                self.fail = false;
                return Err(Error::Unready("simulated ambiguous journal write".into()));
            }
            Ok(batch.rows().clone())
        }
    }
    #[tokio::test]
    async fn pending_quote_blocks_overtaking_and_accounts_commit_separately() {
        let simulator = Simulator::new_scoped("r", 1, 2, 2, 10000).unwrap();
        let mut runtime = Runtime::new(simulator, Projection::new(2, 10, 4).unwrap(), 4).unwrap();
        runtime.submit(bracket("a"), 0, 0).unwrap();
        runtime.submit(bracket("b"), 0, 0).unwrap();
        let q = Quote {
            sequence: 1,
            at_ns: 1,
            bid: 99,
            ask: 100,
            bid_size: 10,
            ask_size: 10,
        };
        runtime.quote(&q).unwrap();
        assert_eq!(runtime.status().pending_fills, 2);
        let first_scope = runtime.next_scope_hash().unwrap().unwrap();
        let mut store = Store {
            fail: true,
            calls: 0,
            rows: BTreeMap::new(),
        };
        assert!(runtime.commit_next(&mut store).await.is_err());
        let fill: Fill = serde_json::from_str(store.rows.values().next().unwrap()).unwrap();
        let key = Key::from_fill(&fill).unwrap();
        assert!(runtime.position(&key).is_none());
        runtime.quote(&q).unwrap();
        let mut next = q.clone();
        next.sequence += 1;
        next.at_ns += 1;
        assert!(runtime.quote(&next).is_err());
        assert!(runtime.amend("a", 1, 1, &Amendment::ExitPosition).is_err());
        runtime.commit_next(&mut store).await.unwrap();
        assert_eq!(runtime.position(&key).unwrap().quantity, 1);
        assert_eq!(runtime.status().pending_fills, 1);
        assert_ne!(runtime.next_scope_hash().unwrap().unwrap(), first_scope);
        runtime.commit_next(&mut store).await.unwrap();
        assert!(!runtime.commit_next(&mut store).await.unwrap());
        assert_eq!(runtime.status().pending_fills, 0);
        assert_eq!(store.calls, 3);
        runtime.amend("a", 1, 1, &Amendment::ExitPosition).unwrap();
        runtime.quote(&next).unwrap();
        runtime.commit_next(&mut store).await.unwrap();
        assert_eq!(runtime.position(&key).unwrap().quantity, 0);
    }
    #[test]
    fn pending_budget_covers_worst_case_quote_before_acceptance() {
        assert!(Runtime::new(
            Simulator::new_scoped("r", 1, 2, 2, 10000).unwrap(),
            Projection::new(2, 10, 4).unwrap(),
            3
        )
        .is_err());
    }
}
