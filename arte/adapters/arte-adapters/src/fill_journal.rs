//! Freeze, persist, verify, then project fills. No implicit retry or dropped prefix.
use arte_core::{
    content_hash,
    execution_events::Fill,
    execution_positions::{Key, Projection},
    Error, Result,
};
use std::{collections::BTreeMap, future::Future};
pub struct Batch {
    scope: Key,
    fills: Vec<Fill>,
    rows: BTreeMap<String, String>,
}
impl Batch {
    pub fn new(fills: Vec<Fill>) -> Result<Self> {
        if fills.is_empty() || fills.len() > 256 {
            return Err(Error::Capacity("fill batch count outside 1..256".into()));
        }
        let scope = Key::from_fill(&fills[0])?;
        let mut rows = BTreeMap::new();
        let mut bytes = 0usize;
        let mut prior = (0, 0);
        for fill in &fills {
            if Key::from_fill(fill)? != scope || fill.at_ns < prior.0 || fill.sequence < prior.1 {
                return Err(Error::Invalid(
                    "fill batch scope or ordering mismatch".into(),
                ));
            }
            prior = (fill.at_ns, fill.sequence);
            let id = fill.id()?;
            let value =
                serde_json::to_string(fill).map_err(|e| Error::Serialization(e.to_string()))?;
            bytes = bytes
                .checked_add(value.len())
                .ok_or_else(|| Error::Capacity("fill batch overflow".into()))?;
            if bytes > 4 * 1024 * 1024 {
                return Err(Error::Capacity("fill batch byte budget".into()));
            }
            if rows
                .insert(id, value.clone())
                .is_some_and(|old| old != value)
            {
                return Err(Error::Conflict(
                    "fill batch execution identity changed".into(),
                ));
            }
        }
        Ok(Self { scope, fills, rows })
    }
    pub fn scope_hash(&self) -> Result<String> {
        content_hash(&self.scope)
    }
    pub(crate) fn rows(&self) -> &BTreeMap<String, String> {
        &self.rows
    }
    pub fn verify_readback(&self, rows: &BTreeMap<String, String>) -> Result<()> {
        if rows != &self.rows {
            return Err(Error::Conflict(
                "fill journal readback differs or is incomplete".into(),
            ));
        }
        Ok(())
    }
}
pub trait Publisher {
    fn publish(
        &mut self,
        batch: &Batch,
    ) -> impl Future<Output = Result<BTreeMap<String, String>>> + Send;
}
pub struct Committer {
    batch: Batch,
    durable_readback: bool,
    projected: usize,
}
impl Committer {
    pub fn new(batch: Batch) -> Self {
        Self {
            batch,
            durable_readback: false,
            projected: 0,
        }
    }
    pub fn projected(&self) -> usize {
        self.projected
    }
    pub fn complete(&self) -> bool {
        self.projected == self.batch.fills.len()
    }
    /// Keep this committer on error. A failed projection exposes its completed
    /// prefix and retains every remaining fill. No later batch may overtake it.
    pub async fn commit(
        &mut self,
        publisher: &mut impl Publisher,
        projection: &mut Projection,
    ) -> Result<()> {
        if !self.durable_readback {
            let rows = publisher.publish(&self.batch).await?;
            self.batch.verify_readback(&rows)?;
            self.durable_readback = true;
        }
        while self.projected < self.batch.fills.len() {
            projection.apply(&self.batch.fills[self.projected])?;
            self.projected += 1;
        }
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::execution_events::{Direction, Leg, Origin};
    fn fill(sequence: u64) -> Fill {
        Fill {
            schema_version: 1,
            origin: Origin::Simulated {
                run_id: "r".into(),
                model: "m".into(),
            },
            command_id: "c".into(),
            account: "a".into(),
            instrument: 1,
            price_scale: 2,
            sequence,
            at_ns: sequence,
            executed_at_ns: None,
            leg: Leg::Entry,
            direction: Direction::Buy,
            quantity: 1,
            price: 100,
        }
    }
    struct Store {
        fail: bool,
        calls: usize,
    }
    impl Publisher for Store {
        async fn publish(&mut self, batch: &Batch) -> Result<BTreeMap<String, String>> {
            self.calls += 1;
            if self.fail {
                self.fail = false;
                return Err(Error::Unready("ambiguous insert".into()));
            }
            Ok(batch.rows.clone())
        }
    }
    #[tokio::test]
    async fn database_failure_cannot_advance_projection_and_retry_is_exact() {
        let first = fill(1);
        let key = Key::from_fill(&first).unwrap();
        let mut commit = Committer::new(Batch::new(vec![first, fill(2)]).unwrap());
        let mut projection = Projection::new(1, 4, 2).unwrap();
        let mut store = Store {
            fail: true,
            calls: 0,
        };
        assert!(commit.commit(&mut store, &mut projection).await.is_err());
        assert_eq!(commit.projected(), 0);
        assert!(projection.position(&key).is_none());
        commit.commit(&mut store, &mut projection).await.unwrap();
        commit.commit(&mut store, &mut projection).await.unwrap();
        assert!(commit.complete());
        assert_eq!(store.calls, 2);
        assert_eq!(projection.position(&key).unwrap().quantity, 2);
    }
    #[tokio::test]
    async fn projection_failure_retains_and_reports_completed_prefix() {
        let mut bad = fill(2);
        bad.leg = Leg::Exit;
        bad.direction = Direction::Sell;
        bad.quantity = 2;
        let mut commit = Committer::new(Batch::new(vec![fill(1), bad]).unwrap());
        let mut projection = Projection::new(1, 4, 2).unwrap();
        let mut store = Store {
            fail: false,
            calls: 0,
        };
        assert!(commit.commit(&mut store, &mut projection).await.is_err());
        assert_eq!(commit.projected(), 1);
        assert!(!commit.complete());
        assert!(commit.commit(&mut store, &mut projection).await.is_err());
        assert_eq!(commit.projected(), 1);
        assert_eq!(store.calls, 1);
    }
    #[test]
    fn mixed_scope_and_incomplete_readback_fail() {
        let mut other = fill(2);
        other.account = "b".into();
        assert!(Batch::new(vec![fill(1), other]).is_err());
        let batch = Batch::new(vec![fill(1)]).unwrap();
        assert!(batch.verify_readback(&BTreeMap::new()).is_err());
    }
}
