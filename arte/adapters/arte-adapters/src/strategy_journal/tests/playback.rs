use super::*;
use arte_core::{
    market_structure::scheduler::{
        playback::{
            accounts::Run,
            sources::{Catalog, Shard},
            Frame, Input, Limits, Poll, Prepared as InputData,
        },
        Scheduler,
    },
    market_structure::{Config, Ordered, Runtime as Market},
    run_manifest::{Clock, Consumer, Execution, Manifest, Pinned},
    v7_extraction::Candle,
    v7_seed::{build, input_hash, SeedPolicy, SourceCertificate, SplitAdjustment},
    v7_stream::StreamPolicy,
};

fn run() -> Run {
    const S: u64 = 1_000_000_000;
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
    let market = Market::new(
        &seed,
        Config {
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
        &SplitAdjustment::default(),
    )
    .unwrap();
    let scheduler = Scheduler::new(Ordered::new(market, 10).unwrap(), "run".into()).unwrap();
    use arte_core::events::*;
    let prepared = InputData::new(
        scheduler.scope(),
        "historical-test",
        vec![Frame {
            watermark_ns: 201 * S,
            evaluated_at_ns: 201 * S,
            inputs: vec![Input {
                eligible: true,
                observation: Observation {
                    key: EventKey {
                        provider: 1,
                        instrument: 1,
                        session: 20260915,
                        kind: EventKind::Trade,
                        sequence: 1,
                    },
                    payload: Payload::Trade {
                        price: Decimal {
                            atoms: 10,
                            scale: 0,
                        },
                        size: Decimal { atoms: 1, scale: 0 },
                        exchange: 1,
                        trade_id: "1".into(),
                        trf: None,
                        conditions: vec![],
                        correction: None,
                    },
                    sip: SourceTime {
                        ns: 200 * S,
                        precision_ns: 1,
                    },
                    participant: None,
                    available_at_ns: 200 * S,
                    receipt: None,
                },
            }],
        }],
        Limits {
            maximum_frames: 1,
            maximum_events: 1,
            maximum_serialized_bytes: 10000,
        },
    )
    .unwrap();
    let catalog = Catalog {
        schema_version: 1,
        authority_manifest_hash: "a".repeat(64),
        clock: Clock::Historical,
        shards: vec![Shard {
            provider: 1,
            instrument: 1,
            session: 20260915,
            prepared_hash: prepared.hash().into(),
            clock_model: "historical-test".into(),
        }],
    };
    let m = Manifest {
        schema_version: 1,
        run_id: "run".into(),
        mode: Mode::Backtest,
        code_release_hash: "a".repeat(64),
        source_manifest_hash: catalog.hash().unwrap(),
        reference_manifest_hash: "b".repeat(64),
        seed_manifest_hash: "c".repeat(64),
        algorithm_manifest_hash: "d".repeat(64),
        dependency_plan_hash: "e".repeat(64),
        hardware_profile_hash: "f".repeat(64),
        clock: Clock::Historical,
        execution: Execution::Simulated {
            fill_model_hash: "1".repeat(64),
            cost_model_hash: "2".repeat(64),
        },
        consumers: ["a", "b"]
            .into_iter()
            .map(|account| Consumer {
                account: account.into(),
                instrument: 1,
                strategy_instance: "s".into(),
                effective_config_hash: "3".repeat(64),
            })
            .collect(),
    };
    let hash = m.hash().unwrap();
    Run::new(
        &Pinned::new(m, &hash).unwrap(),
        &catalog,
        scheduler,
        prepared,
        1,
        2,
    )
    .unwrap()
}

#[tokio::test(start_paused = true)]
async fn playback_commits_concurrently_and_retries_only_failed_accounts() {
    let mut run = run();
    run.resume().unwrap();
    assert_eq!(run.poll().unwrap(), Poll::Boundary);
    let input = run.pending().unwrap().unwrap().input("features".into());
    let mut runtimes: Vec<_> = run
        .scopes()
        .iter()
        .map(|scope| {
            let mut runtime =
                arte_core::strategy_transaction::Runtime::new(scope.clone(), 0_u64, 1024).unwrap();
            let template = prepared_account(&scope.account);
            let decision = template.pending_decision().unwrap();
            runtime
                .prepare(
                    input.clone(),
                    &decision.safety,
                    "evidence".into(),
                    |state| {
                        *state += 1;
                        Ok(vec![Action::Wait {
                            reason: "gate".into(),
                        }])
                    },
                )
                .unwrap();
            runtime
        })
        .collect();
    let mut stores = [timed(1, false), timed(1, true)];
    let mut writes: Vec<_> = runtimes
        .iter_mut()
        .zip(stores.iter_mut())
        .map(|(r, p)| accounts::Write::new(r, p))
        .collect();
    let outcomes = accounts::commit_accounts(&mut writes, &mut run, 2)
        .await
        .unwrap();
    assert!(outcomes[0].result.is_ok());
    assert!(outcomes[1].result.is_err());
    assert_eq!(run.remaining(), Some(1));
    assert!(run.acknowledge().is_err());
    assert_eq!(run.pending().unwrap().unwrap().id, input.event_id);
    assert!(accounts::commit_accounts(&mut writes, &mut run, 2)
        .await
        .unwrap()
        .iter()
        .all(|o| o.result.is_ok()));
    run.acknowledge().unwrap();
    drop(writes);
    assert_eq!(stores.map(|s| s.calls), [1, 2]);
    assert_eq!(run.status().acknowledged_boundaries, 1);
    assert!(runtimes.iter().all(|r| *r.committed_state() == 1));
}
