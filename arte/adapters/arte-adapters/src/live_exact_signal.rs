//! In-process exact-bar and first-squeeze consumer of causal scheduler boundaries.
//! It is calculation state, not an order or feed-health authority. Its image
//! must be committed with the matching scheduler cut for recovery.
use arte_core::{
    event_order::Scope,
    exact_bars::{Builder, Mode as BarMode, INTERVAL_NS},
    market_structure::scheduler::{Boundary, Kind},
    seed_storage::Object,
    strategy350_signal::{Config, Mode as SignalMode, Occurrence, State},
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub struct Bundle {
    pub root: Object,
    pub bars: Object,
    pub signal: Object,
}
impl Bundle {
    pub fn references(root: &Object) -> Result<(String, String)> {
        root.verify()?;
        if root.payload.len() > 4096 {
            return Err(Error::Capacity("exact signal root bytes".into()));
        }
        let saved: Saved = serde_json::from_slice(&root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if saved.version != 1 || !valid_hash(&saved.bars) || !valid_hash(&saved.signal) {
            return Err(Error::Invalid("exact signal root references".into()));
        }
        Ok((saved.bars, saved.signal))
    }
}
fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    version: u32,
    configuration_hash: String,
    scope: (u16, u64, u32),
    last_sequence: u64,
    last_boundary_id: Option<String>,
    last_evaluated_at_ns: u64,
    bars: String,
    signal: String,
}
pub struct Recovery<'a> {
    pub scope: Scope,
    pub session_start_ns: u64,
    pub session_end_ns: u64,
    pub price_scale: u8,
    pub size_scale: u8,
    pub source_generation_hash: &'a str,
    pub signal_config: Config,
    pub expected_sequence: u64,
    pub expected_boundary_id: Option<&'a str>,
}

pub struct Owner {
    bars: Builder,
    signal: State,
    last_sequence: u64,
    last_boundary_id: Option<String>,
    last_evaluated_at_ns: u64,
    failed: bool,
}
impl Owner {
    pub fn new(bars: Builder, signal: State) -> Result<Self> {
        if !bars.is_pristine() || !signal.is_pristine() {
            return Err(Error::Conflict(
                "new exact signal owner needs session start".into(),
            ));
        }
        Self::from_parts(bars, signal)
    }
    fn from_parts(bars: Builder, signal: State) -> Result<Self> {
        if bars.mode() != BarMode::Live
            || signal.mode() != SignalMode::Live
            || bars.configuration_hash() != signal.scope_hash()
            || bars.closed_through_ns() / INTERVAL_NS * INTERVAL_NS != signal.next_bucket_ns()
        {
            return Err(Error::Conflict(
                "exact signal source or clock differs".into(),
            ));
        }
        Ok(Self {
            bars,
            signal,
            last_sequence: 0,
            last_boundary_id: None,
            last_evaluated_at_ns: 0,
            failed: false,
        })
    }
    pub fn scope(&self) -> arte_core::event_order::Scope {
        self.bars.scope()
    }
    pub fn configuration_hash(&self) -> Result<String> {
        arte_core::content_hash(&(
            "arte.exact-signal-configuration.v1",
            self.bars.configuration_hash(),
            self.signal.config_hash()?,
        ))
    }
    pub fn first_occurrence(&self) -> Option<Occurrence> {
        self.signal.first_occurrence()
    }
    pub fn last_boundary(&self) -> (u64, Option<&str>) {
        (self.last_sequence, self.last_boundary_id.as_deref())
    }
    pub fn last_evaluated_at_ns(&self) -> u64 {
        self.last_evaluated_at_ns
    }
    pub fn checkpoint(&self) -> Result<Bundle> {
        if self.failed || (self.last_sequence == 0) != self.last_boundary_id.is_none() {
            return Err(Error::Unready(
                "exact signal checkpoint boundary unavailable".into(),
            ));
        }
        let bars = self.bars.checkpoint()?;
        let signal = self.signal.checkpoint()?;
        let scope = self.bars.scope();
        let saved = Saved {
            version: 1,
            configuration_hash: self.bars.configuration_hash().into(),
            scope: (scope.provider, scope.instrument, scope.session),
            last_sequence: self.last_sequence,
            last_boundary_id: self.last_boundary_id.clone(),
            last_evaluated_at_ns: self.last_evaluated_at_ns,
            bars: bars.id.clone(),
            signal: signal.id.clone(),
        };
        let payload =
            serde_json::to_vec(&saved).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload.len() > 4096 {
            return Err(Error::Capacity("exact signal root bytes".into()));
        }
        Ok(Bundle {
            root: Object::new(payload),
            bars,
            signal,
        })
    }
    pub fn restore(bundle: &Bundle, expected_root: &str, request: Recovery<'_>) -> Result<Self> {
        for object in [&bundle.root, &bundle.bars, &bundle.signal] {
            object.verify()?;
            if object.payload.len() > 4096 {
                return Err(Error::Capacity("exact signal recovery bytes".into()));
            }
        }
        let saved: Saved = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if bundle.root.id != expected_root
            || saved.version != 1
            || saved.scope
                != (
                    request.scope.provider,
                    request.scope.instrument,
                    request.scope.session,
                )
            || saved.last_sequence != request.expected_sequence
            || saved.last_boundary_id.as_deref() != request.expected_boundary_id
            || (saved.last_sequence == 0) != saved.last_boundary_id.is_none()
            || (saved.last_sequence == 0) != (saved.last_evaluated_at_ns == 0)
            || saved
                .last_boundary_id
                .as_ref()
                .is_some_and(String::is_empty)
            || saved.bars != bundle.bars.id
            || saved.signal != bundle.signal.id
        {
            return Err(Error::Conflict(
                "exact signal root or boundary differs".into(),
            ));
        }
        let bars = Builder::restore(
            &bundle.bars,
            request.scope,
            BarMode::Live,
            request.session_start_ns,
            request.session_end_ns,
            request.price_scale,
            request.size_scale,
            request.source_generation_hash.into(),
        )?;
        if saved.configuration_hash != bars.configuration_hash() {
            return Err(Error::Conflict(
                "exact signal source generation differs".into(),
            ));
        }
        let signal = State::restore(
            &bundle.signal,
            bars.configuration_hash(),
            request.signal_config,
            request.session_start_ns,
            request.session_end_ns,
            SignalMode::Live,
        )?;
        let mut owner = Self::from_parts(bars, signal)?;
        owner.last_sequence = saved.last_sequence;
        owner.last_boundary_id = saved.last_boundary_id;
        owner.last_evaluated_at_ns = saved.last_evaluated_at_ns;
        Ok(owner)
    }
    /// Consume exactly once after the scheduler prepares a boundary. A repeat
    /// read of the same unacknowledged boundary is idempotent.
    pub fn observe(&mut self, boundary: &Boundary<'_>) -> Result<Option<Occurrence>> {
        if self.failed {
            return Err(Error::Unready(
                "exact signal owner requires recovery".into(),
            ));
        }
        if boundary.sequence == self.last_sequence
            && self.last_boundary_id.as_deref() == Some(boundary.id)
        {
            return Ok(self.signal.first_occurrence());
        }
        if boundary.id.is_empty()
            || self.last_sequence.checked_add(1) != Some(boundary.sequence)
            || boundary.evaluated_at_ns < self.last_evaluated_at_ns
        {
            self.failed = true;
            return Err(Error::Conflict("exact signal boundary sequence".into()));
        }
        let result = (|| {
            let cutoff_ns = match &boundary.kind {
                Kind::Trade { observation, .. } | Kind::Quote { observation } => {
                    observation.validate()?;
                    if observation.key.provider != self.bars.scope().provider
                        || observation.key.instrument != self.bars.scope().instrument
                        || observation.key.session != self.bars.scope().session
                        || observation.receipt.is_none()
                    {
                        return Err(Error::Conflict(
                            "exact signal market scope or receipt".into(),
                        ));
                    }
                    observation.sip.ns / INTERVAL_NS * INTERVAL_NS
                }
                Kind::Completed {
                    bar, interval_ns, ..
                } => {
                    if *interval_ns == 0
                        || bar.bar.end_ns <= bar.bar.start_ns
                        || bar.bar.end_ns - bar.bar.start_ns != *interval_ns
                    {
                        return Err(Error::Conflict("exact signal completed boundary".into()));
                    }
                    bar.bar.end_ns
                }
            };
            let advance = self.bars.advance(cutoff_ns)?;
            if let Kind::Completed {
                bar,
                interval_ns: INTERVAL_NS,
                ..
            } = &boundary.kind
            {
                if advance.completed.as_ref().is_none_or(|exact| {
                    exact.start_ns != bar.bar.start_ns
                        || exact.end_ns != bar.bar.end_ns
                        || exact.trades != bar.bar.trades
                }) {
                    return Err(Error::Conflict(
                        "exact and scheduler 100 ms bars differ".into(),
                    ));
                }
            }
            self.signal
                .observe_live_advance(&advance, boundary.evaluated_at_ns)?;
            if let Kind::Trade {
                observation,
                eligible,
            } = &boundary.kind
            {
                self.bars.trade(observation, *eligible)?;
            }
            Ok(self.signal.first_occurrence())
        })();
        if result.is_err() {
            self.failed = true;
            return result;
        }
        self.last_sequence = boundary.sequence;
        self.last_boundary_id = Some(boundary.id.into());
        self.last_evaluated_at_ns = boundary.evaluated_at_ns;
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{
        event_order::Scope,
        events::{Decimal, EventKey, EventKind, Observation, Payload, Receipt, SourceTime},
        market::{Bar, Completed},
        strategy350_signal::Config,
    };
    const S: u64 = 1_000_000_000;
    fn scope() -> Scope {
        Scope {
            provider: 1,
            instrument: 10,
            session: 20260922,
        }
    }
    fn observation(
        sequence: u64,
        at: u64,
        kind: EventKind,
        price: &str,
        size: &str,
    ) -> Observation {
        let price = Decimal::parse(price).unwrap();
        let size = Decimal::parse(size).unwrap();
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 10,
                session: 20260922,
                kind,
                sequence,
            },
            payload: match kind {
                EventKind::Trade => Payload::Trade {
                    price,
                    size,
                    exchange: 1,
                    trade_id: format!("t{sequence}"),
                    trf: None,
                    conditions: vec![],
                    correction: None,
                },
                EventKind::Quote => Payload::Quote {
                    bid: price,
                    ask: price,
                    bid_size: size,
                    ask_size: size,
                    bid_exchange: 1,
                    ask_exchange: 1,
                    conditions: vec![],
                    indicators: vec![],
                },
            },
            sip: SourceTime {
                ns: at,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: at + 1,
            receipt: Some(Receipt {
                run_id: "run".into(),
                lane: 1,
                sequence,
                utc_ns: at + 1,
                monotonic_ns: sequence,
            }),
        }
    }
    fn owner() -> Owner {
        let bars = Builder::new(
            scope(),
            BarMode::Live,
            S,
            S + 400_000_000,
            2,
            0,
            "a".repeat(64),
        )
        .unwrap();
        let signal = State::new_live(
            Config {
                minimum_move_bps: 5,
                source_algorithm_hash: "b".repeat(64),
            },
            bars.configuration_hash().into(),
            S,
            S + 400_000_000,
        )
        .unwrap();
        Owner::new(bars, signal).unwrap()
    }
    #[test]
    fn causal_boundaries_open_exact_signal_without_false_empty_bars() {
        let mut owner = owner();
        let first = observation(1, S + 1, EventKind::Trade, "100", "100");
        let boundary = Boundary {
            id: "one",
            sequence: 1,
            evaluated_at_ns: S + 10_000_000,
            kind: Kind::Trade {
                observation: &first,
                eligible: true,
            },
        };
        assert!(owner.observe(&boundary).unwrap().is_none());
        let image = owner.checkpoint().unwrap();
        let generation = "a".repeat(64);
        let request = |id| Recovery {
            scope: scope(),
            session_start_ns: S,
            session_end_ns: S + 400_000_000,
            price_scale: 2,
            size_scale: 0,
            source_generation_hash: &generation,
            signal_config: Config {
                minimum_move_bps: 5,
                source_algorithm_hash: "b".repeat(64),
            },
            expected_sequence: 1,
            expected_boundary_id: Some(id),
        };
        let mut restored = Owner::restore(&image, &image.root.id, request("one")).unwrap();
        assert!(restored.observe(&boundary).unwrap().is_none());
        assert!(Owner::restore(&image, &image.root.id, request("wrong")).is_err());
        let mut damaged = owner.checkpoint().unwrap();
        damaged.bars.payload[0] ^= 1;
        assert!(Owner::restore(&damaged, &damaged.root.id, request("one")).is_err());
        let quote = observation(2, S + 200_000_000, EventKind::Quote, "100", "1");
        let boundary = Boundary {
            id: "two",
            sequence: 2,
            evaluated_at_ns: S + 210_000_000,
            kind: Kind::Quote {
                observation: &quote,
            },
        };
        assert!(owner.observe(&boundary).unwrap().is_none());
        assert!(restored.observe(&boundary).unwrap().is_none());
        assert_eq!(
            owner.checkpoint().unwrap().root.id,
            restored.checkpoint().unwrap().root.id
        );
        for (sequence, at, id) in [(3, S + 200_000_001, "three"), (4, S + 200_000_002, "four")] {
            let event = observation(sequence, at, EventKind::Trade, "100.05", "60");
            let boundary = Boundary {
                id,
                sequence,
                evaluated_at_ns: S + 220_000_000,
                kind: Kind::Trade {
                    observation: &event,
                    eligible: true,
                },
            };
            assert!(owner.observe(&boundary).unwrap().is_none());
        }
        let completed = Completed {
            bar: Bar {
                start_ns: S + 200_000_000,
                end_ns: S + 300_000_000,
                open: 100.05,
                high: 100.05,
                low: 100.05,
                close: 100.05,
                volume: 120.,
                notional: 12_006.,
                trades: 2,
            },
            macd: (0., 0., 0.),
            session_vwap: 100.0,
            session_high: 100.05,
            prior_session_high: Some(100.),
        };
        let boundary = Boundary {
            id: "five",
            sequence: 5,
            evaluated_at_ns: S + 310_000_000,
            kind: Kind::Completed {
                interval_ns: INTERVAL_NS,
                bar: &completed,
                available_at_ns: S + 310_000_000,
            },
        };
        let occurrence = owner.observe(&boundary).unwrap().unwrap();
        assert_eq!(occurrence.event_time_ns, S + 300_000_000);
        assert_eq!(occurrence.available_at_ns, Some(S + 310_000_000));
        assert_eq!(owner.observe(&boundary).unwrap(), Some(occurrence));
        assert_eq!(owner.last_boundary(), (5, Some("five")));
        let changed = Boundary {
            id: "changed",
            ..boundary
        };
        assert!(owner.observe(&changed).is_err());
        assert!(owner.observe(&changed).is_err());
    }
}
