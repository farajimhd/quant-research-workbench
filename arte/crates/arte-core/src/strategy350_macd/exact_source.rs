//! Sparse completed 1s/5s/10s/30s closes from exact nonempty 100 ms trade bars.
//! A watermark seals existing buckets; it never invents an empty close.
use super::TIMEFRAMES_NS;
use crate::{
    content_hash,
    event_order::Scope,
    events::Decimal,
    exact_bars::{Bar, INTERVAL_NS},
    seed_storage::Object,
    Error, Result,
};
use serde::{Deserialize, Serialize};

const MAX_CHECKPOINT_BYTES: usize = 4096;
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Bucket {
    start_ns: u64,
    end_ns: u64,
    close_atoms: i64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CompletedInput {
    pub timeframe_ns: u64,
    pub end_ns: u64,
    pub close: Decimal,
}
#[derive(Clone)]
pub struct Source {
    scope: Scope,
    session_start_ns: u64,
    session_end_ns: u64,
    price_scale: u8,
    exact_bar_hash: String,
    watermark_ns: u64,
    buckets: [Option<Bucket>; 4],
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    version: u32,
    scope: (u16, u64, u32),
    session_start_ns: u64,
    session_end_ns: u64,
    price_scale: u8,
    exact_bar_hash: String,
    watermark_ns: u64,
    buckets: [Option<Bucket>; 4],
}
impl Source {
    pub fn new(
        scope: Scope,
        session_start_ns: u64,
        session_end_ns: u64,
        price_scale: u8,
        exact_bar_hash: String,
    ) -> Result<Self> {
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
            || session_start_ns == 0
            || session_start_ns >= session_end_ns
            || !session_start_ns.is_multiple_of(INTERVAL_NS)
            || !session_end_ns.is_multiple_of(INTERVAL_NS)
            || price_scale > 9
            || exact_bar_hash.len() != 64
            || !exact_bar_hash
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(Error::Invalid("Strategy 350 exact MACD source".into()));
        }
        Ok(Self {
            scope,
            session_start_ns,
            session_end_ns,
            price_scale,
            exact_bar_hash,
            watermark_ns: session_start_ns,
            buckets: [None; 4],
        })
    }
    pub fn scope(&self) -> Scope {
        self.scope
    }
    pub fn session_bounds(&self) -> (u64, u64) {
        (self.session_start_ns, self.session_end_ns)
    }
    pub fn price_scale(&self) -> u8 {
        self.price_scale
    }
    pub fn exact_bar_hash(&self) -> &str {
        &self.exact_bar_hash
    }
    pub fn watermark_ns(&self) -> u64 {
        self.watermark_ns
    }
    pub fn identity_hash(&self) -> Result<String> {
        content_hash(&(
            "arte.strategy-350-exact-macd-source.v1",
            self.scope.provider,
            self.scope.instrument,
            self.scope.session,
            self.session_start_ns,
            self.session_end_ns,
            self.price_scale,
            self.exact_bar_hash.as_str(),
        ))
    }
    pub fn checkpoint(&self) -> Result<Object> {
        self.validate_recovery_geometry()?;
        let payload = serde_json::to_vec(&Saved {
            version: 1,
            scope: (
                self.scope.provider,
                self.scope.instrument,
                self.scope.session,
            ),
            session_start_ns: self.session_start_ns,
            session_end_ns: self.session_end_ns,
            price_scale: self.price_scale,
            exact_bar_hash: self.exact_bar_hash.clone(),
            watermark_ns: self.watermark_ns,
            buckets: self.buckets,
        })
        .map_err(|error| Error::Serialization(error.to_string()))?;
        if payload.len() > MAX_CHECKPOINT_BYTES {
            return Err(Error::Capacity("Strategy 350 MACD source image".into()));
        }
        Ok(Object::new(payload))
    }
    #[allow(clippy::too_many_arguments)]
    pub fn restore_checkpoint(
        image: &Object,
        expected_id: &str,
        scope: Scope,
        session_start_ns: u64,
        session_end_ns: u64,
        price_scale: u8,
        exact_bar_hash: String,
    ) -> Result<Self> {
        image.verify()?;
        if image.id != expected_id || image.payload.len() > MAX_CHECKPOINT_BYTES {
            return Err(Error::Conflict(
                "Strategy 350 MACD source image identity".into(),
            ));
        }
        let saved: Saved = serde_json::from_slice(&image.payload)
            .map_err(|error| Error::Serialization(error.to_string()))?;
        let expected = Self::new(
            scope,
            session_start_ns,
            session_end_ns,
            price_scale,
            exact_bar_hash,
        )?;
        if saved.version != 1
            || saved.scope != (scope.provider, scope.instrument, scope.session)
            || saved.session_start_ns != session_start_ns
            || saved.session_end_ns != session_end_ns
            || saved.price_scale != price_scale
            || saved.exact_bar_hash != expected.exact_bar_hash
        {
            return Err(Error::Conflict("Strategy 350 MACD source pin".into()));
        }
        let restored = Self {
            watermark_ns: saved.watermark_ns,
            buckets: saved.buckets,
            ..expected
        };
        restored.validate_recovery_geometry()?;
        if restored.checkpoint()?.payload != image.payload {
            return Err(Error::Conflict(
                "Strategy 350 MACD source noncanonical".into(),
            ));
        }
        Ok(restored)
    }
    fn validate_recovery_geometry(&self) -> Result<()> {
        if self.watermark_ns < self.session_start_ns || self.watermark_ns > self.session_end_ns {
            return Err(Error::Conflict("Strategy 350 MACD source watermark".into()));
        }
        for (index, bucket) in self.buckets.iter().enumerate() {
            if let Some(bucket) = bucket {
                let timeframe_ns = TIMEFRAMES_NS[index];
                if bucket.close_atoms <= 0
                    || !bucket.start_ns.is_multiple_of(timeframe_ns)
                    || bucket.end_ns.checked_sub(bucket.start_ns) != Some(timeframe_ns)
                    || bucket.end_ns <= self.watermark_ns
                    || bucket.end_ns > self.session_end_ns
                    || bucket.start_ns > self.watermark_ns
                    || bucket.end_ns <= self.session_start_ns
                {
                    return Err(Error::Conflict("Strategy 350 MACD source bucket".into()));
                }
            }
        }
        Ok(())
    }
    /// Bind a live/historical in-process exact-bar advance to its pinned
    /// source generation and contiguous watermark before taking any output.
    pub fn advance_verified(
        &mut self,
        advance: &crate::exact_bars::Advance<'_>,
    ) -> Result<Vec<CompletedInput>> {
        if advance.configuration_hash() != self.exact_bar_hash
            || advance.previous_watermark_ns() != self.watermark_ns
        {
            return Err(Error::Conflict("Strategy 350 MACD exact advance".into()));
        }
        self.advance(advance.completed(), advance.watermark_ns())
    }
    /// Invalid input leaves all timeframe buckets unchanged. The caller must
    /// supply a causal watermark from the complete ordered market authority.
    pub fn advance(&mut self, bar: Option<&Bar>, watermark_ns: u64) -> Result<Vec<CompletedInput>> {
        if watermark_ns < self.watermark_ns || watermark_ns > self.session_end_ns {
            return Err(Error::Conflict("Strategy 350 MACD watermark".into()));
        }
        if let Some(bar) = bar {
            if bar.price_scale != self.price_scale
                || bar.start_ns < self.watermark_ns
                || bar.start_ns < self.session_start_ns
                || bar.end_ns > watermark_ns
                || bar.end_ns.checked_sub(bar.start_ns) != Some(INTERVAL_NS)
                || bar.trades == 0
                || bar.notional <= 0
                || bar.last_trade_source_ns < bar.start_ns
                || bar.last_trade_source_ns >= bar.end_ns
                || bar.low <= 0
                || bar.low > bar.open
                || bar.low > bar.close
                || bar.high < bar.open
                || bar.high < bar.close
                || bar.close <= 0
            {
                return Err(Error::Conflict("Strategy 350 MACD exact bar".into()));
            }
        }
        self.advance_close(bar.map(|value| (value.start_ns, value.close)), watermark_ns)
    }
    /// Advance one certified dense compact-bar batch with a single staged
    /// source and sparse completed output. Historical bars have no measured
    /// trade receipt; this path must never invent one.
    pub(crate) fn advance_compact_batch(
        &mut self,
        first_start_ns: u64,
        present: &[bool],
        closes: &[i64],
        trades: &[u64],
    ) -> Result<Vec<CompletedInput>> {
        if present.is_empty()
            || present.len() != closes.len()
            || present.len() != trades.len()
            || first_start_ns != self.watermark_ns
            || !first_start_ns.is_multiple_of(INTERVAL_NS)
        {
            return Err(Error::Conflict("Strategy 350 compact MACD batch".into()));
        }
        let mut next = self.clone();
        let mut output = Vec::new();
        let mut batch_end_ns = first_start_ns;
        for (slot, (&has_bar, (&close, &trade_count))) in
            present.iter().zip(closes.iter().zip(trades)).enumerate()
        {
            let start_ns = (slot as u64)
                .checked_mul(INTERVAL_NS)
                .and_then(|offset| first_start_ns.checked_add(offset))
                .ok_or_else(|| Error::Capacity("Strategy 350 compact MACD clock".into()))?;
            let end_ns = start_ns
                .checked_add(INTERVAL_NS)
                .ok_or_else(|| Error::Capacity("Strategy 350 compact MACD end".into()))?;
            if end_ns > self.session_end_ns
                || (has_bar && (close <= 0 || trade_count == 0))
                || (!has_bar && trade_count != 0)
            {
                return Err(Error::Conflict("Strategy 350 compact MACD slot".into()));
            }
            if has_bar {
                next.advance_close_into(Some((start_ns, close)), end_ns, &mut output)?;
            }
            batch_end_ns = end_ns;
        }
        next.advance_close_into(None, batch_end_ns, &mut output)?;
        output.sort_unstable_by_key(|value| (value.end_ns, value.timeframe_ns));
        *self = next;
        Ok(output)
    }
    fn advance_close(
        &mut self,
        bar: Option<(u64, i64)>,
        watermark_ns: u64,
    ) -> Result<Vec<CompletedInput>> {
        let mut next = self.clone();
        let mut output = Vec::with_capacity(8);
        next.advance_close_into(bar, watermark_ns, &mut output)?;
        output.sort_unstable_by_key(|value| (value.end_ns, value.timeframe_ns));
        *self = next;
        Ok(output)
    }
    fn advance_close_into(
        &mut self,
        bar: Option<(u64, i64)>,
        watermark_ns: u64,
        output: &mut Vec<CompletedInput>,
    ) -> Result<()> {
        if let Some((bar_start_ns, bar_close)) = bar {
            self.seal_through(bar_start_ns, output);
            for (index, timeframe_ns) in TIMEFRAMES_NS.into_iter().enumerate() {
                let start_ns = bar_start_ns / timeframe_ns * timeframe_ns;
                let end_ns = start_ns
                    .checked_add(timeframe_ns)
                    .ok_or_else(|| Error::Capacity("Strategy 350 MACD bucket clock".into()))?;
                if end_ns > self.session_end_ns {
                    // A session shorter than this timeframe has no completed
                    // bucket. Never synthesize a partial completed bar.
                    continue;
                }
                match &mut self.buckets[index] {
                    Some(bucket) if bucket.start_ns == start_ns => {
                        bucket.close_atoms = bar_close;
                    }
                    Some(_) => {
                        return Err(Error::Conflict("Strategy 350 MACD bucket order".into()));
                    }
                    slot @ None => {
                        *slot = Some(Bucket {
                            start_ns,
                            end_ns,
                            close_atoms: bar_close,
                        });
                    }
                }
            }
        }
        self.seal_through(watermark_ns, output);
        self.watermark_ns = watermark_ns;
        Ok(())
    }
    fn seal_through(&mut self, at_ns: u64, output: &mut Vec<CompletedInput>) {
        for (index, slot) in self.buckets.iter_mut().enumerate() {
            if slot.as_ref().is_some_and(|bucket| bucket.end_ns <= at_ns) {
                let bucket = slot.take().expect("checked present bucket");
                output.push(CompletedInput {
                    timeframe_ns: TIMEFRAMES_NS[index],
                    end_ns: bucket.end_ns,
                    close: Decimal {
                        atoms: bucket.close_atoms,
                        scale: self.price_scale,
                    },
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const S: u64 = 1_000_000_000;
    fn source() -> Source {
        Source::new(
            Scope {
                provider: 1,
                instrument: 10,
                session: 20260922,
            },
            30 * S,
            120 * S,
            2,
            "a".repeat(64),
        )
        .unwrap()
    }
    fn bar(start_ns: u64, close: i64) -> Bar {
        Bar {
            start_ns,
            end_ns: start_ns + INTERVAL_NS,
            price_scale: 2,
            size_scale: 0,
            open: close,
            high: close,
            low: close,
            close,
            volume: 1,
            notional: i128::from(close),
            trades: 1,
            last_trade_source_ns: start_ns + 1,
            last_trade_live_receipt_ns: None,
        }
    }
    #[test]
    fn sealed_exact_bars_produce_sparse_multi_timeframe_closes() {
        let mut source = source();
        let first = bar(30 * S, 1000);
        assert!(source
            .advance(Some(&first), first.end_ns)
            .unwrap()
            .is_empty());
        let second = bar(31 * S, 1100);
        let one = source.advance(Some(&second), second.end_ns).unwrap();
        assert_eq!(one.len(), 1);
        assert_eq!(one[0].timeframe_ns, S);
        assert_eq!(one[0].close.atoms, 1000);
        let sealed = source.advance(None, 60 * S).unwrap();
        assert_eq!(sealed.len(), 4);
        assert_eq!(sealed.iter().filter(|x| x.timeframe_ns == S).count(), 1);
        assert_eq!(sealed.last().unwrap().timeframe_ns, 30 * S);
        assert!(source.advance(None, 90 * S).unwrap().is_empty());
        let later = bar(90 * S, 1200);
        let closed = source.advance(Some(&later), 91 * S).unwrap();
        assert_eq!(closed[0].close.atoms, 1200);
    }
    #[test]
    fn invalid_advance_does_not_mutate_source() {
        let mut source = source();
        let before = source.identity_hash().unwrap();
        let mut wrong = bar(30 * S, 1000);
        wrong.price_scale = 3;
        assert!(source.advance(Some(&wrong), wrong.end_ns).is_err());
        assert_eq!(source.watermark_ns(), 30 * S);
        assert_eq!(source.identity_hash().unwrap(), before);
        assert!(source.advance(None, 121 * S).is_err());
        assert_eq!(source.watermark_ns(), 30 * S);
    }
    #[test]
    fn compact_batch_matches_exact_slot_advance_and_rolls_back_on_error() {
        let mut batched = source();
        let mut exact = source();
        let mut present = vec![false; 300];
        let mut closes = vec![0; 300];
        let mut trades = vec![0; 300];
        for (slot, close) in [(0, 1000), (9, 1010), (10, 1020), (59, 1030), (299, 1040)] {
            present[slot] = true;
            closes[slot] = close;
            trades[slot] = 1;
        }
        let batch_output = batched
            .advance_compact_batch(30 * S, &present, &closes, &trades)
            .unwrap();
        let mut exact_output = Vec::new();
        for slot in 0..present.len() {
            let start = 30 * S + slot as u64 * INTERVAL_NS;
            let exact_bar = present[slot].then(|| bar(start, closes[slot]));
            exact_output.extend(
                exact
                    .advance(exact_bar.as_ref(), start + INTERVAL_NS)
                    .unwrap(),
            );
        }
        assert_eq!(batch_output, exact_output);
        assert_eq!(batched.watermark_ns(), 60 * S);
        let before = batched.identity_hash().unwrap();
        let mut invalid = vec![false; 300];
        let mut invalid_closes = vec![0; 300];
        let invalid_trades = vec![0; 300];
        invalid[200] = true;
        invalid_closes[200] = 500;
        assert!(batched
            .advance_compact_batch(60 * S, &invalid, &invalid_closes, &invalid_trades)
            .is_err());
        assert_eq!(batched.identity_hash().unwrap(), before);
        let empty = vec![false; 300];
        let zeros = vec![0; 300];
        let zero_trades = vec![0_u64; 300];
        assert!(batched
            .advance_compact_batch(60 * S, &empty, &zeros, &zero_trades)
            .unwrap()
            .is_empty());
        for slot in 0..300 {
            exact
                .advance(None, 60 * S + (slot + 1) * INTERVAL_NS)
                .unwrap();
        }
        assert_eq!(batched.watermark_ns(), exact.watermark_ns());
        assert_eq!(
            batched.identity_hash().unwrap(),
            exact.identity_hash().unwrap()
        );
    }
    #[test]
    fn verified_exact_advance_pins_source_generation_and_watermark() {
        use crate::events::{EventKey, EventKind, Observation, Payload, SourceTime};
        let mut builder = crate::exact_bars::Builder::new(
            source().scope(),
            crate::exact_bars::Mode::Historical,
            30 * S,
            120 * S,
            2,
            0,
            "b".repeat(64),
        )
        .unwrap();
        let event = Observation {
            key: EventKey {
                provider: 1,
                instrument: 10,
                session: 20260922,
                kind: EventKind::Trade,
                sequence: 1,
            },
            payload: Payload::Trade {
                price: Decimal {
                    atoms: 1000,
                    scale: 2,
                },
                size: Decimal { atoms: 1, scale: 0 },
                exchange: 1,
                trade_id: "one".into(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns: 30 * S + 1,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: 30 * S + 2,
            receipt: None,
        };
        builder.trade(&event, true).unwrap();
        let mut pinned = Source::new(
            builder.scope(),
            30 * S,
            120 * S,
            2,
            builder.configuration_hash().into(),
        )
        .unwrap();
        {
            let advance = builder.advance(30 * S + INTERVAL_NS).unwrap();
            let mut wrong = source();
            assert!(wrong.advance_verified(&advance).is_err());
            assert!(pinned.advance_verified(&advance).unwrap().is_empty());
        }
        let advance = builder.advance(60 * S).unwrap();
        assert_eq!(pinned.advance_verified(&advance).unwrap().len(), 4);
        assert!(pinned.advance_verified(&advance).is_err());
    }
    #[test]
    fn source_checkpoint_restores_sparse_buckets_and_rejects_changed_pin() {
        let mut continuous = source();
        let first = bar(30 * S, 1000);
        continuous.advance(Some(&first), first.end_ns).unwrap();
        let image = continuous.checkpoint().unwrap();
        let mut restored = Source::restore_checkpoint(
            &image,
            &image.id,
            continuous.scope(),
            30 * S,
            120 * S,
            2,
            "a".repeat(64),
        )
        .unwrap();
        let later = bar(59 * S, 1100);
        assert_eq!(
            continuous.advance(Some(&later), 60 * S).unwrap(),
            restored.advance(Some(&later), 60 * S).unwrap()
        );
        assert_eq!(
            continuous.checkpoint().unwrap().id,
            restored.checkpoint().unwrap().id
        );
        assert!(Source::restore_checkpoint(
            &image,
            &image.id,
            continuous.scope(),
            30 * S,
            120 * S,
            2,
            "b".repeat(64),
        )
        .is_err());
        let mut changed: serde_json::Value = serde_json::from_slice(&image.payload).unwrap();
        changed["watermark_ns"] = (121 * S).into();
        let changed = Object::new(serde_json::to_vec(&changed).unwrap());
        assert!(Source::restore_checkpoint(
            &changed,
            &changed.id,
            continuous.scope(),
            30 * S,
            120 * S,
            2,
            "a".repeat(64),
        )
        .is_err());
    }
    #[test]
    fn short_session_never_publishes_partial_large_timeframes() {
        let mut source = Source::new(source().scope(), S, 2 * S, 2, "a".repeat(64)).unwrap();
        let first = bar(S, 1000);
        assert!(source
            .advance(Some(&first), first.end_ns)
            .unwrap()
            .is_empty());
        let completed = source.advance(None, 2 * S).unwrap();
        assert_eq!(completed.len(), 1);
        assert_eq!(completed[0].timeframe_ns, S);
    }
}
