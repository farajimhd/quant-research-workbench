use super::*;

#[test]
fn incremental_lifecycle_matches_full_evidence_rebuild_through_crosses_and_flips() {
    let at = 1_700_000_000_000;
    let mut reference = GenericStructureEngine::new("TEST");
    reference.timeframe_states = (0..40).map(|i| confirmed(i, if i % 2 == 0 {1} else {-1}, 9.0 + i as f64 * 0.05, at)).collect();
    reference.refresh_unified_level_tracks(Utc.timestamp_millis_opt(at + 3000).unwrap(), 10.0);
    let mut incremental = GenericStructureEngine::new("TEST");
    incremental.seed_checkpoint(&reference.checkpoint());
    for i in 0..2_000 {
        let price = 8.9 + (i % 87) as f64 * 0.03;
        let ts = Utc.timestamp_millis_opt(at + 4000 + i * 100).unwrap();
        reference.update_unified_level_lifecycles_mode(ts, price, 100.0, true);
        incremental.update_unified_level_lifecycles_mode(ts, price, 100.0, false);
        if i % 37 == 0 {
            // Source refresh can interleave with ordinary trades.
            reference.refresh_unified_level_tracks(ts, price);
            incremental.refresh_unified_level_tracks(ts, price);
        }
        assert_eq!(serde_json::to_value(reference.checkpoint()).unwrap(), serde_json::to_value(incremental.checkpoint()).unwrap(), "state diverged at event {i}");
    }
    assert!(reference.unified_tracks.iter().any(|t| t.level.role_flip_count > 0));
}

#[test]
fn checkpoint_json_preserves_exact_structural_boundaries() {
    let at = 1_700_000_000_000;
    let mut engine = GenericStructureEngine::new("TEST");
    let mut level = event_native_level(1, 1, 2.0, at);
    // Actual boundaries found drifting by one ULP after a canonical SUGP restore.
    level.lower = 1.9997009999999997;
    level.upper = 2.0155039902398121;
    engine.levels.push(level);
    let checkpoint = engine.checkpoint();
    let bytes = serde_json::to_vec(&checkpoint).unwrap();
    let decoded = crate::structure_checkpoint_json::decode_checkpoint(std::str::from_utf8(&bytes).unwrap()).unwrap();
    assert_eq!(decoded.levels[0].lower.to_bits(), checkpoint.levels[0].lower.to_bits());
    assert_eq!(decoded.levels[0].upper.to_bits(), checkpoint.levels[0].upper.to_bits());
    let mut restored = GenericStructureEngine::new("TEST");
    restored.seed_checkpoint(&decoded);
    assert_eq!(serde_json::to_value(checkpoint).unwrap(), serde_json::to_value(restored.checkpoint()).unwrap());
}

#[test]
fn prominent_native_evidence_is_inherited_once_without_recounting_refreshes() {
    let at = 1_700_000_000_000;
    let mut engine = GenericStructureEngine::new("TEST");
    engine.levels = vec![
        event_native_level(1, 1, 9.0, at),
        event_native_level(2, 1, 9.005, at + 10_000),
    ];
    for level in &mut engine.levels {
        level.hold_count = 40;
        level.touch_count = 45;
    }
    let ts = Utc.timestamp_millis_opt(at + 20_000).unwrap();
    engine.refresh_unified_level_tracks(ts, 9.1);
    assert_eq!(engine.unified_tracks.len(), 1);
    let before = engine.unified_tracks[0].level.clone();
    assert_eq!(before.hold_count, 40);
    engine.refresh_unified_level_tracks(ts + chrono::Duration::seconds(1), 9.1);
    let after = &engine.unified_tracks[0].level;
    assert_eq!(before.unified_level_id, after.unified_level_id);
    assert_eq!(before.hold_count, after.hold_count);
    assert_eq!(before.touch_count, after.touch_count);
    assert_eq!(before.price, after.price);
}

#[test]
fn adjacent_zones_do_not_merge_outside_each_others_observed_geometry() {
    let first = unified_test_level(1, -1, 6.85, 6.87);
    let second = unified_test_level(2, -1, 6.87, 6.89);
    assert!(!unified_geometry_matches(&first, &second));
    let mut tracks = vec![first, second]
        .into_iter()
        .map(|level| UnifiedLevelTrack {
            level,
            lifecycle: LevelLifecycle::Active,
            last_relation: -1,
        })
        .collect::<Vec<_>>();
    consolidate_unified_tracks(&mut tracks);
    assert_eq!(tracks.len(), 2);
}

#[test]
fn historical_raw_levels_are_not_evicted_by_current_day_capacity() {
    let at = 1_700_000_000_000;
    let mut engine = GenericStructureEngine::new("TEST");
    engine.levels = (0..600)
        .map(|i| event_native_level(i, -1, 10.0 + i as f64, at))
        .collect();
    engine.prune_levels();
    assert_eq!(engine.levels.len(), 600);
    engine.levels[0].lifecycle = LevelLifecycle::Retired;
    engine.prune_levels();
    assert_eq!(engine.levels.len(), 599);
}

fn confirmed(id: u64, side: i8, price: f64, at: i64) -> TimeframeState {
    let pivot = swing(id, side, price, at, at + 2_000, false);
    TimeframeState {
        timeframe: "1s".into(),
        active_high: (side < 0).then_some(pivot.clone()),
        active_low: (side > 0).then_some(pivot),
        ..TimeframeState::default()
    }
}

#[test]
fn prominent_current_pivots_do_not_compete_with_historical_scores_or_slots() {
    let at = 1_700_000_000_000;
    let mut states = (0..300)
        .map(|i| confirmed(i, if i % 2 == 0 { -1 } else { 1 }, 20.0 + i as f64, at))
        .collect::<Vec<_>>();
    states.extend([
        confirmed(1001, -1, 6.85, at + 100_000),
        confirmed(1002, 1, 6.39, at + 100_000),
    ]);
    let mut levels = unified_structure_levels("TEST", &states, &[], 6.6);
    assert_eq!(levels.len(), 302);
    assert!(levels.iter().all(|l| l.lower.is_finite()
        && l.upper.is_finite()
        && l.lower <= l.price
        && l.upper >= l.price));
    let mut tracks = levels
        .iter()
        .cloned()
        .map(|level| UnifiedLevelTrack {
            level,
            lifecycle: LevelLifecycle::Active,
            last_relation: 0,
        })
        .collect::<Vec<_>>();
    for track in &mut tracks {
        track.level.hold_quality_score = 0.0;
        track.level.hold_probability = 0.0;
    }
    prune_unified_tracks(&mut tracks, 6.6);
    assert_eq!(tracks.len(), 302);
    levels.sort_by_key(|level| level.unified_level_id);
    for state in &mut states {
        if let Some(swing) = state.active_high.as_mut().or(state.active_low.as_mut()) {
            swing.strength = 0.0;
            swing.confidence = 0.0;
        }
    }
    let mut rescored = unified_structure_levels("TEST", &states, &[], 6.6);
    rescored.sort_by_key(|level| level.unified_level_id);
    let geometry = |rows: &[UnifiedStructureLevel]| {
        rows.iter()
            .map(|l| (l.unified_level_id, l.side, l.price, l.lower, l.upper))
            .collect::<Vec<_>>()
    };
    assert_eq!(geometry(&levels), geometry(&rescored));
}

#[test]
fn unfinished_broken_and_subsecond_only_pivots_do_not_found_levels() {
    let at = 1_700_000_000_000;
    let mut unfinished = confirmed(1, -1, 10.0, at);
    unfinished.active_high.as_mut().unwrap().confirmed_at = None;
    let mut broken = confirmed(2, -1, 11.0, at);
    broken.active_high.as_mut().unwrap().broken = true;
    let mut noise = confirmed(3, -1, 12.0, at);
    noise.timeframe = "100ms".into();
    assert!(unified_structure_levels("TEST", &[unfinished, broken, noise], &[], 10.0).is_empty());
}

#[test]
fn price_clusters_do_not_chain_across_distinct_resistances() {
    let at = 1_700_000_000_000;
    let states = [
        confirmed(1, -1, 10.0, at),
        confirmed(2, -1, 10.015, at + 1000),
        confirmed(3, -1, 10.03, at + 2000),
    ];
    let levels = unified_structure_levels("TEST", &states, &[], 10.0);
    assert_eq!(levels.len(), 2);
    assert!(levels.iter().all(|l| l.upper - l.lower <= 0.040001));
}

#[test]
fn old_checkpoints_cannot_seed_the_new_construction_algorithm() {
    let engine = GenericStructureEngine::new("TEST");
    let mut checkpoint = engine.checkpoint();
    checkpoint.algorithm_version = 17;
    let mut next = GenericStructureEngine::new("TEST");
    next.seed_checkpoint(&checkpoint);
    assert_eq!(next.checkpoint().algorithm_version, 18);
    assert!(checkpoint
        .migrate_completed_session_extrema(NaiveDate::from_ymd_opt(2026, 8, 21).unwrap())
        .is_err());
}

#[test]
fn confirmed_native_recurrence_retains_geometry_independent_of_hold_statistics() {
    let at = 1_700_000_000_000;
    let mut sources = vec![
        event_native_level(1, 1, 9.0, at),
        event_native_level(2, 1, 9.005, at + 10_000),
    ];
    let before = unified_structure_levels("TEST", &[], &sources, 9.1);
    assert_eq!(before.len(), 1);
    for source in &mut sources {
        source.hold_count = 0;
        source.role_flip_count = 0;
        source.touch_count = 0;
        source.accepted_break_count = 1000;
    }
    let after = unified_structure_levels("TEST", &[], &sources, 9.1);
    assert_eq!(before[0].unified_level_id, after[0].unified_level_id);
    assert_eq!(before[0].price, after[0].price);
    assert_eq!(before[0].lower, after[0].lower);
    assert_eq!(before[0].upper, after[0].upper);
}
