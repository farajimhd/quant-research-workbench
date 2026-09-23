//! In-process exact-bar and first-squeeze consumer of causal scheduler boundaries.
//! It is calculation state, not an order or feed-health authority. Its image
//! must be committed with the matching scheduler cut for recovery.
use arte_core::{
    event_order::Scope,
    exact_bars::{Bar as ExactBar, Builder, Mode as BarMode, INTERVAL_NS},
    market_structure::scheduler::{Boundary, Kind},
    seed_storage::Object,
    strategy350_macd::{self, exact_source::Source as MacdSource},
    strategy350_noise::{self, Source as NoiseSource},
    strategy350_signal::{Config, Mode as SignalMode, Occurrence, State},
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub struct Bundle {
    pub root: Object,
    pub bars: Object,
    pub signal: Object,
    pub noise: Object,
    pub macd: Object,
    pub macd_source: Object,
}
impl Bundle {
    pub fn references(root: &Object) -> Result<(String, String, String, String, String)> {
        root.verify()?;
        if root.payload.len() > 4096 {
            return Err(Error::Capacity("exact signal root bytes".into()));
        }
        let saved: Saved = serde_json::from_slice(&root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if saved.version != 3
            || !valid_hash(&saved.bars)
            || !valid_hash(&saved.signal)
            || !valid_hash(&saved.noise)
            || !valid_hash(&saved.macd)
            || !valid_hash(&saved.macd_source)
        {
            return Err(Error::Invalid("exact signal root references".into()));
        }
        Ok((
            saved.bars,
            saved.signal,
            saved.noise,
            saved.macd,
            saved.macd_source,
        ))
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
    last_exact_completed: Option<ExactBar>,
    source_1s: NoiseSource,
    bars: String,
    signal: String,
    noise: String,
    macd: String,
    macd_source: String,
}
pub struct Recovery<'a> {
    pub scope: Scope,
    pub session_start_ns: u64,
    pub session_end_ns: u64,
    pub price_scale: u8,
    pub size_scale: u8,
    pub source_generation_hash: &'a str,
    pub signal_config: Config,
    pub noise_config: strategy350_noise::Config,
    pub macd_config: strategy350_macd::Config,
    pub expected_sequence: u64,
    pub expected_boundary_id: Option<&'a str>,
}

pub struct Owner {
    bars: Builder,
    signal: State,
    noise: strategy350_noise::State,
    source_1s: NoiseSource,
    macd: strategy350_macd::State,
    macd_source: MacdSource,
    last_sequence: u64,
    last_boundary_id: Option<String>,
    last_evaluated_at_ns: u64,
    last_exact_completed: Option<ExactBar>,
    failed: bool,
}
impl Owner {
    pub fn new(
        bars: Builder,
        signal: State,
        noise: strategy350_noise::State,
        macd_config: &strategy350_macd::Config,
    ) -> Result<Self> {
        if !bars.is_pristine() || !signal.is_pristine() || !noise.is_pristine() {
            return Err(Error::Conflict(
                "new exact signal owner needs session start".into(),
            ));
        }
        let (start, end) = bars.session_bounds();
        let source_1s = NoiseSource::new(start, end, bars.price_scale())?;
        let macd = strategy350_macd::State::new(bars.scope(), start, end, macd_config)?;
        let macd_source = MacdSource::new(
            bars.scope(),
            start,
            end,
            bars.price_scale(),
            bars.configuration_hash().into(),
        )?;
        Self::from_parts(bars, signal, noise, source_1s, macd, macd_source)
    }
    fn from_parts(
        bars: Builder,
        signal: State,
        noise: strategy350_noise::State,
        source_1s: NoiseSource,
        macd: strategy350_macd::State,
        macd_source: MacdSource,
    ) -> Result<Self> {
        source_1s.verify()?;
        macd.require_source(&macd_source)?;
        if bars.mode() != BarMode::Live
            || signal.mode() != SignalMode::Live
            || bars.configuration_hash() != signal.scope_hash()
            || bars.closed_through_ns() / INTERVAL_NS * INTERVAL_NS != signal.next_bucket_ns()
            || bars.session_bounds() != noise.session_bounds()
            || bars.price_scale() != noise.price_scale()
            || noise.last_end_ns().unwrap_or(bars.session_bounds().0)
                != source_1s.last_closed_end_ns()
            || source_1s
                .latest_absorbed_end_ns()
                .is_some_and(|at| at > bars.closed_through_ns())
            || macd_source.exact_bar_hash() != bars.configuration_hash()
            || macd_source.watermark_ns() != bars.closed_through_ns()
        {
            return Err(Error::Conflict(
                "exact signal source or clock differs".into(),
            ));
        }
        Ok(Self {
            bars,
            signal,
            noise,
            source_1s,
            macd,
            macd_source,
            last_sequence: 0,
            last_boundary_id: None,
            last_evaluated_at_ns: 0,
            last_exact_completed: None,
            failed: false,
        })
    }
    pub fn scope(&self) -> arte_core::event_order::Scope {
        self.bars.scope()
    }
    pub fn configuration_hash(&self) -> Result<String> {
        arte_core::content_hash(&(
            "arte.exact-signal-configuration.v2",
            self.bars.configuration_hash(),
            self.signal.config_hash()?,
            self.noise.configuration_hash(),
            self.macd.config_hash(),
            self.macd_source.identity_hash()?,
        ))
    }
    pub fn first_occurrence(&self) -> Option<Occurrence> {
        self.signal.first_occurrence()
    }
    pub fn noise_distance(&self, entry_atoms: i64) -> Result<strategy350_noise::Distance> {
        self.noise.distance(entry_atoms)
    }
    pub fn forming_macd_for_boundary(
        &self,
        boundary: &Boundary<'_>,
    ) -> Result<strategy350_macd::Outcome> {
        if self.last_boundary() != (boundary.sequence, Some(boundary.id))
            || self.last_evaluated_at_ns != boundary.evaluated_at_ns
        {
            return Err(Error::Unready(
                "Strategy 350 MACD boundary not consumed".into(),
            ));
        }
        let Kind::Trade {
            observation,
            eligible: true,
        } = &boundary.kind
        else {
            return Err(Error::Unready(
                "Strategy 350 MACD needs eligible trade".into(),
            ));
        };
        let arte_core::events::Payload::Trade { price, .. } = &observation.payload else {
            return Err(Error::Conflict("Strategy 350 MACD trade payload".into()));
        };
        self.macd.preview_trade(
            observation.sip.ns,
            boundary.evaluated_at_ns,
            arte_core::events::Decimal {
                atoms: price.atoms_at_scale(self.bars.price_scale())?,
                scale: self.bars.price_scale(),
            },
        )
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
        let noise = self.noise.checkpoint(8 * 1024 * 1024)?;
        let macd = self.macd.checkpoint()?;
        let macd_source = self.macd_source.checkpoint()?;
        let scope = self.bars.scope();
        let saved = Saved {
            version: 3,
            configuration_hash: self.configuration_hash()?,
            scope: (scope.provider, scope.instrument, scope.session),
            last_sequence: self.last_sequence,
            last_boundary_id: self.last_boundary_id.clone(),
            last_evaluated_at_ns: self.last_evaluated_at_ns,
            last_exact_completed: self.last_exact_completed.clone(),
            source_1s: self.source_1s.clone(),
            bars: bars.id.clone(),
            signal: signal.id.clone(),
            noise: noise.id.clone(),
            macd: macd.id.clone(),
            macd_source: macd_source.id.clone(),
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
            noise,
            macd,
            macd_source,
        })
    }
    pub fn restore(bundle: &Bundle, expected_root: &str, request: Recovery<'_>) -> Result<Self> {
        for object in [
            &bundle.root,
            &bundle.bars,
            &bundle.signal,
            &bundle.macd,
            &bundle.macd_source,
        ] {
            object.verify()?;
            if object.payload.len() > 4096 {
                return Err(Error::Capacity("exact signal recovery bytes".into()));
            }
        }
        bundle.noise.verify()?;
        if bundle.noise.payload.len() > 8 * 1024 * 1024 {
            return Err(Error::Capacity("exact signal noise recovery bytes".into()));
        }
        let saved: Saved = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if bundle.root.id != expected_root
            || saved.version != 3
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
            || saved.noise != bundle.noise.id
            || saved.macd != bundle.macd.id
            || saved.macd_source != bundle.macd_source.id
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
        let signal = State::restore(
            &bundle.signal,
            bars.configuration_hash(),
            request.signal_config,
            request.session_start_ns,
            request.session_end_ns,
            SignalMode::Live,
        )?;
        let noise = strategy350_noise::State::restore_checkpoint(
            &bundle.noise,
            &bundle.noise.id,
            request.noise_config,
            request.session_start_ns,
            request.session_end_ns,
            8 * 1024 * 1024,
        )?;
        let macd = strategy350_macd::State::restore_checkpoint(
            &bundle.macd,
            &bundle.macd.id,
            request.scope,
            request.session_start_ns,
            request.session_end_ns,
            &request.macd_config,
        )?;
        let macd_source = MacdSource::restore_checkpoint(
            &bundle.macd_source,
            &bundle.macd_source.id,
            request.scope,
            request.session_start_ns,
            request.session_end_ns,
            request.price_scale,
            bars.configuration_hash().into(),
        )?;
        if saved.source_1s.session_start_ns() != request.session_start_ns
            || saved.source_1s.session_end_ns() != request.session_end_ns
            || saved.source_1s.price_scale() != request.price_scale
            || saved.source_1s.last_closed_end_ns() > bars.closed_through_ns()
        {
            return Err(Error::Conflict("exact signal noise source differs".into()));
        }
        if saved.last_exact_completed.as_ref().is_some_and(|last| {
            last.end_ns.checked_sub(last.start_ns) != Some(INTERVAL_NS)
                || last.end_ns > bars.closed_through_ns()
                || last.end_ns > signal.next_bucket_ns()
                || last.price_scale != request.price_scale
                || last.size_scale != request.size_scale
                || bars
                    .current()
                    .is_some_and(|current| current.start_ns < last.end_ns)
        }) {
            return Err(Error::Conflict("exact signal recent bar geometry".into()));
        }
        let mut owner = Self::from_parts(bars, signal, noise, saved.source_1s, macd, macd_source)?;
        if saved.configuration_hash != owner.configuration_hash()? {
            return Err(Error::Conflict("exact signal configuration differs".into()));
        }
        owner.last_sequence = saved.last_sequence;
        owner.last_boundary_id = saved.last_boundary_id;
        owner.last_evaluated_at_ns = saved.last_evaluated_at_ns;
        owner.last_exact_completed = saved.last_exact_completed;
        if owner.checkpoint()?.root.payload != bundle.root.payload {
            return Err(Error::Conflict("noncanonical exact signal root".into()));
        }
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
            self.macd
                .advance_verified(&mut self.macd_source, &advance)?;
            if let Some(exact) = advance.completed() {
                self.last_exact_completed = Some(exact.clone());
                self.source_1s.absorb(exact)?;
            }
            if let Kind::Completed {
                bar,
                interval_ns: INTERVAL_NS,
                ..
            } = &boundary.kind
            {
                if self.last_exact_completed.as_ref().is_none_or(|exact| {
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
            if let Kind::Completed {
                bar,
                interval_ns: 1_000_000_000,
                ..
            } = &boundary.kind
            {
                let one_second = self
                    .source_1s
                    .close(bar.bar.end_ns, Some(bar.bar.trades))?
                    .ok_or_else(|| Error::Conflict("exact one-second bar missing".into()))?;
                self.noise.observe(one_second)?;
            }
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
            S + 1_000_000_000,
            2,
            0,
            "a".repeat(64),
        )
        .unwrap();
        let signal = State::new_live(
            Config {
                execution_interval: arte_core::execution_interval::ExecutionInterval::Fixed(
                    100_000_000,
                ),
                minimum_move_bps: 5,
                source_algorithm_hash: "b".repeat(64),
            },
            bars.configuration_hash().into(),
            S,
            S + 1_000_000_000,
        )
        .unwrap();
        let noise = strategy350_noise::State::new(crate::test_noise_config(), S, S + 1_000_000_000)
            .unwrap();
        Owner::new(bars, signal, noise, &crate::test_macd_config()).unwrap()
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
        assert!(!owner.forming_macd_for_boundary(&boundary).unwrap().bullish);
        let image = owner.checkpoint().unwrap();
        let generation = "a".repeat(64);
        let request = |id| Recovery {
            scope: scope(),
            session_start_ns: S,
            session_end_ns: S + 1_000_000_000,
            price_scale: 2,
            size_scale: 0,
            source_generation_hash: &generation,
            signal_config: Config {
                execution_interval: arte_core::execution_interval::ExecutionInterval::Fixed(
                    100_000_000,
                ),
                minimum_move_bps: 5,
                source_algorithm_hash: "b".repeat(64),
            },
            noise_config: crate::test_noise_config(),
            macd_config: crate::test_macd_config(),
            expected_sequence: 1,
            expected_boundary_id: Some(id),
        };
        let mut restored = Owner::restore(&image, &image.root.id, request("one")).unwrap();
        assert!(restored.observe(&boundary).unwrap().is_none());
        assert!(Owner::restore(&image, &image.root.id, request("wrong")).is_err());
        let mut changed_config = request("one");
        changed_config.macd_config.source_algorithm_hash = "f".repeat(64);
        assert!(Owner::restore(&image, &image.root.id, changed_config).is_err());
        let mut damaged = owner.checkpoint().unwrap();
        damaged.bars.payload[0] ^= 1;
        assert!(Owner::restore(&damaged, &damaged.root.id, request("one")).is_err());
        let mut damaged_macd = owner.checkpoint().unwrap();
        damaged_macd.macd.payload[0] ^= 1;
        assert!(Owner::restore(&damaged_macd, &damaged_macd.root.id, request("one")).is_err());
        let mut damaged_source = owner.checkpoint().unwrap();
        damaged_source.macd_source.payload[0] ^= 1;
        assert!(Owner::restore(&damaged_source, &damaged_source.root.id, request("one")).is_err());
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
    #[test]
    fn larger_timeframe_first_keeps_exact_100ms_comparison_across_restore() {
        let bars = Builder::new(
            scope(),
            BarMode::Live,
            S,
            S + 2_000_000_000,
            2,
            0,
            "a".repeat(64),
        )
        .unwrap();
        let signal = State::new_live(
            Config {
                execution_interval: arte_core::execution_interval::ExecutionInterval::Fixed(
                    100_000_000,
                ),
                minimum_move_bps: 5,
                source_algorithm_hash: "b".repeat(64),
            },
            bars.configuration_hash().into(),
            S,
            S + 2_000_000_000,
        )
        .unwrap();
        let noise = strategy350_noise::State::new(crate::test_noise_config(), S, S + 2_000_000_000)
            .unwrap();
        let mut owner = Owner::new(bars, signal, noise, &crate::test_macd_config()).unwrap();
        let trade = observation(1, S + 900_000_001, EventKind::Trade, "100", "1");
        owner
            .observe(&Boundary {
                id: "trade",
                sequence: 1,
                evaluated_at_ns: S + 950_000_000,
                kind: Kind::Trade {
                    observation: &trade,
                    eligible: true,
                },
            })
            .unwrap();
        let one_second = Completed {
            bar: Bar {
                start_ns: S,
                end_ns: S + 1_000_000_000,
                open: 100.,
                high: 100.,
                low: 100.,
                close: 100.,
                volume: 1.,
                notional: 100.,
                trades: 1,
            },
            macd: (0., 0., 0.),
            session_vwap: 100.,
            session_high: 100.,
            prior_session_high: None,
        };
        owner
            .observe(&Boundary {
                id: "one-second",
                sequence: 2,
                evaluated_at_ns: S + 1_100_000_000,
                kind: Kind::Completed {
                    interval_ns: 1_000_000_000,
                    bar: &one_second,
                    available_at_ns: S + 1_100_000_000,
                },
            })
            .unwrap();
        assert_eq!(
            owner.noise_distance(10_000).unwrap().observed_at_ns,
            S + 1_000_000_000
        );
        let image = owner.checkpoint().unwrap();
        let generation = "a".repeat(64);
        let mut restored = Owner::restore(
            &image,
            &image.root.id,
            Recovery {
                scope: scope(),
                session_start_ns: S,
                session_end_ns: S + 2_000_000_000,
                price_scale: 2,
                size_scale: 0,
                source_generation_hash: &generation,
                signal_config: Config {
                    execution_interval: arte_core::execution_interval::ExecutionInterval::Fixed(
                        100_000_000,
                    ),
                    minimum_move_bps: 5,
                    source_algorithm_hash: "b".repeat(64),
                },
                noise_config: crate::test_noise_config(),
                macd_config: crate::test_macd_config(),
                expected_sequence: 2,
                expected_boundary_id: Some("one-second"),
            },
        )
        .unwrap();
        assert_eq!(
            owner.noise_distance(10_000).unwrap(),
            restored.noise_distance(10_000).unwrap()
        );
        let hundred_ms = Completed {
            bar: Bar {
                start_ns: S + 900_000_000,
                end_ns: S + 1_000_000_000,
                ..one_second.bar.clone()
            },
            ..one_second.clone()
        };
        let same_close = Boundary {
            id: "hundred-ms",
            sequence: 3,
            evaluated_at_ns: S + 1_100_000_000,
            kind: Kind::Completed {
                interval_ns: INTERVAL_NS,
                bar: &hundred_ms,
                available_at_ns: S + 1_100_000_000,
            },
        };
        owner.observe(&same_close).unwrap();
        restored.observe(&same_close).unwrap();
        assert_eq!(
            owner.checkpoint().unwrap().root.id,
            restored.checkpoint().unwrap().root.id
        );
        let wrong = Completed {
            bar: Bar {
                trades: 2,
                ..hundred_ms.bar.clone()
            },
            ..hundred_ms.clone()
        };
        let changed = Boundary {
            id: "wrong",
            sequence: 4,
            evaluated_at_ns: S + 1_200_000_000,
            kind: Kind::Completed {
                interval_ns: INTERVAL_NS,
                bar: &wrong,
                available_at_ns: S + 1_200_000_000,
            },
        };
        assert!(owner.observe(&changed).is_err());
    }
}
