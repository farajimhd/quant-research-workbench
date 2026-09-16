use super::*;
use arte_core::{
    events::*,
    orders::{Bracket, Side},
    run_manifest::{Clock, Consumer, Execution, Manifest},
    simulation_costs::Model,
    strategy_dispatch::Mode,
};

fn limits() -> Limits {
    Limits {
        maximum_bytes: 1_000_000,
        maximum_orders: 8,
        maximum_pending_fills: 16,
        projection: ProjectionLimits {
            positions: 4,
            fills: 100,
            lots_per_position: 10,
            bytes: 100_000,
        },
    }
}
fn model() -> Model {
    Model {
        schema_version: 1,
        currency: "USD".into(),
        currency_scale: 2,
        fixed_per_fill_minor: 1,
        per_share_atoms: 0,
        per_share_scale: 0,
        minimum_per_fill_minor: 1,
    }
}
fn run() -> Run {
    let hash = "a".repeat(64);
    let manifest = Manifest {
        schema_version: 1,
        run_id: "recovery".into(),
        mode: Mode::Backtest,
        code_release_hash: hash.clone(),
        source_manifest_hash: hash.clone(),
        reference_manifest_hash: hash.clone(),
        seed_manifest_hash: hash.clone(),
        algorithm_manifest_hash: hash.clone(),
        dependency_plan_hash: hash.clone(),
        hardware_profile_hash: hash.clone(),
        clock: Clock::Historical,
        execution: Execution::Simulated {
            fill_model_hash: crate::test_fill_model().hash().unwrap(),
            cost_model_hash: model().hash().unwrap(),
        },
        consumers: ["a", "b"]
            .into_iter()
            .map(|account| Consumer {
                account: account.into(),
                instrument: 1,
                strategy_instance: "s".into(),
                effective_config_hash: hash.clone(),
            })
            .collect(),
    };
    let hash = manifest.hash().unwrap();
    Run::new(manifest, &hash).unwrap()
}
fn fixture(run: &Run) -> Runtime {
    let mut runtime = Runtime::new(
        Simulator::new_scoped("recovery", 1, 2, 8, 10000).unwrap(),
        Projection::new(4, 100, 10).unwrap(),
        16,
    )
    .unwrap();
    runtime
        .bind_source(arte_core::event_order::Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        })
        .unwrap();
    runtime
        .bind_costs(Costs::new(model(), run).unwrap(), crate::test_fill_model())
        .unwrap();
    // Seed owned orders here; production funding/submission is covered by the
    // multi-account playback lifecycle fixture, not authorized by this codec.
    for account in ["a", "b"] {
        runtime
            .simulator
            .submit(
                Bracket {
                    command_id: account.into(),
                    account: account.into(),
                    instrument: 1,
                    side: Side::Long,
                    quantity: 3,
                    entry: 100,
                    price_scale: 2,
                    stop: Some(90),
                    target: Some(110),
                    tick: 1,
                    deadline_ns: 100,
                },
                0,
                0,
            )
            .unwrap();
        runtime
            .owners
            .insert(account.into(), run.scope(account, 1, "s").unwrap());
        runtime.reservations.insert(
            account.into(),
            Reservation {
                command_id: account.into(),
                instrument: 1,
                cash_minor: 300,
            },
        );
    }
    runtime
}
fn quote(runtime: &mut Runtime, sequence: u64, bid: i64, size: u64) {
    let quote = Quote {
        sequence,
        at_ns: sequence,
        bid,
        ask: bid + 1,
        bid_size: size,
        ask_size: size,
    };
    runtime.quote(&quote).unwrap();
    runtime.last_source_quote = Some((
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Quote,
                sequence,
            },
            payload: Payload::Quote {
                bid: Decimal {
                    atoms: bid,
                    scale: 2,
                },
                ask: Decimal {
                    atoms: bid + 1,
                    scale: 2,
                },
                bid_size: Decimal {
                    atoms: size as i64,
                    scale: 0,
                },
                ask_size: Decimal {
                    atoms: size as i64,
                    scale: 0,
                },
                bid_exchange: 1,
                ask_exchange: 1,
                conditions: vec![],
                indicators: vec![],
            },
            sip: SourceTime {
                ns: sequence,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: sequence,
            receipt: None,
        },
        sequence,
        sequence,
    ));
}
fn cut(sequence: u64) -> Cut {
    Cut {
        boundary_sequence: sequence,
        boundary_hash: "b".repeat(64),
        at_ns: sequence,
    }
}
pub(crate) fn publication_fixture() -> (Run, Cut, Model, Limits, Bundle) {
    let run = run();
    let mut runtime = fixture(&run);
    runtime.advance_playback_clock(1).unwrap();
    let cut = cut(1);
    let bundle = runtime
        .checkpoint(&run, &cut, &BTreeMap::new(), limits())
        .unwrap();
    (run, cut, model(), limits(), bundle)
}
#[derive(Default)]
struct Journal {
    last: BTreeMap<String, Fill>,
    fail: bool,
}
impl Publisher for Journal {
    async fn publish(&mut self, batch: &Batch) -> Result<BTreeMap<String, String>> {
        for payload in batch.rows().values() {
            let fill: Fill = serde_json::from_str(payload).unwrap();
            self.last.insert(fill.command_id.clone(), fill);
        }
        if self.fail {
            self.fail = false;
            return Err(Error::Unready("ambiguous fill acknowledgment".into()));
        }
        Ok(batch.rows().clone())
    }
}
async fn drain(runtime: &mut Runtime, journal: &mut Journal) {
    while runtime.commit_next(journal).await.unwrap() {}
}
#[tokio::test]
async fn multi_account_recovery_continues_partial_exits_with_exact_cash_and_projection() {
    let run = run();
    let mut original = fixture(&run);
    let mut journal = Journal::default();
    quote(&mut original, 1, 99, 6);
    assert!(original
        .checkpoint(&run, &cut(1), &journal.last, limits())
        .is_err());
    journal.fail = true;
    assert!(original.commit_next(&mut journal).await.is_err());
    assert!(original
        .checkpoint(&run, &cut(1), &journal.last, limits())
        .is_err());
    drain(&mut original, &mut journal).await;
    for (sequence, bid, size) in [(2, 110, 2), (3, 112, 6)] {
        let image = original
            .checkpoint(&run, &cut(sequence - 1), &journal.last, limits())
            .unwrap();
        let mut restored = Runtime::restore_checkpoint(
            &image,
            &image.root.id,
            &run,
            &cut(sequence - 1),
            Costs::new(model(), &run).unwrap(),
            limits(),
        )
        .unwrap();
        assert_eq!(
            restored
                .checkpoint(&run, &cut(sequence - 1), &journal.last, limits())
                .unwrap()
                .root
                .id,
            image.root.id
        );
        let mut replay_journal = Journal {
            last: journal.last.clone(),
            fail: false,
        };
        quote(&mut original, sequence, bid, size);
        quote(&mut restored, sequence, bid, size);
        drain(&mut original, &mut journal).await;
        drain(&mut restored, &mut replay_journal).await;
        assert_eq!(
            original
                .checkpoint(&run, &cut(sequence), &journal.last, limits())
                .unwrap()
                .root
                .id,
            restored
                .checkpoint(&run, &cut(sequence), &replay_journal.last, limits())
                .unwrap()
                .root
                .id
        );
    }
    assert_eq!(original.closed_net_cash_minor("a").unwrap(), 29);
    assert_eq!(original.closed_net_cash_minor("b").unwrap(), 34);
}
#[tokio::test]
async fn mismatched_components_and_incomplete_graphs_fail_closed() {
    let run = run();
    let mut runtime = fixture(&run);
    let mut journal = Journal::default();
    quote(&mut runtime, 1, 99, 6);
    drain(&mut runtime, &mut journal).await;
    let mut image = runtime
        .checkpoint(&run, &cut(1), &journal.last, limits())
        .unwrap();
    let restore = |bundle: &Bundle| {
        Runtime::restore_checkpoint(
            bundle,
            &bundle.root.id,
            &run,
            &cut(1),
            Costs::new(model(), &run).unwrap(),
            limits(),
        )
    };
    let root_bytes = image.root.payload.clone();
    let mut tiny = limits();
    tiny.maximum_bytes = 16;
    assert!(runtime
        .checkpoint(&run, &cut(1), &journal.last, tiny)
        .is_err());
    assert!(Runtime::restore_checkpoint(
        &image,
        &image.root.id,
        &run,
        &cut(1),
        Costs::new(model(), &run).unwrap(),
        tiny
    )
    .is_err());
    tiny = limits();
    tiny.maximum_orders = 2;
    assert!(runtime
        .checkpoint(&run, &cut(1), &journal.last, tiny)
        .is_err());
    let mut root: Root = serde_json::from_slice(&root_bytes).unwrap();
    root.fill_model.participation_bps -= 1;
    image.root = encode(&root, limits().maximum_bytes).unwrap();
    assert!(restore(&image).is_err());
    root.fill_model.participation_bps += 1;
    root.version = 1;
    image.root = encode(&root, limits().maximum_bytes).unwrap();
    assert!(restore(&image).is_err());
    root.version = 2;
    root.last_source_quote.as_mut().unwrap().1 += 1;
    image.root = encode(&root, limits().maximum_bytes).unwrap();
    assert!(restore(&image).is_err());
    root.last_source_quote.as_mut().unwrap().1 -= 1;
    root.released.insert("a".into());
    image.root = encode(&root, limits().maximum_bytes).unwrap();
    assert!(restore(&image).is_err());
    root.released.clear();
    root.cash.remove("a");
    image.root = encode(&root, limits().maximum_bytes).unwrap();
    assert!(restore(&image).is_err());
    image.root = Object::new(root_bytes);
    let key = image.objects.keys().next().unwrap().clone();
    let removed = image.objects.remove(&key).unwrap();
    assert!(restore(&image).is_err());
    image.objects.insert(key, removed);
    let extra = Object::new(vec![1]);
    image.objects.insert(extra.id.clone(), extra);
    assert!(restore(&image).is_err());
    assert!(runtime
        .checkpoint(&run, &cut(2), &journal.last, limits())
        .is_err());
    runtime.reservations.remove("a");
    assert!(runtime
        .checkpoint(&run, &cut(1), &journal.last, limits())
        .is_err());
}
#[tokio::test]
async fn terminal_release_markers_and_quote_retry_survive_restore() {
    let run = run();
    let mut runtime = fixture(&run);
    let mut journal = Journal::default();
    quote(&mut runtime, 1, 99, 6);
    drain(&mut runtime, &mut journal).await;
    quote(&mut runtime, 2, 110, 6);
    drain(&mut runtime, &mut journal).await;
    runtime.released.insert("a".into());
    let image = runtime
        .checkpoint(&run, &cut(2), &journal.last, limits())
        .unwrap();
    let mut restored = Runtime::restore_checkpoint(
        &image,
        &image.root.id,
        &run,
        &cut(2),
        Costs::new(model(), &run).unwrap(),
        limits(),
    )
    .unwrap();
    assert!(restored.released.contains("a"));
    quote(&mut restored, 2, 110, 6);
    assert!(!restored.commit_next(&mut journal).await.unwrap());
    assert_eq!(
        restored
            .checkpoint(&run, &cut(2), &journal.last, limits())
            .unwrap()
            .root
            .id,
        image.root.id
    );
}

#[test]
fn database_archive_roundtrip_and_multichunk_integrity() {
    use super::storage::{Header, Stored, CHUNK_BYTES};
    let run = run();
    let mut runtime = fixture(&run);
    runtime.advance_playback_clock(1).unwrap();
    let mut limits = limits();
    limits.maximum_bytes = 4 * CHUNK_BYTES;
    let mut image = runtime
        .checkpoint(&run, &cut(1), &BTreeMap::new(), limits)
        .unwrap();
    let stored = Stored::from_execution(&image, limits).unwrap();
    let restored = stored.hydrate(limits).unwrap();
    Runtime::restore_checkpoint(
        &restored,
        &image.root.id,
        &run,
        &cut(1),
        Costs::new(model(), &run).unwrap(),
        limits,
    )
    .unwrap();
    assert_eq!(restored.root.payload, image.root.payload);
    // Transport-codec fixture: a binary object crosses several chunk boundaries.
    // It is not a valid execution graph; semantic restore must still reject it.
    let blob = Object::new([0u8, 255, 195, 169].repeat(CHUNK_BYTES / 2 + 1));
    image.objects.insert(blob.id.clone(), blob);
    let mut stored = Stored::from_execution(&image, limits).unwrap();
    assert!(stored.chunks.len() > 1);
    let restored = stored.hydrate(limits).unwrap();
    assert!(Runtime::restore_checkpoint(
        &restored,
        &image.root.id,
        &run,
        &cut(1),
        Costs::new(model(), &run).unwrap(),
        limits
    )
    .is_err());
    for (id, object) in &image.objects {
        assert_eq!(restored.objects[id].payload, object.payload);
    }
    let id = stored.chunks.keys().next().unwrap().clone();
    let chunk = stored.chunks.remove(&id).unwrap();
    assert!(stored.hydrate(limits).is_err());
    stored.chunks.insert(id.clone(), chunk);
    stored.chunks.get_mut(&id).unwrap().payload[0] ^= 1;
    assert!(stored.hydrate(limits).is_err());
    let mut small = limits;
    small.maximum_bytes = 1;
    assert!(Header::decode(&stored.root, small).is_err());
    let mut payload = stored.root.payload.clone();
    payload.push(b' ');
    assert!(Header::decode(&Object::new(payload), limits).is_err());
}
