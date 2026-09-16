use super::*;
use arte_core::{
    event_order::Scope,
    market_structure::scheduler::playback::{sources::Shard, Frame, Limits, Mode, Poll},
    run_manifest::{Clock, Consumer, Execution, Manifest},
    strategy_dispatch,
    v7_extraction::Candle,
    v7_seed::{build, input_hash, SeedPolicy, SourceCertificate},
    v7_stream::StreamPolicy,
};
const S: u64 = 1_000_000_000;
pub(crate) fn fixture() -> (Document, Pinned, Catalog, Prepared, Bundle) {
    let bars: Vec<_> = (100..118)
        .map(|t| Candle {
            t,
            open: 10.,
            high: 11.,
            low: 9.,
            close: 10.,
            volume: 1.,
        })
        .collect();
    let seed = build(
        &bars,
        &[],
        SourceCertificate {
            instrument: 1,
            ticker: "TEST".into(),
            session: 20260914,
            start_second: 100,
            end_second: 120,
            source_generation: "offline".into(),
            input_hash: input_hash(&bars, &[]).unwrap(),
            certified_at_second: 121,
        },
        None,
        &SeedPolicy::default(),
        &SplitAdjustment::default(),
        121,
    )
    .unwrap();
    let seed = Bundle::from_seed(&seed).unwrap();
    let prepared = Prepared::new(
        Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        },
        "historical-fixture",
        vec![Frame {
            watermark_ns: 300 * S,
            evaluated_at_ns: 300 * S + 1,
            inputs: vec![],
        }],
        Limits {
            maximum_frames: 1,
            maximum_events: 1,
            maximum_serialized_bytes: 10000,
        },
    )
    .unwrap();
    let sources = Catalog {
        schema_version: 1,
        authority_manifest_hash: "a".repeat(64),
        clock: Clock::Historical,
        shards: vec![Shard {
            provider: 1,
            instrument: 1,
            session: 20260915,
            prepared_hash: prepared.hash().into(),
            clock_model: "historical-fixture".into(),
        }],
    };
    let manifest = Manifest {
        schema_version: 1,
        run_id: "market-startup-test".into(),
        mode: strategy_dispatch::Mode::Backtest,
        code_release_hash: "a".repeat(64),
        source_manifest_hash: sources.hash().unwrap(),
        reference_manifest_hash: "b".repeat(64),
        seed_manifest_hash: content_hash(&seed.manifest).unwrap(),
        algorithm_manifest_hash: "c".repeat(64),
        dependency_plan_hash: "d".repeat(64),
        hardware_profile_hash: "e".repeat(64),
        clock: Clock::Historical,
        execution: Execution::Simulated {
            fill_model_hash: "f".repeat(64),
            cost_model_hash: "f".repeat(64),
        },
        consumers: vec![Consumer {
            account: "a".into(),
            instrument: 1,
            strategy_instance: "strategy".into(),
            effective_config_hash: "f".repeat(64),
        }],
    };
    let manifest = Pinned::new(manifest.clone(), &manifest.hash().unwrap()).unwrap();
    let document = Document {
        schema_version: 1,
        manifest_hash: manifest.hash().into(),
        configuration: market_structure::Config {
            provider: 1,
            instrument: 1,
            session: 20260915,
            start_second: 200,
            end_second: 300,
            macd_periods: (2, 3, 2),
            maximum_bars: 100,
            maximum_market_events: 100,
            additional_timeframes: vec![],
            structure: StreamPolicy {
                input_generation: "offline".into(),
                ..StreamPolicy::default()
            },
        },
        split: SplitAdjustment::default(),
        quote_policy: Policy {
            provider: 1,
            valid_from_ns: 200 * S,
            valid_to_ns: 300 * S,
            available_at_ns: 199 * S,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: Default::default(),
            allowed_indicators: Default::default(),
            allow_empty_conditions: true,
            allow_empty_indicators: true,
        },
        maximum_pending_events: 100,
        frames_per_poll: 1,
        maximum_consumers: 1,
    };
    (document, manifest, sources, prepared, seed)
}
#[test]
fn portable_market_inputs_build_a_paused_run_and_empty_interval_completes() {
    let (document, manifest, sources, prepared, seed) = fixture();
    let hash = document.hash().unwrap();
    let bytes = serde_json::to_vec(&document).unwrap();
    let decoded = Document::read(bytes.as_slice(), &hash).unwrap();
    let mut run = decoded
        .assemble(&hash, &manifest, &sources, prepared, &seed)
        .unwrap();
    assert_eq!(run.status().mode, Mode::Paused);
    assert_eq!(run.status().admitted_events, 0);
    assert_eq!(run.scopes().len(), 1);
    assert_eq!(run.market().unwrap().source_scope().instrument, 1);
    assert_eq!(
        run.quotes().unwrap().policy_hash().unwrap(),
        content_hash(&document.quote_policy).unwrap()
    );
    run.resume().unwrap();
    for _ in 0..3 {
        if run.poll().unwrap() == Poll::Complete {
            break;
        }
    }
    assert_eq!(run.status().mode, Mode::Complete);
}
#[test]
fn wrong_identity_future_evidence_and_partial_source_are_rejected() {
    for fault in 0..11 {
        let (mut document, manifest, sources, prepared, mut seed) = fixture();
        let original_hash = document.hash().unwrap();
        match fault {
            0 => document.manifest_hash = "0".repeat(64),
            1 => document.quote_policy.provider = 2,
            2 => document.quote_policy.available_at_ns = 200 * S + 1,
            3 => document.quote_policy.valid_to_ns -= 1,
            4 => document.configuration.end_second += 1,
            5 => document.configuration.session += 1,
            6 => document.maximum_pending_events = 0,
            7 => {
                seed.objects.pop_first();
            }
            8 => seed.manifest.source_generation.push('x'),
            9 => document.configuration.start_second = 100, // seed unavailable
            10 => document.configuration.maximum_bars += 1, // old independent pin
            _ => unreachable!(),
        }
        let hash = if fault == 10 {
            original_hash
        } else {
            document.hash().unwrap_or_default()
        };
        assert!(
            document
                .assemble(&hash, &manifest, &sources, prepared, &seed)
                .is_err(),
            "fault {fault}"
        );
    }
}
#[test]
fn portable_document_rejects_unknown_fields_oversize_and_wrong_pin() {
    let (document, _, _, _, _) = fixture();
    let hash = document.hash().unwrap();
    let bytes = serde_json::to_vec(&document).unwrap();
    assert!(Document::read(bytes.as_slice(), &"0".repeat(64)).is_err());
    let mut json = serde_json::to_value(&document).unwrap();
    json["configuration"]["unknown"] = true.into();
    assert!(Document::read(serde_json::to_vec(&json).unwrap().as_slice(), &hash).is_err());
    assert!(Document::read(std::io::repeat(b' '), &hash).is_err());
}
