use super::*;
use crate::{
    fill_journal::{Batch, Publisher},
    ownership::Lease,
};
use arte_core::config::Acceptance;
use std::collections::BTreeSet;
const TABLE: &str = "execution_fills_v1";
pub struct FillPublisher<'a> {
    database: &'a ClickHouse,
    lease: &'a mut Lease,
    scope_hash: String,
}
impl<'a> FillPublisher<'a> {
    pub fn new(
        database: &'a ClickHouse,
        lease: &'a mut Lease,
        scope_hash: String,
        passed: &BTreeSet<Acceptance>,
    ) -> Result<Self> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "fill writer acceptance missing: {required:?}"
                )));
            }
        }
        lease.require(&scope_hash)?;
        Ok(Self {
            database,
            lease,
            scope_hash,
        })
    }
}
impl Publisher for FillPublisher<'_> {
    async fn publish(&mut self, batch: &Batch) -> Result<BTreeMap<String, String>> {
        self.lease.require(&self.scope_hash)?;
        if batch.scope_hash()? != self.scope_hash {
            return Err(Error::Conflict("fill writer scope mismatch".into()));
        }
        self.database.verify_storage(TABLE).await?;
        self.database
            .stage_event_values(TABLE, batch.rows())
            .await?;
        let rows = self
            .database
            .event_values(TABLE, &batch.rows().keys().cloned().collect::<Vec<_>>())
            .await?;
        batch.verify_readback(&rows)?;
        Ok(rows)
    }
}
