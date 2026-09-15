//! Linked per-page progress avoids rewriting the entire prefix on each page.
use super::*;
use serde::{Deserialize, Serialize};
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub schema_version: u16,
    pub plan_hash: String,
    pub previous: Option<String>,
    pub page_number: usize,
    pub page: Page,
    pub next_url: Option<String>,
    pub last_sip_ns: Option<u64>,
}
impl Record {
    pub fn id(&self) -> Result<String> {
        arte_core::content_hash(self)
    }
}
impl Acquisition {
    fn plan_hash(&self) -> Result<String> {
        arte_core::content_hash(&(&self.authority, self.interval, &self.path, &self.first_hash))
    }
    pub(super) fn make_progress(&self) -> Result<Record> {
        Ok(Record {
            schema_version: 1,
            plan_hash: self.plan_hash()?,
            previous: self.last_checkpoint.clone(),
            page_number: self.pages.len(),
            page: self
                .pages
                .last()
                .ok_or_else(|| Error::Unready("no completed acquisition page".into()))?
                .clone(),
            next_url: self.next.clone(),
            last_sip_ns: self.last_sip,
        })
    }
    pub fn progress_record(&self) -> Option<&Record> {
        self.checkpoint_to_ack.as_ref()
    }
    /// Call only after immutable progress readback. A matching string alone does
    /// not prove storage durability; the supervisor owns this acknowledgment.
    pub fn acknowledge_progress(&mut self, id: &str) -> Result<()> {
        let record = self
            .checkpoint_to_ack
            .as_ref()
            .ok_or_else(|| Error::Unready("no acquisition progress pending".into()))?;
        if record.id()? != id {
            return Err(Error::Conflict(
                "acquisition progress acknowledgment differs".into(),
            ));
        }
        self.last_checkpoint = Some(id.to_owned());
        self.checkpoint_to_ack = None;
        Ok(())
    }
    /// Records must be root-to-head and match a separately pinned durable head.
    /// Restored progress is NOT coverage. Final coverage re-reads every event batch.
    pub fn restore(mut self, head: &str, records: &[Record]) -> Result<Self> {
        if !self.pages.is_empty()
            || self.pending.is_some()
            || records.is_empty()
            || records.len() > self.maximum_pages
        {
            return Err(Error::Invalid(
                "invalid acquisition restore bounds or owner state".into(),
            ));
        }
        let plan = self.plan_hash()?;
        for record in records {
            if record.schema_version != 1
                || record.plan_hash != plan
                || record.previous != self.last_checkpoint
                || record.page_number != self.pages.len() + 1
            {
                return Err(Error::Conflict(
                    "acquisition progress chain or plan mismatch".into(),
                ));
            }
            let expected = self
                .next
                .as_ref()
                .ok_or_else(|| Error::Conflict("progress after completed pagination".into()))?;
            if arte_core::content_hash(expected)? != record.page.request_hash {
                return Err(Error::Conflict("restored request cursor mismatch".into()));
            }
            let next_hash = record
                .next_url
                .as_deref()
                .map(|url| arte_core::content_hash(&url))
                .transpose()?;
            if next_hash != record.page.next_request_hash {
                return Err(Error::Conflict("restored next cursor hash mismatch".into()));
            }
            if let Some(url) = &record.next_url {
                if crate::massive::validate_page_url(url, &self.path)?.as_str() != url {
                    return Err(Error::Invalid("unsafe restored cursor".into()));
                }
            }
            if record
                .last_sip_ns
                .is_some_and(|t| t < self.interval.start || t >= self.interval.end)
                || (record.page.source_rows > 0 && record.last_sip_ns.is_none())
                || self
                    .last_sip
                    .is_some_and(|t| record.last_sip_ns.is_none_or(|next| next < t))
                || (record.page.source_rows == 0 && record.last_sip_ns != self.last_sip)
            {
                return Err(Error::Invalid("restored SIP frontier mismatch".into()));
            }
            self.acknowledged
                .extend(record.page.batches.iter().cloned());
            self.visited.insert(record.page.request_hash.clone());
            self.pages.push(record.page.clone());
            self.last_sip = record.last_sip_ns;
            self.next = record.next_url.clone();
            self.last_checkpoint = Some(record.id()?);
        }
        if self.last_checkpoint.as_deref() != Some(head) {
            return Err(Error::Conflict("acquisition durable head mismatch".into()));
        }
        let prefix = arte_core::acquisition::Certificate {
            schema_version: 1,
            authority: self.authority.clone(),
            interval: self.interval,
            first_request_hash: self.first_hash.clone(),
            published_at_ns: self.pages.last().unwrap().acquired_at_ns,
            pages: self.pages.clone(),
        };
        prefix.validate_prefix(&self.acknowledged)?;
        Ok(self)
    }
}
