//! Scheduler recovery includes applied-but-unacknowledged heads. Restoring this
//! graph is not a decision-journal receipt or permission to resume execution.
use super::*;
use crate::{content_hash, event_order::Buffer, seed_storage::Object};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Write,
    sync::Arc,
};

pub struct Request<'a> {
    pub context_hash: &'a str,
    pub run_id: &'a str,
    pub seed_hash: &'a str,
    pub configuration_hash: &'a str,
    pub quote_policy: Arc<crate::quote_state::eligibility::Pinned>,
    pub maximum_pending: usize,
    pub maximum_bytes: usize,
}
pub struct Bundle {
    pub root: Object,
    pub market: Object,
    pub trades: Object,
    pub quotes: Object,
    pub book: Object,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    context_hash: String,
    run_id: String,
    sequence: u64,
    pending: Option<Pending>,
    completed: Vec<(u64, usize, u64)>,
    eligibility: Vec<(EventKey, bool)>,
    quote_applied: Vec<(EventKey, String)>,
    trade_receipt_ns: u64,
    quote_receipt_ns: u64,
    market: String,
    trades: String,
    quotes: String,
    book: String,
}
fn hash_valid(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn bounds(context: &str, maximum: usize) -> Result<()> {
    if !hash_valid(context) || maximum == 0 || maximum > 64 * 1024 * 1024 {
        return Err(Error::Invalid(
            "scheduler recovery context or budget".into(),
        ));
    }
    Ok(())
}
struct Writer {
    bytes: Vec<u8>,
    maximum: usize,
}
impl Write for Writer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.maximum.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other("scheduler recovery byte budget"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
fn encode(root: &Root, maximum: usize) -> Result<Object> {
    let mut writer = Writer {
        bytes: Vec::new(),
        maximum,
    };
    serde_json::to_writer(&mut writer, root).map_err(|e| Error::Serialization(e.to_string()))?;
    Ok(Object::new(writer.bytes))
}
impl Scheduler {
    pub fn checkpoint(&self, context_hash: &str, maximum_bytes: usize) -> Result<Bundle> {
        bounds(context_hash, maximum_bytes)?;
        self.state()?;
        let market = self.market.runtime.checkpoint()?;
        let market = Object {
            id: market.hash,
            payload: market.bytes,
        };
        let trades = self.market.buffer.checkpoint(context_hash, maximum_bytes)?;
        let quotes = self.quotes.buffer.checkpoint(context_hash, maximum_bytes)?;
        let book = self
            .quotes
            .book
            .checkpoint(context_hash, maximum_bytes.min(1024 * 1024))?;
        let used = [&market, &trades, &quotes, &book]
            .iter()
            .try_fold(0usize, |n, o| n.checked_add(o.payload.len()))
            .ok_or_else(|| Error::Capacity("scheduler recovery size overflow".into()))?;
        if used >= maximum_bytes {
            return Err(Error::Capacity(
                "scheduler recovery component budget".into(),
            ));
        }
        let root = Root {
            version: 1,
            context_hash: context_hash.into(),
            run_id: self.run_id.clone(),
            sequence: self.sequence,
            pending: self.pending.clone(),
            completed: self.completed.iter().copied().collect(),
            eligibility: self
                .market
                .eligibility
                .iter()
                .map(|(k, v)| (k.clone(), *v))
                .collect(),
            quote_applied: self
                .quotes
                .applied
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
            trade_receipt_ns: self.market.newest_receipt_ns,
            quote_receipt_ns: self.quotes.newest_receipt_ns,
            market: market.id.clone(),
            trades: trades.id.clone(),
            quotes: quotes.id.clone(),
            book: book.id.clone(),
        };
        Ok(Bundle {
            root: encode(&root, maximum_bytes - used)?,
            market,
            trades,
            quotes,
            book,
        })
    }
    pub fn restore_checkpoint(
        bundle: &Bundle,
        expected_root: &str,
        request: Request<'_>,
    ) -> Result<Self> {
        bounds(request.context_hash, request.maximum_bytes)?;
        let total = [
            &bundle.root,
            &bundle.market,
            &bundle.trades,
            &bundle.quotes,
            &bundle.book,
        ]
        .iter()
        .try_fold(0usize, |n, o| n.checked_add(o.payload.len()))
        .ok_or_else(|| Error::Capacity("scheduler recovery size overflow".into()))?;
        if total > request.maximum_bytes || bundle.root.id != expected_root {
            return Err(Error::Invalid(
                "scheduler recovery identity or budget".into(),
            ));
        }
        for object in [
            &bundle.root,
            &bundle.market,
            &bundle.trades,
            &bundle.quotes,
            &bundle.book,
        ] {
            object.verify()?;
        }
        let root: Root = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if root.version != 1
            || root.context_hash != request.context_hash
            || root.run_id != request.run_id
            || root.run_id.is_empty()
            || root.run_id.len() > 128
            || root.market != bundle.market.id
            || root.trades != bundle.trades.id
            || root.quotes != bundle.quotes.id
            || root.book != bundle.book.id
            || encode(&root, request.maximum_bytes)?.payload != bundle.root.payload
        {
            return Err(Error::Conflict("scheduler recovery pins differ".into()));
        }
        let runtime = Runtime::restore(
            &bundle.market.payload,
            &bundle.market.id,
            request.seed_hash,
            request.configuration_hash,
        )?;
        let scope = runtime.source_scope();
        let trades = Buffer::restore_checkpoint(
            &bundle.trades,
            &root.trades,
            request.context_hash,
            scope,
            request.maximum_pending,
            request.maximum_bytes,
        )?;
        let quotes = Buffer::restore_checkpoint(
            &bundle.quotes,
            &root.quotes,
            request.context_hash,
            scope,
            request.maximum_pending,
            request.maximum_bytes,
        )?;
        let book = crate::quote_state::Book::restore_checkpoint(
            &bundle.book,
            &root.book,
            request.context_hash,
            scope,
            request.quote_policy,
            request.maximum_bytes.min(1024 * 1024),
        )?;
        if root.eligibility.len() != trades.pending()
            || root.quote_applied.len() > runtime.maximum_market_events
            || root.completed.len() > runtime.additional.len() + 1
            || trades.watermark_ns() != quotes.watermark_ns()
            || trades.watermark_ns() < runtime.start_ns
            || trades.watermark_ns() > runtime.end_ns
            || root.trade_receipt_ns < runtime.start_ns
            || root.quote_receipt_ns < runtime.start_ns
        {
            return Err(Error::Conflict(
                "scheduler recovery population or clocks".into(),
            ));
        }
        let eligibility: BTreeMap<_, _> = root.eligibility.iter().cloned().collect();
        let applied: BTreeMap<_, _> = root.quote_applied.iter().cloned().collect();
        if eligibility.len() != root.eligibility.len()
            || applied.len() != root.quote_applied.len()
            || !root.eligibility.windows(2).all(|p| p[0].0 < p[1].0)
            || !root.quote_applied.windows(2).all(|p| p[0].0 < p[1].0)
        {
            return Err(Error::Conflict(
                "scheduler recovery identity ordering".into(),
            ));
        }
        let maximum = runtime.maximum_market_events;
        let restored = Self {
            market: Ordered {
                runtime,
                buffer: trades,
                eligibility,
                newest_receipt_ns: root.trade_receipt_ns,
                failed: false,
            },
            run_id: root.run_id,
            sequence: root.sequence,
            pending: root.pending,
            completed: root.completed.into(),
            quotes: quotes::Quotes {
                buffer: quotes,
                book,
                applied,
                maximum,
                newest_receipt_ns: root.quote_receipt_ns,
            },
        };
        restored.validate_recovery()?;
        Ok(restored)
    }
    fn validate_recovery(&self) -> Result<()> {
        let scope = self.scope();
        let runtime = &self.market.runtime;
        let trade_head = match self.pending.as_ref().map(|p| &p.kind) {
            Some(PendingKind::Trade { key, eligible }) => Some((key, *eligible)),
            _ => None,
        };
        let quote_head = match self.pending.as_ref().map(|p| &p.kind) {
            Some(PendingKind::Quote { key }) => Some(key),
            _ => None,
        };
        for event in self.market.buffer.pending_events() {
            let eligible = self
                .market
                .eligibility
                .get(&event.key)
                .ok_or_else(|| Error::Conflict("recovered trade eligibility missing".into()))?;
            let applied = runtime.applied.get(&event.key);
            let expected = content_hash(&(&event.key, &event.payload, event.sip, eligible))?;
            if event.key.kind != crate::events::EventKind::Trade
                || event.available_at_ns > self.market.newest_receipt_ns
                || applied.is_some()
                    != trade_head
                        .is_some_and(|(key, value)| key == &event.key && value == *eligible)
                || applied.is_some_and(|hash| hash != &expected)
            {
                return Err(Error::Conflict(
                    "recovered trade application state differs".into(),
                ));
            }
        }
        for (key, hash) in &self.quotes.applied {
            if key.provider != scope.provider
                || key.instrument != scope.instrument
                || key.session != scope.session
                || key.kind != crate::events::EventKind::Quote
                || !hash_valid(hash)
            {
                return Err(Error::Conflict("recovered quote ledger scope".into()));
            }
        }
        for event in self.quotes.buffer.pending_events() {
            let applied = self.quotes.applied.get(&event.key);
            let expected = content_hash(&(&event.key, &event.payload, event.sip))?;
            if event.key.kind != crate::events::EventKind::Quote
                || event.available_at_ns > self.quotes.newest_receipt_ns
                || applied.is_some() != (quote_head == Some(&event.key))
                || applied.is_some_and(|hash| hash != &expected)
            {
                return Err(Error::Conflict(
                    "recovered quote application state differs".into(),
                ));
            }
        }
        match self.quotes.book.latest() {
            None if !self.quotes.applied.is_empty() => {
                return Err(Error::Conflict("recovered quote book missing".into()))
            }
            Some(event)
                if self.quotes.applied.get(&event.key)
                    != Some(&content_hash(&(&event.key, &event.payload, event.sip))?)
                    || event.available_at_ns > self.quotes.newest_receipt_ns
                    || quote_head.is_some_and(|key| key != &event.key) =>
            {
                return Err(Error::Conflict(
                    "recovered quote book and ledger differ".into(),
                ));
            }
            _ => {}
        }
        let mut completions = BTreeSet::new();
        for (interval, index, available) in &self.completed {
            let bar = runtime
                .timeframe(*interval)?
                .completed()
                .get(*index)
                .ok_or_else(|| Error::Invalid("recovered completed bar missing".into()))?;
            if !completions.insert((*interval, *index))
                || *available < bar.bar.end_ns
                || *available > runtime.observed_at_ns
            {
                return Err(Error::Conflict("recovered completed queue differs".into()));
            }
        }
        if let Some(pending) = self.pending()? {
            runtime.clock(self.watermark_ns(), pending.evaluated_at_ns)?;
            if self.sequence == 0
                || pending.evaluated_at_ns
                    < self
                        .market
                        .newest_receipt_ns
                        .max(self.quotes.newest_receipt_ns)
            {
                return Err(Error::Conflict("recovered pending boundary clock".into()));
            }
            let kind_hash = match pending.kind {
                Kind::Quote { observation } => content_hash(&("quote", &observation.key))?,
                Kind::Trade {
                    observation,
                    eligible,
                } => content_hash(&(&observation.key, eligible))?,
                Kind::Completed {
                    interval_ns,
                    bar,
                    available_at_ns,
                } => {
                    if available_at_ns < bar.bar.end_ns || available_at_ns > pending.evaluated_at_ns
                    {
                        return Err(Error::Conflict("recovered bar availability differs".into()));
                    }
                    if let Some(Pending {
                        kind: PendingKind::Completed { index, .. },
                        ..
                    }) = &self.pending
                    {
                        if completions.contains(&(interval_ns, *index)) {
                            return Err(Error::Conflict(
                                "pending bar repeated in completion queue".into(),
                            ));
                        }
                    }
                    content_hash(&(&interval_ns, &available_at_ns, bar))?
                }
            };
            let id = content_hash(&(
                "causal-market-boundary-v3",
                &self.run_id,
                (scope.provider, scope.instrument, scope.session),
                runtime.configuration_hash(),
                self.sequence,
                pending.evaluated_at_ns,
                kind_hash,
            ))?;
            if id != pending.id {
                return Err(Error::Conflict("recovered pending boundary hash".into()));
            }
        }
        Ok(())
    }
}
