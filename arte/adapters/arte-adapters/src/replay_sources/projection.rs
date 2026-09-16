//! Explicit retrospective simulation projection. Never persist these modeled
//! observations as source facts or use this path for recorded-live replay.
use super::Source;
use arte_core::{
    content_hash,
    event_order::Scope,
    events::{EventKey, EventKind, Payload},
    market_structure::scheduler::playback::{
        sources::{Catalog, Shard},
        Frame, Input, Limits, Prepared,
    },
    run_manifest::Clock,
    Error, Result,
};
use serde::Serialize;
use std::collections::BTreeMap;

/// A caller supplies decisions from its approved trade-condition authority. This
/// adapter verifies complete identity binding; it does not approve that policy.
pub struct Eligibility {
    pub policy_hash: String,
    pub trades: BTreeMap<EventKey, bool>,
}
/// Evaluate the same pinned condition rules used by the live market lane.
/// Policy must already be known at historical session start, not REST acquisition.
pub fn prepare_with_policy(
    trades: &Source,
    quotes: &Source,
    scope: Scope,
    timing: Policy,
    policy: &arte_core::trade_eligibility::Pinned,
    limits: Limits,
) -> Result<Projection> {
    let interval = trades.certificate().interval;
    policy.require_interval(interval, interval.start)?;
    if policy.provider() != scope.provider || trades.observations().len() > limits.maximum_events {
        return Err(Error::Conflict(
            "replay trade policy provider or event budget".into(),
        ));
    }
    let mut decisions = BTreeMap::new();
    for event in trades.observations() {
        let eligible = policy.evaluate(event, event.sip.ns)?;
        if decisions.insert(event.key.clone(), eligible).is_some() {
            return Err(Error::Conflict(
                "duplicate historical trade identity".into(),
            ));
        }
    }
    prepare(
        trades,
        quotes,
        scope,
        timing,
        &Eligibility {
            policy_hash: policy.hash().into(),
            trades: decisions,
        },
        limits,
    )
}
#[derive(Clone, Serialize)]
pub struct Policy {
    /// Positive fixed SIP-to-modeled-availability delay, at most one second.
    /// Not measured network latency. The minimum one ns permits half-open release.
    pub delay_ns: u64,
}
pub struct Projection {
    pub prepared: Prepared,
    pub catalog: Catalog,
    pub manifest: Manifest,
}
#[derive(Serialize)]
pub struct Manifest {
    pub version: u32,
    pub trade_certificate: String,
    pub quote_certificate: String,
    pub policy: Policy,
    pub eligibility_policy: String,
    pub eligibility_decisions: String,
    pub session: u32,
}
fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Exactly one certified revision per channel and the same complete interval.
/// No overlap repair, silent filtering, revision selection or unknown eligibility.
/// Raw sources stay borrowed and unchanged. Deterministic tie order is SIP,
/// provider sequence, then full event key. Equal-SIP groups release only after
/// their final chunk has been admitted; scheduler capacity must fit such groups.
pub fn prepare(
    trades: &Source,
    quotes: &Source,
    scope: Scope,
    policy: Policy,
    eligibility: &Eligibility,
    limits: Limits,
) -> Result<Projection> {
    let tc = trades.certificate();
    let qc = quotes.certificate();
    if policy.delay_ns == 0 || policy.delay_ns > 1_000_000_000 {
        return Err(Error::Invalid(
            "historical modeled delay outside (0, 1s]".into(),
        ));
    }
    if tc.authority.kind != EventKind::Trade
        || qc.authority.kind != EventKind::Quote
        || tc.interval != qc.interval
        || [tc, qc].iter().any(|c| {
            c.authority.provider != scope.provider || c.authority.instrument != scope.instrument
        })
        || !valid_hash(&eligibility.policy_hash)
    {
        return Err(Error::Conflict(
            "historical projection source scope or eligibility identity".into(),
        ));
    }
    let count = trades
        .observations()
        .len()
        .checked_add(quotes.observations().len())
        .ok_or_else(|| Error::Capacity("historical projection count overflow".into()))?;
    if limits.maximum_events == 0
        || limits.maximum_events > 10_000_000
        || count > limits.maximum_events
        || limits.maximum_frames == 0
        || limits.maximum_frames > 10_000_000
        || limits.maximum_serialized_bytes == 0
        || eligibility.trades.len() != trades.observations().len()
    {
        return Err(Error::Capacity(
            "historical projection limits or eligibility count".into(),
        ));
    }
    let mut ordered: Vec<_> = trades
        .observations()
        .iter()
        .chain(quotes.observations())
        .collect();
    // Fail on all duplicate identities, even equal payloads. Source revision
    // reconciliation belongs before this projection and cannot silently lose rows.
    let mut keys = std::collections::BTreeSet::new();
    for event in &ordered {
        if event.key.session != scope.session
            || event.receipt.is_some()
            // No provider-specific numeric correction meaning is guessed. A
            // marked correction needs a separate causal availability contract.
            || matches!(event.payload, Payload::Trade { correction: Some(_), .. })
            || !keys.insert(&event.key)
            || (event.key.kind == EventKind::Trade && !eligibility.trades.contains_key(&event.key))
        {
            return Err(Error::Conflict(
                "historical projection session, correction, duplicate or eligibility".into(),
            ));
        }
    }
    drop(keys);
    ordered.sort_unstable_by_key(|e| (e.sip.ns, e.key.sequence, &e.key));
    let manifest = Manifest {
        version: 1,
        trade_certificate: tc.id()?,
        quote_certificate: qc.id()?,
        policy,
        eligibility_policy: eligibility.policy_hash.clone(),
        // JSON object keys cannot be composite event keys. Hash an ordered list.
        eligibility_decisions: content_hash(&eligibility.trades.iter().collect::<Vec<_>>())?,
        session: scope.session,
    };
    let authority = content_hash(&("arte.historical-projection.v1", &manifest))?;
    let clock_model = format!("historical-sip-delay-v1:{authority}");
    let clock = |at: u64| {
        at.checked_add(manifest.policy.delay_ns)
            .ok_or_else(|| Error::Invalid("historical projection clock overflow".into()))
    };
    let mut frames = Vec::new();
    let mut bytes = 0usize;
    let mut append = |frame: Frame| -> Result<()> {
        let size = serde_json::to_vec(&frame)
            .map_err(|e| Error::Serialization(e.to_string()))?
            .len();
        bytes = bytes
            .checked_add(size)
            .ok_or_else(|| Error::Capacity("projection byte overflow".into()))?;
        if frames.len() == limits.maximum_frames || bytes > limits.maximum_serialized_bytes {
            return Err(Error::Capacity(
                "historical projection frame or byte budget".into(),
            ));
        }
        frames.push(frame);
        Ok(())
    };
    let mut start = 0;
    while start < ordered.len() {
        let at = ordered[start].sip.ns;
        let end = start + ordered[start..].partition_point(|e| e.sip.ns == at);
        let evaluated_at_ns = clock(at)?;
        for (index, chunk) in ordered[start..end].chunks(256).enumerate() {
            let last = start + (index + 1) * 256 >= end;
            let inputs = chunk
                .iter()
                .map(|raw| {
                    let mut observation = (*raw).clone();
                    // Simulation-only copy. Original availability is retained by the
                    // certificate-addressed source; receipt/participant stay unchanged.
                    observation.available_at_ns = evaluated_at_ns;
                    let eligible = eligibility
                        .trades
                        .get(&observation.key)
                        .copied()
                        .unwrap_or(false);
                    Input {
                        observation,
                        eligible,
                    }
                })
                .collect();
            append(Frame {
                watermark_ns: if last { at + 1 } else { at },
                evaluated_at_ns,
                inputs,
            })?;
        }
        start = end;
    }
    // Certified interval end closes remaining bars, including a certified empty
    // source. It does not assert that the provider omitted no source events.
    append(Frame {
        watermark_ns: tc.interval.end,
        evaluated_at_ns: clock(tc.interval.end)?,
        inputs: vec![],
    })?;
    let prepared = Prepared::new(scope, &clock_model, frames, limits)?;
    let catalog = Catalog {
        schema_version: 1,
        authority_manifest_hash: authority,
        clock: Clock::Historical,
        shards: vec![Shard {
            provider: scope.provider,
            instrument: scope.instrument,
            session: scope.session,
            prepared_hash: prepared.hash().to_owned(),
            clock_model,
        }],
    };
    catalog.hash()?;
    Ok(Projection {
        prepared,
        catalog,
        manifest,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{
        acquisition::{Authority, Certificate, Page, Verifier},
        coverage::Interval,
        event_storage::Batch,
        events::{Decimal, Observation, Payload, SourceTime},
    };
    fn scope() -> Scope {
        Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        }
    }
    fn limits() -> Limits {
        Limits {
            maximum_frames: 1024,
            maximum_events: 1024,
            maximum_serialized_bytes: 1_000_000,
        }
    }
    fn event(kind: EventKind, sequence: u64, at: u64) -> Observation {
        let price = Decimal {
            atoms: 100,
            scale: 2,
        };
        let size = Decimal { atoms: 1, scale: 0 };
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: scope().session,
                kind,
                sequence,
            },
            payload: if kind == EventKind::Trade {
                Payload::Trade {
                    price,
                    size,
                    exchange: 1,
                    trade_id: sequence.to_string(),
                    trf: None,
                    conditions: vec![],
                    correction: None,
                }
            } else {
                Payload::Quote {
                    bid: price,
                    ask: price,
                    bid_size: size,
                    ask_size: size,
                    bid_exchange: 1,
                    ask_exchange: 1,
                    conditions: vec![],
                    indicators: vec![],
                }
            },
            sip: SourceTime {
                ns: at,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: 30,
            receipt: None,
        }
    }
    fn source(kind: EventKind, observations: Vec<Observation>, end: u64) -> Source {
        let batch = (!observations.is_empty()).then(|| Batch::prepare(&observations).unwrap());
        let certificate = Certificate {
            schema_version: 1,
            authority: Authority {
                provider: 1,
                instrument: 1,
                kind,
                source_revision: "r1".into(),
                contract_hash: "a".repeat(64),
                capabilities_hash: "b".repeat(64),
            },
            interval: Interval { start: 10, end },
            first_request_hash: "c".repeat(64),
            pages: vec![Page {
                request_hash: "c".repeat(64),
                response_hash: "d".repeat(64),
                next_request_hash: None,
                acquired_at_ns: 30,
                source_rows: observations.len() as u64,
                accepted_rows: observations.len() as u64,
                rejected_rows: 0,
                deduplicated_rows: 0,
                batches: batch.iter().map(|b| b.id().unwrap()).collect(),
                identity_checked: true,
                ordering_checked: true,
                interval_checked: true,
            }],
            published_at_ns: 40,
        };
        let mut verifier = Verifier::new(certificate).unwrap();
        if let Some(batch) = batch {
            verifier.observe(&batch).unwrap();
        }
        Source {
            certificate: verifier.finish().unwrap(),
            observations,
            trade_seconds: None,
        }
    }
    fn eligibility(source: &Source) -> Eligibility {
        Eligibility {
            policy_hash: "e".repeat(64),
            trades: source
                .observations()
                .iter()
                .map(|e| (e.key.clone(), true))
                .collect(),
        }
    }
    fn expected_frame(events: &[Observation], at: u64, watermark_ns: u64) -> Frame {
        Frame {
            watermark_ns,
            evaluated_at_ns: at + 2,
            inputs: events
                .iter()
                .map(|e| {
                    let mut observation = e.clone();
                    observation.available_at_ns = at + 2;
                    Input {
                        eligible: e.key.kind == EventKind::Trade,
                        observation,
                    }
                })
                .collect(),
        }
    }
    #[test]
    fn projection_pins_model_and_provenance_without_changing_source_clocks() {
        let t = event(EventKind::Trade, 1, 10);
        let q = event(EventKind::Quote, 1, 10);
        let trades = source(EventKind::Trade, vec![t.clone()], 20);
        let quotes = source(EventKind::Quote, vec![q.clone()], 20);
        let eligible = eligibility(&trades);
        let p = prepare(
            &trades,
            &quotes,
            scope(),
            Policy { delay_ns: 2 },
            &eligible,
            limits(),
        )
        .unwrap();
        assert_eq!(trades.observations(), std::slice::from_ref(&t));
        assert_eq!(quotes.observations(), std::slice::from_ref(&q));
        assert_eq!(p.catalog.clock, Clock::Historical);
        assert_eq!(
            p.manifest.trade_certificate,
            trades.certificate().id().unwrap()
        );
        let expected = Prepared::new(
            scope(),
            &p.catalog.shards[0].clock_model,
            vec![expected_frame(&[t, q], 10, 11), expected_frame(&[], 20, 20)],
            limits(),
        )
        .unwrap();
        assert_eq!(p.prepared.hash(), expected.hash());
        let other = prepare(
            &trades,
            &quotes,
            scope(),
            Policy { delay_ns: 3 },
            &eligible,
            limits(),
        )
        .unwrap();
        assert_ne!(p.prepared.hash(), other.prepared.hash());
        assert_ne!(p.catalog.hash().unwrap(), other.catalog.hash().unwrap());
    }
    #[test]
    fn tied_groups_chunk_without_early_release_and_empty_intervals_close() {
        let events: Vec<_> = (1..=257)
            .map(|seq| event(EventKind::Trade, seq, 10))
            .collect();
        let trades = source(EventKind::Trade, events.clone(), 20);
        let quotes = source(EventKind::Quote, vec![], 20);
        let p = prepare(
            &trades,
            &quotes,
            scope(),
            Policy { delay_ns: 2 },
            &eligibility(&trades),
            limits(),
        )
        .unwrap();
        let expected = Prepared::new(
            scope(),
            &p.catalog.shards[0].clock_model,
            vec![
                expected_frame(&events[..256], 10, 10),
                expected_frame(&events[256..], 10, 11),
                expected_frame(&[], 20, 20),
            ],
            limits(),
        )
        .unwrap();
        assert_eq!(p.prepared.hash(), expected.hash());
        let trades = source(EventKind::Trade, vec![], 20);
        let p = prepare(
            &trades,
            &quotes,
            scope(),
            Policy { delay_ns: 2 },
            &eligibility(&trades),
            limits(),
        )
        .unwrap();
        let expected = Prepared::new(
            scope(),
            &p.catalog.shards[0].clock_model,
            vec![expected_frame(&[], 20, 20)],
            limits(),
        )
        .unwrap();
        assert_eq!(p.prepared.hash(), expected.hash());
    }
    #[test]
    fn rejects_scope_coverage_decision_budget_and_clock_errors() {
        for fault in 0..11 {
            let trades = source(
                EventKind::Trade,
                vec![event(EventKind::Trade, 1, 10)],
                if fault == 10 { u64::MAX } else { 20 },
            );
            let quotes = source(
                EventKind::Quote,
                vec![],
                if fault == 10 {
                    u64::MAX
                } else if fault == 0 {
                    21
                } else {
                    20
                },
            );
            let mut eligible = eligibility(&trades);
            let mut scope = scope();
            let mut budget = limits();
            let mut policy = Policy { delay_ns: 2 };
            match fault {
                1 => scope.session += 1,
                2 => {
                    eligible.trades.clear();
                }
                3 => {
                    let mut key = eligible.trades.pop_first().unwrap().0;
                    key.sequence += 1;
                    eligible.trades.insert(key, true);
                }
                4 => eligible.policy_hash = "bad".into(),
                5 => budget.maximum_frames = 1,
                6 => budget.maximum_serialized_bytes = 1,
                7 => budget.maximum_events = 0,
                8 => policy.delay_ns = 0,
                9 => policy.delay_ns = 1_000_000_001,
                _ => {}
            }
            assert!(
                prepare(&trades, &quotes, scope, policy, &eligible, budget).is_err(),
                "fault {fault}"
            );
        }
    }

    #[test]
    fn explicit_ineligibility_changes_identity_and_marked_corrections_are_not_retimed() {
        let mut trade = event(EventKind::Trade, 1, 10);
        let trades = source(EventKind::Trade, vec![trade.clone()], 20);
        let quotes = source(EventKind::Quote, vec![], 20);
        let mut eligible = eligibility(&trades);
        let first = prepare(
            &trades,
            &quotes,
            scope(),
            Policy { delay_ns: 2 },
            &eligible,
            limits(),
        )
        .unwrap();
        eligible.trades.insert(trade.key.clone(), false);
        let second = prepare(
            &trades,
            &quotes,
            scope(),
            Policy { delay_ns: 2 },
            &eligible,
            limits(),
        )
        .unwrap();
        assert_ne!(
            first.catalog.hash().unwrap(),
            second.catalog.hash().unwrap()
        );
        let mut frame = expected_frame(std::slice::from_ref(&trade), 10, 11);
        frame.inputs[0].eligible = false;
        let expected = Prepared::new(
            scope(),
            &second.catalog.shards[0].clock_model,
            vec![frame, expected_frame(&[], 20, 20)],
            limits(),
        )
        .unwrap();
        assert_eq!(second.prepared.hash(), expected.hash());
        if let Payload::Trade { correction, .. } = &mut trade.payload {
            *correction = Some(0);
        }
        let trades = source(EventKind::Trade, vec![trade], 20);
        assert!(prepare(
            &trades,
            &quotes,
            scope(),
            Policy { delay_ns: 2 },
            &eligible,
            limits()
        )
        .is_err());
    }

    #[test]
    fn conflicting_quote_versions_require_reconciliation_before_projection() {
        let trades = source(EventKind::Trade, vec![], 20);
        let first = event(EventKind::Quote, 1, 10);
        let mut second = first.clone();
        if let Payload::Quote { ask, .. } = &mut second.payload {
            ask.atoms += 1;
        }
        let quotes = source(EventKind::Quote, vec![first, second], 20);
        assert!(prepare(
            &trades,
            &quotes,
            scope(),
            Policy { delay_ns: 2 },
            &eligibility(&trades),
            limits()
        )
        .is_err());
    }

    #[test]
    fn historical_projection_uses_shared_policy_not_acquisition_time_or_unknown_defaults() {
        use arte_core::trade_eligibility::{Pinned, Policy as TradePolicy};
        let mut event = event(EventKind::Trade, 1, 10);
        if let Payload::Trade { conditions, .. } = &mut event.payload {
            *conditions = vec![2];
        }
        let trades = source(EventKind::Trade, vec![event], 20);
        let quotes = source(EventKind::Quote, vec![], 20);
        let policy = TradePolicy {
            schema_version: 1,
            provider: 1,
            valid_from_ns: 10,
            valid_to_ns: 20,
            available_at_ns: 9,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: Default::default(),
            excluded_conditions: [2].into(),
            allow_empty_conditions: true,
        };
        let hash = policy.hash().unwrap();
        let pinned = Pinned::new(policy.clone(), &hash).unwrap();
        let projected = prepare_with_policy(
            &trades,
            &quotes,
            scope(),
            Policy { delay_ns: 2 },
            &pinned,
            limits(),
        )
        .unwrap();
        let expected = prepare(
            &trades,
            &quotes,
            scope(),
            Policy { delay_ns: 2 },
            &Eligibility {
                policy_hash: hash,
                trades: [(trades.observations()[0].key.clone(), false)].into(),
            },
            limits(),
        )
        .unwrap();
        assert_eq!(projected.prepared.hash(), expected.prepared.hash());
        for fault in 0..2 {
            let mut invalid = policy.clone();
            if fault == 0 {
                invalid.available_at_ns = 11;
            } else {
                invalid.excluded_conditions.clear();
            }
            let hash = invalid.hash().unwrap();
            let invalid = Pinned::new(invalid, &hash).unwrap();
            assert!(prepare_with_policy(
                &trades,
                &quotes,
                scope(),
                Policy { delay_ns: 2 },
                &invalid,
                limits()
            )
            .is_err());
        }
    }
}
