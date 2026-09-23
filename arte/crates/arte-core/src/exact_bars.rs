//! Exact compact trade bars for live and historical calculations. Watermarks
//! are supplied by the ordered market owner; silence alone certifies nothing.
use crate::{
    content_hash,
    event_order::Scope,
    events::{EventKind, Observation, Payload},
    seed_storage::Object,
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub const INTERVAL_NS: u64 = 100_000_000;
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Historical,
    Live,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bar {
    pub start_ns: u64,
    pub end_ns: u64,
    pub price_scale: u8,
    pub size_scale: u8,
    pub open: i64,
    pub high: i64,
    pub low: i64,
    pub close: i64,
    pub volume: i64,
    pub notional: i128,
    pub trades: u64,
    pub last_trade_source_ns: u64,
    /// Absent in historical data. Not the bar's completion/availability time.
    pub last_trade_live_receipt_ns: Option<u64>,
}
pub struct Advance<'a> {
    pub configuration_hash: &'a str,
    pub previous_watermark_ns: u64,
    pub watermark_ns: u64,
    pub completed: Option<Bar>,
}
pub struct Builder {
    scope: Scope,
    configuration_hash: String,
    mode: Mode,
    session_start_ns: u64,
    session_end_ns: u64,
    price_scale: u8,
    size_scale: u8,
    closed_through_ns: u64,
    last_order: Option<(u64, u64)>,
    current: Option<Bar>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    version: u32,
    configuration_hash: String,
    scope: (u16, u64, u32),
    mode: Mode,
    session_start_ns: u64,
    session_end_ns: u64,
    price_scale: u8,
    size_scale: u8,
    closed_through_ns: u64,
    last_order: Option<(u64, u64)>,
    current: Option<Bar>,
}
impl Builder {
    pub fn new(
        scope: Scope,
        mode: Mode,
        session_start_ns: u64,
        session_end_ns: u64,
        price_scale: u8,
        size_scale: u8,
        source_generation_hash: String,
    ) -> Result<Self> {
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
            || session_start_ns == 0
            || session_start_ns >= session_end_ns
            || !session_start_ns.is_multiple_of(INTERVAL_NS)
            || !session_end_ns.is_multiple_of(INTERVAL_NS)
            || price_scale > 9
            || size_scale > 9
            || source_generation_hash.len() != 64
            || !source_generation_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("exact-bar source scope or scales".into()));
        }
        let configuration_hash = content_hash(&(
            "arte.exact-bars.v1",
            scope.provider,
            scope.instrument,
            scope.session,
            mode,
            session_start_ns,
            session_end_ns,
            price_scale,
            size_scale,
            source_generation_hash,
        ))?;
        Ok(Self {
            scope,
            configuration_hash,
            mode,
            session_start_ns,
            session_end_ns,
            price_scale,
            size_scale,
            closed_through_ns: session_start_ns,
            last_order: None,
            current: None,
        })
    }
    pub fn closed_through_ns(&self) -> u64 {
        self.closed_through_ns
    }
    pub fn configuration_hash(&self) -> &str {
        &self.configuration_hash
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
    pub fn mode(&self) -> Mode {
        self.mode
    }
    pub fn current(&self) -> Option<&Bar> {
        self.current.as_ref()
    }
    pub fn is_pristine(&self) -> bool {
        self.closed_through_ns == self.session_start_ns
            && self.last_order.is_none()
            && self.current.is_none()
    }
    pub fn checkpoint(&self) -> Result<Object> {
        let saved = Saved {
            version: 1,
            configuration_hash: self.configuration_hash.clone(),
            scope: (
                self.scope.provider,
                self.scope.instrument,
                self.scope.session,
            ),
            mode: self.mode,
            session_start_ns: self.session_start_ns,
            session_end_ns: self.session_end_ns,
            price_scale: self.price_scale,
            size_scale: self.size_scale,
            closed_through_ns: self.closed_through_ns,
            last_order: self.last_order,
            current: self.current.clone(),
        };
        let payload =
            serde_json::to_vec(&saved).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload.len() > 4096 {
            return Err(Error::Capacity("exact-bar checkpoint bytes".into()));
        }
        Ok(Object::new(payload))
    }
    #[allow(clippy::too_many_arguments)]
    pub fn restore(
        object: &Object,
        scope: Scope,
        mode: Mode,
        session_start_ns: u64,
        session_end_ns: u64,
        price_scale: u8,
        size_scale: u8,
        source_generation_hash: String,
    ) -> Result<Self> {
        object.verify()?;
        if object.payload.len() > 4096 {
            return Err(Error::Capacity("exact-bar checkpoint bytes".into()));
        }
        let saved: Saved = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let mut builder = Self::new(
            scope,
            mode,
            session_start_ns,
            session_end_ns,
            price_scale,
            size_scale,
            source_generation_hash,
        )?;
        if saved.version != 1
            || saved.configuration_hash != builder.configuration_hash
            || saved.scope != (scope.provider, scope.instrument, scope.session)
            || saved.mode != mode
            || saved.session_start_ns != session_start_ns
            || saved.session_end_ns != session_end_ns
            || saved.price_scale != price_scale
            || saved.size_scale != size_scale
            || saved.closed_through_ns < session_start_ns
            || saved.closed_through_ns > session_end_ns
            || saved
                .last_order
                .is_some_and(|(at, _)| at < session_start_ns || at >= session_end_ns)
            || saved.current.as_ref().is_some_and(|bar| {
                bar.start_ns < session_start_ns
                    || bar.start_ns >= session_end_ns
                    || !bar.start_ns.is_multiple_of(INTERVAL_NS)
                    || bar.start_ns.checked_add(INTERVAL_NS) != Some(bar.end_ns)
                    || bar.start_ns > saved.closed_through_ns
                    || saved.closed_through_ns >= bar.end_ns
                    || bar.price_scale != price_scale
                    || bar.size_scale != size_scale
                    || bar.open <= 0
                    || bar.high < bar.open.max(bar.close)
                    || bar.low <= 0
                    || bar.low > bar.open.min(bar.close)
                    || bar.volume <= 0
                    || bar.notional <= 0
                    || bar.trades == 0
                    || bar.last_trade_source_ns < bar.start_ns
                    || bar.last_trade_source_ns >= bar.end_ns
                    || saved
                        .last_order
                        .is_none_or(|(at, _)| at < bar.last_trade_source_ns || at >= bar.end_ns)
                    || (mode == Mode::Live) != bar.last_trade_live_receipt_ns.is_some()
            })
            || (saved.current.is_some() && saved.last_order.is_none())
        {
            return Err(Error::Conflict(
                "exact-bar checkpoint scope or geometry".into(),
            ));
        }
        builder.closed_through_ns = saved.closed_through_ns;
        builder.last_order = saved.last_order;
        builder.current = saved.current;
        Ok(builder)
    }
    /// `eligible` is the result of the pinned trade-condition policy. An
    /// ineligible trade advances source order but cannot alter a bar.
    pub fn trade(&mut self, event: &Observation, eligible: bool) -> Result<()> {
        event.validate()?;
        if event.key.provider != self.scope.provider
            || event.key.instrument != self.scope.instrument
            || event.key.session != self.scope.session
            || event.key.kind != EventKind::Trade
            || (self.mode == Mode::Live) != event.receipt.is_some()
            || event.sip.ns < self.session_start_ns
            || event.sip.ns >= self.session_end_ns
            || event.sip.ns < self.closed_through_ns
            || self
                .last_order
                .is_some_and(|last| (event.sip.ns, event.key.sequence) <= last)
        {
            return Err(Error::Conflict(
                "exact-bar trade scope, clock or order".into(),
            ));
        }
        let start_ns = event.sip.ns / INTERVAL_NS * INTERVAL_NS;
        if start_ns > self.closed_through_ns
            || self
                .current
                .as_ref()
                .is_some_and(|bar| bar.start_ns != start_ns)
        {
            return Err(Error::Unready(
                "certify and close preceding exact-bar buckets".into(),
            ));
        }
        if !eligible {
            self.last_order = Some((event.sip.ns, event.key.sequence));
            return Ok(());
        }
        let Payload::Trade { price, size, .. } = &event.payload else {
            return Err(Error::Invalid("exact bar needs trade payload".into()));
        };
        let price = price.atoms_at_scale(self.price_scale)?;
        let size = size.atoms_at_scale(self.size_scale)?;
        let end_ns = start_ns + INTERVAL_NS;
        let next = if let Some(prior) = &self.current {
            let volume = prior
                .volume
                .checked_add(size)
                .ok_or_else(|| Error::Capacity("exact-bar volume overflow".into()))?;
            let notional = prior
                .notional
                .checked_add(i128::from(price) * i128::from(size))
                .ok_or_else(|| Error::Capacity("exact-bar notional overflow".into()))?;
            let trades = prior
                .trades
                .checked_add(1)
                .ok_or_else(|| Error::Capacity("exact-bar trade count overflow".into()))?;
            Bar {
                high: prior.high.max(price),
                low: prior.low.min(price),
                close: price,
                volume,
                notional,
                trades,
                last_trade_source_ns: event.sip.ns,
                last_trade_live_receipt_ns: event.receipt.as_ref().map(|r| r.utc_ns),
                ..prior.clone()
            }
        } else {
            Bar {
                start_ns,
                end_ns,
                price_scale: self.price_scale,
                size_scale: self.size_scale,
                open: price,
                high: price,
                low: price,
                close: price,
                volume: size,
                notional: i128::from(price) * i128::from(size),
                trades: 1,
                last_trade_source_ns: event.sip.ns,
                last_trade_live_receipt_ns: event.receipt.as_ref().map(|r| r.utc_ns),
            }
        };
        self.current = Some(next);
        self.last_order = Some((event.sip.ns, event.key.sequence));
        Ok(())
    }
    /// A trusted event-time watermark seals every bucket ending at or before
    /// it. Empty gaps are represented by the covered interval, not fake bars.
    pub fn advance(&mut self, watermark_ns: u64) -> Result<Advance<'_>> {
        if watermark_ns < self.closed_through_ns || watermark_ns > self.session_end_ns {
            return Err(Error::Invalid(
                "exact-bar watermark rewind or session escape".into(),
            ));
        }
        let previous_watermark_ns = self.closed_through_ns;
        let completed = if self
            .current
            .as_ref()
            .is_some_and(|bar| bar.end_ns <= watermark_ns)
        {
            self.current.take()
        } else {
            None
        };
        self.closed_through_ns = watermark_ns;
        Ok(Advance {
            configuration_hash: &self.configuration_hash,
            previous_watermark_ns,
            watermark_ns,
            completed,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{Decimal, EventKey, Receipt, SourceTime};
    const S: u64 = 1_000_000_000;
    fn scope() -> Scope {
        Scope {
            provider: 1,
            instrument: 10,
            session: 20260922,
        }
    }
    fn trade(sequence: u64, at: u64, price: &str, size: &str, live: bool) -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 10,
                session: 20260922,
                kind: EventKind::Trade,
                sequence,
            },
            payload: Payload::Trade {
                price: Decimal::parse(price).unwrap(),
                size: Decimal::parse(size).unwrap(),
                exchange: 1,
                trade_id: format!("t{sequence}"),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns: at,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: at + 1,
            receipt: live.then(|| Receipt {
                run_id: "run".into(),
                lane: 1,
                sequence,
                utc_ns: at + 1,
                monotonic_ns: sequence,
            }),
        }
    }
    #[test]
    fn exact_atoms_and_certified_empty_gap_do_not_fabricate_bars() {
        let mut bars = Builder::new(
            scope(),
            Mode::Historical,
            S,
            S + 500_000_000,
            2,
            2,
            "a".repeat(64),
        )
        .unwrap();
        bars.trade(&trade(1, S + 1, "10.00", "1.25", false), true)
            .unwrap();
        bars.trade(&trade(2, S + 2, "10.05", "0.75", false), true)
            .unwrap();
        assert!(bars.advance(S + 99_000_000).unwrap().completed.is_none());
        let first = bars.advance(S + 100_000_000).unwrap().completed.unwrap();
        assert_eq!(
            (first.open, first.high, first.low, first.close),
            (1000, 1005, 1000, 1005)
        );
        assert_eq!(
            (first.volume, first.notional, first.trades),
            (200, 200_375, 2)
        );
        assert_eq!(first.last_trade_live_receipt_ns, None);
        assert!(bars
            .trade(&trade(3, S + 300_000_001, "10.10", "1", false), true)
            .is_err());
        let empty = bars.advance(S + 300_000_000).unwrap();
        assert_eq!(empty.previous_watermark_ns, S + 100_000_000);
        assert!(empty.completed.is_none());
        bars.trade(&trade(3, S + 300_000_001, "10.10", "1", false), true)
            .unwrap();
        assert_eq!(
            bars.advance(S + 400_000_000)
                .unwrap()
                .completed
                .unwrap()
                .close,
            1010
        );
        assert!(bars.advance(S + 399_000_000).is_err());
    }
    #[test]
    fn live_receipt_precision_and_source_order_fail_before_mutation() {
        let mut bars = Builder::new(
            scope(),
            Mode::Live,
            S,
            S + 300_000_000,
            2,
            0,
            "a".repeat(64),
        )
        .unwrap();
        assert!(bars
            .trade(&trade(1, S + 1, "10", "1", false), true)
            .is_err());
        assert!(bars
            .trade(&trade(1, S + 1, "10.005", "1", true), true)
            .is_err());
        bars.trade(&trade(1, S + 1, "10.01", "1", true), true)
            .unwrap();
        assert_eq!(
            bars.current().unwrap().last_trade_live_receipt_ns,
            Some(S + 2)
        );
        assert!(bars
            .trade(&trade(1, S + 1, "10.01", "1", true), true)
            .is_err());
        bars.trade(&trade(2, S + 2, "10.02", "1", true), false)
            .unwrap();
        assert_eq!(bars.current().unwrap().trades, 1);
        bars.advance(S + 100_000_000).unwrap();
        assert!(bars
            .trade(&trade(3, S + 3, "10.03", "1", true), true)
            .is_err());
        assert_eq!(bars.closed_through_ns(), S + 100_000_000);
    }
    #[test]
    fn aggregate_overflow_is_atomic() {
        let mut bars = Builder::new(
            scope(),
            Mode::Historical,
            S,
            S + 100_000_000,
            0,
            0,
            "a".repeat(64),
        )
        .unwrap();
        bars.trade(&trade(1, S + 1, "1", &i64::MAX.to_string(), false), true)
            .unwrap();
        let prior = bars.current().unwrap().clone();
        assert!(bars.trade(&trade(2, S + 2, "1", "1", false), true).is_err());
        assert_eq!(bars.current().unwrap(), &prior);
    }
    #[test]
    fn exact_live_bars_drive_first_squeeze_after_certified_empty_bucket() {
        use crate::strategy350_signal::{Config, State};
        let mut bars = Builder::new(
            scope(),
            Mode::Live,
            S,
            S + 400_000_000,
            2,
            0,
            "a".repeat(64),
        )
        .unwrap();
        let mut signal = State::new_live(
            Config {
                minimum_move_bps: 5,
                source_algorithm_hash: "b".repeat(64),
            },
            bars.configuration_hash().into(),
            S,
            S + 400_000_000,
        )
        .unwrap();
        bars.trade(&trade(1, S + 1, "100", "100", true), true)
            .unwrap();
        assert!(!signal
            .observe_live_advance(&bars.advance(S + 100_000_000).unwrap(), S + 110_000_000)
            .unwrap());
        assert!(!signal
            .observe_live_advance(&bars.advance(S + 200_000_000).unwrap(), S + 210_000_000)
            .unwrap());
        bars.trade(&trade(2, S + 200_000_001, "100.05", "60", true), true)
            .unwrap();
        bars.trade(&trade(3, S + 200_000_002, "100.05", "60", true), true)
            .unwrap();
        assert!(signal
            .observe_live_advance(&bars.advance(S + 300_000_000).unwrap(), S + 310_000_000)
            .unwrap());
        assert_eq!(
            signal.first_occurrence().unwrap().event_time_ns,
            S + 300_000_000
        );
        assert_eq!(
            signal.first_occurrence().unwrap().available_at_ns,
            Some(S + 310_000_000)
        );
        assert!(signal
            .observe_live_advance(&bars.advance(S + 400_000_000).unwrap(), S + 410_000_000)
            .unwrap());
        let mut wrong = State::new_live(
            Config {
                minimum_move_bps: 5,
                source_algorithm_hash: "b".repeat(64),
            },
            "c".repeat(64),
            S,
            S + 400_000_000,
        )
        .unwrap();
        assert!(wrong
            .observe_live_advance(&bars.advance(S + 400_000_000).unwrap(), S + 410_000_000)
            .is_err());
    }
    #[test]
    fn developing_exact_bar_restores_only_under_identical_source_and_geometry() {
        let generation = "a".repeat(64);
        let mut original = Builder::new(
            scope(),
            Mode::Live,
            S,
            S + 300_000_000,
            2,
            0,
            generation.clone(),
        )
        .unwrap();
        original
            .trade(&trade(1, S + 1, "10.01", "2", true), true)
            .unwrap();
        let image = original.checkpoint().unwrap();
        let restore = |object: &Object| {
            Builder::restore(
                object,
                scope(),
                Mode::Live,
                S,
                S + 300_000_000,
                2,
                0,
                generation.clone(),
            )
        };
        let mut recovered = restore(&image).unwrap();
        assert!(Builder::restore(
            &image,
            scope(),
            Mode::Historical,
            S,
            S + 300_000_000,
            2,
            0,
            generation.clone()
        )
        .is_err());
        assert!(Builder::restore(
            &image,
            scope(),
            Mode::Live,
            S,
            S + 300_000_000,
            3,
            0,
            generation.clone()
        )
        .is_err());
        assert!(Builder::restore(
            &image,
            scope(),
            Mode::Live,
            S,
            S + 300_000_000,
            2,
            0,
            "b".repeat(64)
        )
        .is_err());
        let next = trade(2, S + 2, "10.02", "3", true);
        original.trade(&next, true).unwrap();
        recovered.trade(&next, true).unwrap();
        assert_eq!(
            original.checkpoint().unwrap().id,
            recovered.checkpoint().unwrap().id
        );
        assert_eq!(
            original.advance(S + 100_000_000).unwrap().completed,
            recovered.advance(S + 100_000_000).unwrap().completed
        );
        let mut corrupt = image.clone();
        corrupt.payload[0] ^= 1;
        assert!(restore(&corrupt).is_err());
        let mut invalid: serde_json::Value = serde_json::from_slice(&image.payload).unwrap();
        invalid["current"]["end_ns"] = serde_json::json!(S + 500_000_000);
        assert!(restore(&Object::new(serde_json::to_vec(&invalid).unwrap())).is_err());
    }
}
