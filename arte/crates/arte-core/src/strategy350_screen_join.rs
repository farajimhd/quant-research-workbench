//! Conservative Strategy 350 bar and signal screen with explicit Watchlist policy.
//! The output schedules event/quote refinement; it never authorizes an order.
use crate::{
    bar_catalogue, boolean_catalogue, content_hash,
    coverage::Interval,
    exact_bars,
    execution_interval::{ExecutableKind, ExecutionInterval},
    strategy350_bar_screen::{self, ScreenBatch, ScreenIdentity, StreamingScreen},
    strategy350_catalogue::{WatchlistPolicy, SIGNAL, WATCHLIST},
    strategy350_price_gate::PriceFact,
    strategy350_signal, Error, Result,
};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
pub mod parallel;

pub struct SelectedBatch {
    scope: crate::event_order::Scope,
    first_start_ns: u64,
    refine: Vec<bool>,
    screen_config_hash: [u8; 32],
    signal_config_hash: [u8; 32],
    watchlist_config_hash: Option<[u8; 32]>,
    /// Exact verified inputs and this batch's refinement mask, not an order proof.
    evidence_hash: String,
}
/// A true 100 ms historical screen bucket. This selects an exact event/quote
/// replay range; it never substitutes for causal event or order evidence.
pub struct HistoricalSelectedBucket<'a> {
    scope: crate::event_order::Scope,
    start_ns: u64,
    row_index: usize,
    batch_evidence_hash: &'a str,
}
impl SelectedBatch {
    pub fn scope(&self) -> crate::event_order::Scope {
        self.scope
    }
    pub fn first_start_ns(&self) -> u64 {
        self.first_start_ns
    }
    pub fn refine(&self) -> &[bool] {
        &self.refine
    }
    pub fn evidence_hash(&self) -> &str {
        &self.evidence_hash
    }
    pub fn selected_bucket(&self, index: usize) -> Result<Option<HistoricalSelectedBucket<'_>>> {
        let selected = *self
            .refine
            .get(index)
            .ok_or_else(|| Error::Invalid("Strategy 350 selected bucket index".into()))?;
        if !selected {
            return Ok(None);
        }
        let offset_ns = (index as u64)
            .checked_mul(bar_catalogue::BASE_INTERVAL_NS)
            .ok_or_else(|| Error::Capacity("Strategy 350 selected bucket clock".into()))?;
        let start_ns = self
            .first_start_ns
            .checked_add(offset_ns)
            .ok_or_else(|| Error::Capacity("Strategy 350 selected bucket clock".into()))?;
        Ok(Some(HistoricalSelectedBucket {
            scope: self.scope,
            start_ns,
            row_index: index,
            batch_evidence_hash: &self.evidence_hash,
        }))
    }
}
impl HistoricalSelectedBucket<'_> {
    pub fn scope(&self) -> crate::event_order::Scope {
        self.scope
    }
    pub fn start_ns(&self) -> u64 {
        self.start_ns
    }
    pub fn identity_hash(&self) -> Result<String> {
        content_hash(&(
            "arte.strategy-350-historical-selected-bucket.v1",
            self.scope.provider,
            self.scope.instrument,
            self.scope.session,
            self.start_ns,
            self.row_index,
            self.batch_evidence_hash,
        ))
    }
}

/// Sparse half-open ranges for exact historical trade/quote replay. The full
/// certified screen interval must be supplied; omitted batches are rejected.
pub struct RefinementPlan {
    scope: crate::event_order::Scope,
    source_interval: Interval,
    intervals: Vec<Interval>,
    screen_config_hash: [u8; 32],
    signal_config_hash: [u8; 32],
    watchlist_config_hash: Option<[u8; 32]>,
    evidence_hash: String,
}
/// Lazily derives run-pinned trade proofs from a bounded sparse schedule.
/// Quotes stay on the complete market tape and never become trade proofs.
pub struct HistoricalRefinement<'a, 'p> {
    source: &'a crate::market_structure::scheduler::playback::sources::HistoricalSource<'p>,
    positions: Vec<(usize, usize)>,
    cursor: usize,
    plan_hash: String,
}
impl HistoricalRefinement<'_, '_> {
    pub fn plan_hash(&self) -> &str {
        &self.plan_hash
    }
    pub fn remaining(&self) -> usize {
        self.positions.len() - self.cursor
    }
    /// A selected proof becomes visible only at the matching pending market
    /// boundary of the same pinned run. Other boundaries leave the cursor still.
    pub fn next_for_pending(
        &mut self,
        run: &crate::market_structure::scheduler::playback::accounts::Run,
    ) -> Result<Option<crate::market_structure::scheduler::playback::sources::HistoricalEventProof>>
    {
        let proof = self.peek_for_pending(run)?;
        if proof.is_some() {
            self.cursor += 1;
        }
        Ok(proof)
    }
    /// Inspect the next selected trade without consuming its proof. A caller
    /// can validate all calculation inputs before committing this cursor.
    pub fn peek_for_pending(
        &self,
        run: &crate::market_structure::scheduler::playback::accounts::Run,
    ) -> Result<Option<crate::market_structure::scheduler::playback::sources::HistoricalEventProof>>
    {
        if run.run_id() != self.source.run_id()
            || run.manifest_hash() != self.source.manifest_hash()
            || run.prepared_hash() != self.source.prepared().hash()
        {
            return Err(Error::Conflict(
                "Strategy 350 refinement playback run differs".into(),
            ));
        }
        let boundary = run
            .pending()?
            .ok_or_else(|| Error::Unready("Strategy 350 playback boundary absent".into()))?;
        let Some(&(frame, input)) = self.positions.get(self.cursor) else {
            return Ok(None);
        };
        let proof = self.source.event(frame, input)?;
        if !matches_pending_trade(&proof, &boundary)? {
            return Ok(None);
        }
        Ok(Some(proof))
    }
    #[cfg(test)]
    fn next_trade_unchecked(
        &mut self,
    ) -> Result<Option<crate::market_structure::scheduler::playback::sources::HistoricalEventProof>>
    {
        let Some(&(frame, input)) = self.positions.get(self.cursor) else {
            return Ok(None);
        };
        let proof = self.source.event(frame, input)?;
        self.cursor += 1;
        Ok(Some(proof))
    }
}
fn matches_pending_trade(
    proof: &crate::market_structure::scheduler::playback::sources::HistoricalEventProof,
    boundary: &crate::market_structure::scheduler::Boundary<'_>,
) -> Result<bool> {
    let crate::market_structure::scheduler::Kind::Trade {
        observation,
        eligible,
    } = &boundary.kind
    else {
        return Ok(false);
    };
    if &observation.key != proof.key() {
        return Ok(false);
    }
    if boundary.evaluated_at_ns != proof.evaluated_at_ns()
        || *eligible != proof.eligible()
        || content_hash(*observation)? != proof.event_hash()
    {
        return Err(Error::Conflict(
            "Strategy 350 pending trade differs from run proof".into(),
        ));
    }
    Ok(true)
}
impl RefinementPlan {
    pub fn bind_historical<'a, 'p>(
        &self,
        source: &'a crate::market_structure::scheduler::playback::sources::HistoricalSource<'p>,
        maximum_selected: usize,
    ) -> Result<HistoricalRefinement<'a, 'p>> {
        let prepared = source.prepared();
        let mut positions = self.selected_prepared_positions(prepared, maximum_selected)?;
        positions.retain(|&(frame, input)| {
            prepared.frames()[frame].inputs[input].observation.key.kind
                == crate::events::EventKind::Trade
        });
        Ok(HistoricalRefinement {
            source,
            positions,
            cursor: 0,
            plan_hash: self.evidence_hash.clone(),
        })
    }
    pub fn from_batches(
        scope: crate::event_order::Scope,
        source_interval: Interval,
        batches: &[SelectedBatch],
        maximum_intervals: usize,
    ) -> Result<Self> {
        source_interval.validate()?;
        if scope.provider == 0
            || scope.instrument == 0
            || batches.is_empty()
            || batches.len() > 100_000
            || maximum_intervals == 0
            || maximum_intervals > 1_000_000
            || !source_interval
                .start
                .is_multiple_of(bar_catalogue::BASE_INTERVAL_NS)
            || !source_interval
                .end
                .is_multiple_of(bar_catalogue::BASE_INTERVAL_NS)
        {
            return Err(Error::Invalid("Strategy 350 refinement plan bounds".into()));
        }
        let mut cursor = source_interval.start;
        let screen_config_hash = batches[0].screen_config_hash;
        let signal_config_hash = batches[0].signal_config_hash;
        let watchlist_config_hash = batches[0].watchlist_config_hash;
        let mut intervals: Vec<Interval> = Vec::new();
        let mut digest = Sha256::new();
        digest.update(b"arte.strategy-350-refinement-plan.v2");
        digest.update(scope.provider.to_be_bytes());
        digest.update(scope.instrument.to_be_bytes());
        digest.update(scope.session.to_be_bytes());
        digest.update(source_interval.start.to_be_bytes());
        digest.update(source_interval.end.to_be_bytes());
        digest.update(screen_config_hash);
        digest.update(signal_config_hash);
        digest.update([u8::from(watchlist_config_hash.is_some())]);
        if let Some(hash) = watchlist_config_hash {
            digest.update(hash);
        }
        for batch in batches {
            let count = batch.refine.len();
            let span_ns = (count as u64)
                .checked_mul(bar_catalogue::BASE_INTERVAL_NS)
                .ok_or_else(|| Error::Capacity("Strategy 350 refinement batch clock".into()))?;
            let end_ns = cursor
                .checked_add(span_ns)
                .ok_or_else(|| Error::Capacity("Strategy 350 refinement batch clock".into()))?;
            if batch.scope != scope
                || batch.screen_config_hash != screen_config_hash
                || batch.signal_config_hash != signal_config_hash
                || batch.watchlist_config_hash != watchlist_config_hash
                || batch.first_start_ns != cursor
                || count == 0
                || end_ns > source_interval.end
            {
                return Err(Error::Conflict(
                    "Strategy 350 refinement batch gap or scope".into(),
                ));
            }
            digest.update(batch.first_start_ns.to_be_bytes());
            digest.update((count as u64).to_be_bytes());
            digest.update(batch.evidence_hash.as_bytes());
            for (index, &refine) in batch.refine.iter().enumerate() {
                if !refine {
                    continue;
                }
                let start_ns = cursor + index as u64 * bar_catalogue::BASE_INTERVAL_NS;
                let end_ns = start_ns + bar_catalogue::BASE_INTERVAL_NS;
                if let Some(last) = intervals.last_mut().filter(|last| last.end == start_ns) {
                    last.end = end_ns;
                } else {
                    if intervals.len() == maximum_intervals {
                        return Err(Error::Capacity(
                            "Strategy 350 refinement interval budget".into(),
                        ));
                    }
                    intervals.push(Interval {
                        start: start_ns,
                        end: end_ns,
                    });
                }
            }
            cursor = end_ns;
        }
        if cursor != source_interval.end {
            return Err(Error::Unready(
                "Strategy 350 refinement source interval incomplete".into(),
            ));
        }
        for interval in &intervals {
            digest.update(interval.start.to_be_bytes());
            digest.update(interval.end.to_be_bytes());
        }
        Ok(Self {
            scope,
            source_interval,
            intervals,
            screen_config_hash,
            signal_config_hash,
            watchlist_config_hash,
            evidence_hash: format!("{:x}", digest.finalize()),
        })
    }
    pub fn scope(&self) -> crate::event_order::Scope {
        self.scope
    }
    pub fn source_interval(&self) -> Interval {
        self.source_interval
    }
    pub fn intervals(&self) -> &[Interval] {
        &self.intervals
    }
    pub fn evidence_hash(&self) -> &str {
        &self.evidence_hash
    }
    pub fn configuration_hashes(&self) -> ([u8; 32], [u8; 32], Option<[u8; 32]>) {
        (
            self.screen_config_hash,
            self.signal_config_hash,
            self.watchlist_config_hash,
        )
    }
    /// Test a run-pinned historical trade against the sparse screen ranges.
    /// A false result only skips expensive Strategy 350 refinement; market
    /// replay and V7 state must still consume the complete source.
    pub fn contains_replay_event(
        &self,
        event: &crate::market_structure::scheduler::playback::sources::HistoricalEventProof,
    ) -> Result<bool> {
        self.contains_source_time(event.scope(), event.source_time_ns())
    }
    /// Build a reusable sparse index after the complete historical source has
    /// been certified. This never narrows market/V7 replay or source loading.
    pub fn selected_source_indices(
        &self,
        observations: &[crate::events::Observation],
        kind: crate::events::EventKind,
        maximum_selected: usize,
    ) -> Result<Vec<usize>> {
        if maximum_selected == 0 || maximum_selected > 10_000_000 {
            return Err(Error::Capacity(
                "Strategy 350 refinement event budget".into(),
            ));
        }
        let mut selected = Vec::new();
        for (index, event) in observations.iter().enumerate() {
            event.validate()?;
            let scope = crate::event_order::Scope {
                provider: event.key.provider,
                instrument: event.key.instrument,
                session: event.key.session,
            };
            if event.key.kind != kind
                || event.receipt.is_some()
                || matches!(
                    event.payload,
                    crate::events::Payload::Trade {
                        correction: Some(_),
                        ..
                    }
                )
            {
                return Err(Error::Conflict(
                    "Strategy 350 refinement source type or clock".into(),
                ));
            }
            if self.contains_source_time(scope, event.sip.ns)? {
                if selected.len() == maximum_selected {
                    return Err(Error::Capacity("Strategy 350 selected events".into()));
                }
                selected.push(index);
            }
        }
        selected.sort_unstable_by(|&left, &right| {
            let a = &observations[left];
            let b = &observations[right];
            (a.sip.ns, a.key.sequence, &a.key).cmp(&(b.sip.ns, b.key.sequence, &b.key))
        });
        Ok(selected)
    }
    /// Return locations in the complete modeled playback tape. Market/V7 still
    /// processes every frame; only expensive strategy refinement uses this list.
    pub fn selected_prepared_positions(
        &self,
        prepared: &crate::market_structure::scheduler::playback::Prepared,
        maximum_selected: usize,
    ) -> Result<Vec<(usize, usize)>> {
        if prepared.scope() != self.scope {
            return Err(Error::Conflict("Strategy 350 prepared scope".into()));
        }
        prepared.require_interval(self.source_interval)?;
        if maximum_selected == 0 || maximum_selected > 10_000_000 {
            return Err(Error::Capacity(
                "Strategy 350 prepared selection budget".into(),
            ));
        }
        let mut positions = Vec::new();
        for (frame_index, frame) in prepared.frames().iter().enumerate() {
            for (input_index, input) in frame.inputs.iter().enumerate() {
                let event = &input.observation;
                if event.receipt.is_some()
                    || matches!(
                        event.payload,
                        crate::events::Payload::Trade {
                            correction: Some(_),
                            ..
                        }
                    )
                {
                    return Err(Error::Conflict(
                        "Strategy 350 prepared source is not historical".into(),
                    ));
                }
                if self.contains_source_time(self.scope, event.sip.ns)? {
                    if positions.len() == maximum_selected {
                        return Err(Error::Capacity(
                            "Strategy 350 prepared selected inputs".into(),
                        ));
                    }
                    positions.push((frame_index, input_index));
                }
            }
        }
        Ok(positions)
    }
    fn contains_source_time(&self, scope: crate::event_order::Scope, at: u64) -> Result<bool> {
        if scope != self.scope || at < self.source_interval.start || at >= self.source_interval.end
        {
            return Err(Error::Conflict(
                "Strategy 350 replay event outside screen authority".into(),
            ));
        }
        let index = self
            .intervals
            .partition_point(|interval| interval.end <= at);
        Ok(self
            .intervals
            .get(index)
            .is_some_and(|interval| at >= interval.start && at < interval.end))
    }
}

/// A completed live bucket selected for exact event/quote refinement. This
/// does not certify feed completeness or authorize an account decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LiveSelectedBucket {
    scope: crate::event_order::Scope,
    start_ns: u64,
    available_at_ns: u64,
    screen_possible: bool,
    signal_active: bool,
    screen_config_hash: [u8; 32],
    signal_config_hash: [u8; 32],
    selection_identity: [u8; 32],
}
impl LiveSelectedBucket {
    pub fn scope(&self) -> crate::event_order::Scope {
        self.scope
    }
    pub fn start_ns(&self) -> u64 {
        self.start_ns
    }
    pub fn available_at_ns(&self) -> u64 {
        self.available_at_ns
    }
    pub fn needs_refinement(&self) -> bool {
        self.screen_possible && self.signal_active
    }
    pub fn configuration_hashes(&self) -> ([u8; 32], [u8; 32], Option<[u8; 32]>) {
        (self.screen_config_hash, self.signal_config_hash, None)
    }
    pub fn identity_hash(&self) -> Result<String> {
        content_hash(&(
            "arte.strategy-350-live-selected-bucket.v2",
            self.scope.provider,
            self.scope.instrument,
            self.scope.session,
            self.start_ns,
            self.available_at_ns,
            self.screen_possible,
            self.signal_active,
            self.screen_config_hash,
            self.signal_config_hash,
            self.selection_identity,
        ))
    }
}

#[cfg(test)]
pub(crate) fn test_live_selected(
    scope: crate::event_order::Scope,
    start_ns: u64,
    available_at_ns: u64,
) -> LiveSelectedBucket {
    test_live_selected_with_hashes(scope, start_ns, available_at_ns, [0xbb; 32], [0xaa; 32])
}

#[cfg(test)]
pub(crate) fn test_live_selected_with_hashes(
    scope: crate::event_order::Scope,
    start_ns: u64,
    available_at_ns: u64,
    screen_config_hash: [u8; 32],
    signal_config_hash: [u8; 32],
) -> LiveSelectedBucket {
    LiveSelectedBucket {
        scope,
        start_ns,
        available_at_ns,
        screen_possible: true,
        signal_active: true,
        screen_config_hash,
        signal_config_hash,
        selection_identity: [0; 32],
    }
}

#[cfg(test)]
pub(crate) fn test_historical_refinement(
    scope: crate::event_order::Scope,
    source_interval: Interval,
) -> RefinementPlan {
    RefinementPlan::from_batches(
        scope,
        source_interval,
        &[SelectedBatch {
            scope,
            first_start_ns: source_interval.start,
            refine: vec![
                true;
                ((source_interval.end - source_interval.start) / bar_catalogue::BASE_INTERVAL_NS)
                    as usize
            ],
            evidence_hash: "a".repeat(64),
            screen_config_hash: [0xbb; 32],
            signal_config_hash: [0xaa; 32],
            watchlist_config_hash: None,
        }],
        1,
    )
    .unwrap()
}

/// One ticker-owned live join. Both calculations advance on the identical
/// sealed exact-bar step; neither state commits if the other rejects it.
pub struct StreamingJoin {
    scope: crate::event_order::Scope,
    screen: StreamingScreen,
    signal: strategy350_signal::State,
    screen_config_hash: [u8; 32],
    signal_config_hash: [u8; 32],
    selection_identity: [u8; 32],
}
impl StreamingJoin {
    pub fn new_live(
        identity: ScreenIdentity,
        screen_config: &strategy350_bar_screen::Config,
        expected_screen_hash: &str,
        prior_close: &PriceFact,
        signal_config: strategy350_signal::Config,
        expected_signal_hash: &str,
        watchlist_policy: WatchlistPolicy,
    ) -> Result<Self> {
        if identity.source != exact_bars::Mode::Live
            || watchlist_policy != WatchlistPolicy::NotRequired
        {
            return Err(Error::Unready(
                "Strategy 350 live join source or Watchlist producer".into(),
            ));
        }
        if signal_config.hash()? != expected_signal_hash {
            return Err(Error::Conflict(
                "Strategy 350 live signal configuration differs".into(),
            ));
        }
        let scope = identity.scope;
        let start_ns = identity.session_start_ns;
        let end_ns = identity.session_end_ns;
        let source_hash = identity.source_bar_hash.clone();
        let identity_bytes = serde_json::to_vec(&(
            "arte.strategy-350-live-selection-identity.v1",
            scope.provider,
            scope.instrument,
            scope.session,
            start_ns,
            end_ns,
            identity.price_scale,
            source_hash.as_str(),
            expected_screen_hash,
            expected_signal_hash,
            prior_close,
        ))
        .map_err(|e| Error::Serialization(e.to_string()))?;
        let selection_identity: [u8; 32] = Sha256::digest(identity_bytes).into();
        let screen =
            StreamingScreen::new(identity, screen_config, expected_screen_hash, prior_close)?;
        let signal =
            strategy350_signal::State::new_live(signal_config, source_hash, start_ns, end_ns)?;
        Ok(Self {
            scope,
            screen,
            signal,
            screen_config_hash: crate::strategy350_effective::hash_bytes(expected_screen_hash)?,
            signal_config_hash: crate::strategy350_effective::hash_bytes(expected_signal_hash)?,
            selection_identity,
        })
    }

    pub fn observe_live_advance(
        &mut self,
        advance: &exact_bars::Advance<'_>,
        available_at_ns: u64,
    ) -> Result<Vec<LiveSelectedBucket>> {
        let mut screen = self.screen.clone();
        let mut signal = self.signal.clone();
        let screen_points = screen.observe_live_advance(advance)?;
        let signal_points = signal.observe_live_advance_buckets(advance, available_at_ns)?;
        if screen_points.len() != signal_points.len() {
            return Err(Error::Conflict(
                "Strategy 350 live screen and signal bucket count".into(),
            ));
        }
        let mut output = Vec::with_capacity(screen_points.len());
        for (screen_point, signal_point) in screen_points.into_iter().zip(signal_points) {
            if screen_point.scope != self.scope
                || screen_point.source != exact_bars::Mode::Live
                || screen_point.start_ns != signal_point.bucket_start_ns()
                || screen_point
                    .start_ns
                    .checked_add(bar_catalogue::BASE_INTERVAL_NS)
                    .is_none_or(|end| end > signal_point.available_at_ns())
            {
                return Err(Error::Conflict(
                    "Strategy 350 live screen and signal bucket alignment".into(),
                ));
            }
            output.push(LiveSelectedBucket {
                scope: self.scope,
                start_ns: screen_point.start_ns,
                available_at_ns: signal_point.available_at_ns(),
                screen_possible: screen_point.needs_refinement,
                signal_active: signal_point.active(),
                screen_config_hash: self.screen_config_hash,
                signal_config_hash: self.signal_config_hash,
                selection_identity: self.selection_identity,
            });
        }
        self.screen = screen;
        self.signal = signal;
        Ok(output)
    }
}

struct BooleanCursor<'a> {
    batches: &'a [boolean_catalogue::Batch],
    batch: usize,
    row: usize,
}
impl<'a> BooleanCursor<'a> {
    fn new(product: &'a boolean_catalogue::Complete) -> Self {
        Self {
            batches: product.batches(),
            batch: 0,
            row: 0,
        }
    }
    fn next(&mut self) -> Result<(bool, bool, bool)> {
        while self.batch < self.batches.len() && self.row == self.batches[self.batch].count as usize
        {
            self.batch += 1;
            self.row = 0;
        }
        let batch = self
            .batches
            .get(self.batch)
            .ok_or_else(|| Error::Conflict("Strategy 350 Boolean grid exhausted".into()))?;
        let row = self.row;
        self.row += 1;
        Ok((batch.evaluated[row], batch.known[row], batch.value[row]))
    }
    fn finished(mut self) -> bool {
        while self.batch < self.batches.len() && self.row == self.batches[self.batch].count as usize
        {
            self.batch += 1;
            self.row = 0;
        }
        self.batch == self.batches.len()
    }
}

fn aligned(
    product: &boolean_catalogue::Complete,
    bar: &bar_catalogue::Complete,
    kind: fn(&ExecutableKind) -> bool,
    id: &str,
) -> Result<()> {
    let r = product.request();
    let b = bar.request();
    if !kind(&r.definition.kind)
        || r.definition.id != id
        || r.definition.interval != ExecutionInterval::Fixed(bar_catalogue::BASE_INTERVAL_NS)
        || r.provider != b.provider
        || r.instrument != b.instruments[0]
        || r.session != b.session
        || r.interval != b.interval
        || r.source_bar_request_hash != b.hash()?
        || r.source_bar_coverage_hash != bar.coverage_hash()
    {
        return Err(Error::Conflict(
            "Strategy 350 Boolean product alignment".into(),
        ));
    }
    Ok(())
}
pub fn select(
    bar: &bar_catalogue::Complete,
    config: &strategy350_bar_screen::Config,
    prior_close: &PriceFact,
    signal: &boolean_catalogue::Complete,
    watchlist_policy: WatchlistPolicy,
    watchlist: Option<&boolean_catalogue::Complete>,
) -> Result<Vec<SelectedBatch>> {
    if bar.request().instruments.len() != 1 {
        return Err(Error::Invalid(
            "Strategy 350 join requires one instrument shard".into(),
        ));
    }
    aligned(
        signal,
        bar,
        |kind| matches!(kind, ExecutableKind::SignalStream),
        SIGNAL,
    )?;
    let watchlist = match watchlist_policy {
        WatchlistPolicy::NotRequired if watchlist.is_none() => None,
        WatchlistPolicy::Required => {
            let product =
                watchlist.ok_or_else(|| Error::Unready("required Watchlist missing".into()))?;
            aligned(
                product,
                bar,
                |kind| matches!(kind, ExecutableKind::Watchlist),
                WATCHLIST,
            )?;
            Some(product)
        }
        WatchlistPolicy::NotRequired => {
            return Err(Error::Invalid("unused Watchlist supplied".into()))
        }
    };
    let instrument = bar.request().instruments[0];
    let closes = BTreeMap::from([(
        instrument,
        PriceFact {
            value: prior_close.value,
            available_at_ns: prior_close.available_at_ns,
            source_hash: prior_close.source_hash.clone(),
            source_order: prior_close.source_order,
        },
    )]);
    let screen = strategy350_bar_screen::project(bar, config, &closes)?;
    if screen.len() != bar.batches().len() {
        return Err(Error::Conflict(
            "Strategy 350 bar screen batch count".into(),
        ));
    }
    let screen_hash = config.hash()?;
    let source_hash = content_hash(&(
        "arte.strategy-350-screen-input.v1",
        bar.request().hash()?,
        bar.coverage_hash(),
        signal.request().hash()?,
        signal.coverage_hash(),
        watchlist
            .map(|product| product.request().hash())
            .transpose()?,
        watchlist.map(boolean_catalogue::Complete::coverage_hash),
        &screen_hash,
        prior_close,
    ))?;
    let screen_config_hash = crate::strategy350_effective::hash_bytes(&screen_hash)?;
    let signal_config_hash =
        crate::strategy350_effective::hash_bytes(&signal.request().definition.implementation_hash)?;
    let watchlist_config_hash = watchlist
        .map(|product| {
            crate::strategy350_effective::hash_bytes(
                &product.request().definition.implementation_hash,
            )
        })
        .transpose()?;
    let mut signal_values = BooleanCursor::new(signal);
    let mut watch_values = watchlist.map(BooleanCursor::new);
    let mut output = Vec::with_capacity(screen.len());
    for (
        ScreenBatch {
            instrument,
            first_start_ns,
            needs_refinement,
            late_before,
            prior_high_atoms,
        },
        raw_bar,
    ) in screen.into_iter().zip(bar.batches())
    {
        if raw_bar.instrument != instrument
            || raw_bar.first_start_ns != first_start_ns
            || raw_bar.count as usize != needs_refinement.len()
            || late_before.len() != needs_refinement.len()
            || prior_high_atoms.len() != needs_refinement.len()
        {
            return Err(Error::Conflict("Strategy 350 bar screen alignment".into()));
        }
        let (open, high, low) = raw_bar
            .open
            .as_ref()
            .zip(raw_bar.high.as_ref())
            .zip(raw_bar.low.as_ref())
            .map(|((open, high), low)| (open, high, low))
            .ok_or_else(|| Error::Unready("Strategy 350 OHLC screen columns".into()))?;
        let mut hasher = Sha256::new();
        hasher.update(source_hash.as_bytes());
        hasher.update(instrument.to_be_bytes());
        hasher.update(first_start_ns.to_be_bytes());
        hasher.update(raw_bar.count.to_be_bytes());
        let mut refine = Vec::with_capacity(needs_refinement.len());
        for (index, bar_possible) in needs_refinement.into_iter().enumerate() {
            let (signal_evaluated, signal_known, signal_true) = signal_values.next()?;
            let watch = watch_values.as_mut().map(BooleanCursor::next).transpose()?;
            let (watch_known, watch_true) =
                watch.map_or((true, true), |(_, known, value)| (known, value));
            if bar_possible
                && (!signal_evaluated
                    || !signal_known
                    || watch.is_some_and(|(evaluated, _, _)| !evaluated)
                    || !watch_known)
            {
                return Err(Error::Unready(
                    "Strategy 350 required signal or Watchlist unevaluated or unknown".into(),
                ));
            }
            let selected = bar_possible && signal_true && watch_true;
            hasher.update([
                u8::from(raw_bar.present[index]),
                u8::from(bar_possible),
                u8::from(late_before[index]),
                u8::from(signal_evaluated),
                u8::from(signal_known),
                u8::from(signal_true),
                u8::from(watch.is_some()),
                u8::from(watch.is_some_and(|(evaluated, _, _)| evaluated)),
                u8::from(watch_known),
                u8::from(watch_true),
                u8::from(selected),
            ]);
            for value in [
                open[index],
                high[index],
                low[index],
                prior_high_atoms[index],
            ] {
                hasher.update(value.to_be_bytes());
            }
            refine.push(selected);
        }
        output.push(SelectedBatch {
            scope: crate::event_order::Scope {
                provider: bar.request().provider,
                instrument,
                session: bar.request().session,
            },
            first_start_ns,
            refine,
            screen_config_hash,
            signal_config_hash,
            watchlist_config_hash,
            evidence_hash: format!("{:x}", hasher.finalize()),
        });
    }
    if !signal_values.finished() || watch_values.is_some_and(|cursor| !cursor.finished()) {
        return Err(Error::Conflict("Strategy 350 Boolean grid length".into()));
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Decimal;
    use crate::{
        bar_catalogue::{
            Batch as BarBatch, Column, Coverage as BarCoverage, Readback as BarReadback,
            Request as BarRequest, Source,
        },
        boolean_catalogue::{
            Batch as BoolBatch, Coverage as BoolCoverage, Readback as BoolReadback,
            Request as BoolRequest,
        },
        coverage::Interval,
        execution_interval::{ExecutionContract, ExecutionInterval},
    };
    use std::collections::BTreeSet;
    const S: u64 = 1_000_000_000;
    fn bar() -> bar_catalogue::Complete {
        bar_for(10)
    }
    fn bar_for(instrument: u64) -> bar_catalogue::Complete {
        let interval = Interval {
            start: S,
            end: S + 300_000_000,
        };
        let request = BarRequest {
            provider: 1,
            instruments: vec![instrument],
            session: 20260922,
            interval,
            timeframe_ns: 100_000_000,
            source_generation: "a".repeat(64),
            calculation_hash: "b".repeat(64),
            columns: BTreeSet::from([Column::Open, Column::High, Column::Low]),
            maximum_rows: 3,
        };
        let coverage = BarCoverage {
            provider: 1,
            session: 20260922,
            interval,
            timeframe_ns: 100_000_000,
            source_generation: request.source_generation.clone(),
            calculation_hash: request.calculation_hash.clone(),
            sources: BTreeMap::from([(
                instrument,
                Source {
                    certificate_hash: "c".repeat(64),
                    price_scale: 2,
                    size_scale: 2,
                },
            )]),
            published_at_ns: 2 * S,
        };
        let mut read = BarReadback::new(request.clone(), &coverage, 2 * S).unwrap();
        read.observe(BarBatch {
            request_hash: request.hash().unwrap(),
            coverage_hash: coverage.hash().unwrap(),
            instrument,
            first_start_ns: S,
            count: 3,
            price_scale: 2,
            size_scale: 2,
            present: vec![true, true, true],
            open: Some(vec![1000, 1150, 1050]),
            high: Some(vec![1000, 1150, 1050]),
            low: Some(vec![1000, 1150, 1050]),
            close: None,
            volume: None,
            notional: None,
            trades: None,
        })
        .unwrap();
        read.finish().unwrap()
    }
    fn boolean(
        bar: &bar_catalogue::Complete,
        kind: ExecutableKind,
        id: &str,
        known: Vec<bool>,
        value: Vec<bool>,
    ) -> boolean_catalogue::Complete {
        boolean_with_parts(bar, kind, id, known, value, &[3])
    }
    fn boolean_with_parts(
        bar: &bar_catalogue::Complete,
        kind: ExecutableKind,
        id: &str,
        known: Vec<bool>,
        value: Vec<bool>,
        parts: &[usize],
    ) -> boolean_catalogue::Complete {
        boolean_with_parts_at_cadence(bar, kind, id, known, value, parts, 100_000_000)
    }
    fn boolean_with_parts_at_cadence(
        bar: &bar_catalogue::Complete,
        kind: ExecutableKind,
        id: &str,
        known: Vec<bool>,
        value: Vec<bool>,
        parts: &[usize],
        cadence_ns: u64,
    ) -> boolean_catalogue::Complete {
        let request = BoolRequest {
            provider: 1,
            instrument: bar.request().instruments[0],
            session: 20260922,
            interval: bar.request().interval,
            definition: ExecutionContract {
                kind,
                id: id.into(),
                implementation_hash: "d".repeat(64),
                interval: ExecutionInterval::Fixed(cadence_ns),
            },
            source_bar_request_hash: bar.request().hash().unwrap(),
            source_bar_coverage_hash: bar.coverage_hash().into(),
            maximum_rows: 3,
        };
        let mut digest = boolean_catalogue::TransitionDigest::new(&request).unwrap();
        let mut last = None;
        for (i, (&k, &v)) in known.iter().zip(&value).enumerate() {
            let next = k.then_some(v);
            if next != last {
                digest.observe(S + i as u64 * 100_000_000, k, v).unwrap();
            }
            last = next;
        }
        let (transition_hash, transition_count) = digest.finish();
        let coverage = BoolCoverage {
            request_hash: request.hash().unwrap(),
            source_bar_coverage_hash: bar.coverage_hash().into(),
            producer_hash: "d".repeat(64),
            transition_hash,
            transition_count,
            published_at_ns: 3 * S,
        };
        let mut read = BoolReadback::new(request, &coverage, 3 * S).unwrap();
        let mut offset = 0;
        for &count in parts {
            let end = offset + count;
            read.observe(BoolBatch {
                request_hash: coverage.request_hash.clone(),
                coverage_hash: coverage.hash().unwrap(),
                first_start_ns: S + offset as u64 * 100_000_000,
                count: count as u32,
                evaluated: (offset..end)
                    .map(|i| (S + (i as u64 + 1) * 100_000_000).is_multiple_of(cadence_ns))
                    .collect(),
                known: known[offset..end].to_vec(),
                value: value[offset..end].to_vec(),
            })
            .unwrap();
            offset = end;
        }
        read.finish().unwrap()
    }
    fn config() -> strategy350_bar_screen::Config {
        strategy350_bar_screen::Config {
            execution_interval: ExecutionInterval::Fixed(100_000_000),
            prior_close_source_hash: "e".repeat(64),
            prior_close_max: Decimal::parse("20").unwrap(),
            purchase_min: Decimal::parse("1").unwrap(),
            late_gain_bps: 1500,
            hod_floor_bps: 7000,
        }
    }
    fn close() -> PriceFact {
        PriceFact {
            value: Decimal::parse("19").unwrap(),
            available_at_ns: S - 1,
            source_hash: "e".repeat(64),
            source_order: None,
        }
    }
    #[test]
    fn parallel_screen_plans_sort_shards_and_enforce_effective_budgets() {
        let bars10 = bar_for(10);
        let bars20 = bar_for(20);
        let signal10 = boolean(
            &bars10,
            ExecutableKind::SignalStream,
            SIGNAL,
            vec![true; 3],
            vec![true; 3],
        );
        let signal20 = boolean(
            &bars20,
            ExecutableKind::SignalStream,
            SIGNAL,
            vec![true; 3],
            vec![true; 3],
        );
        let screen = config();
        let prior_close = close();
        let mut effective = crate::strategy350_effective::test_config(ExecutionInterval::Events);
        effective.screen_config_hash = screen.hash().unwrap();
        effective.signal_config_hash = "d".repeat(64);
        let pin = |bars, signal| parallel::Pinned {
            bars,
            signal,
            watchlist: None,
            screen: &screen,
            prior_close: &prior_close,
            effective: &effective,
            watchlist_policy: WatchlistPolicy::NotRequired,
        };
        let limits = || parallel::Limits {
            workers: 2,
            maximum_intervals_per_shard: 3,
            maximum_total_intervals: 6,
            maximum_selected_buckets_per_shard: 3,
            maximum_total_selected_buckets: 6,
        };
        let projected = parallel::prepare_many(
            vec![pin(&bars20, &signal20), pin(&bars10, &signal10)],
            limits(),
        )
        .unwrap();
        assert_eq!(
            projected
                .iter()
                .map(|item| item.scope.instrument)
                .collect::<Vec<_>>(),
            [10, 20]
        );
        assert!(projected.iter().all(|item| item.selected_buckets > 0));
        assert_ne!(
            projected[0].plan.evidence_hash(),
            projected[1].plan.evidence_hash()
        );
        let mut serial_limits = limits();
        serial_limits.workers = 1;
        let serial = parallel::prepare_many(
            vec![pin(&bars10, &signal10), pin(&bars20, &signal20)],
            serial_limits,
        )
        .unwrap();
        assert_eq!(
            projected
                .iter()
                .map(|item| item.plan.evidence_hash())
                .collect::<Vec<_>>(),
            serial
                .iter()
                .map(|item| item.plan.evidence_hash())
                .collect::<Vec<_>>()
        );
        assert!(parallel::prepare_many(
            vec![pin(&bars10, &signal10), pin(&bars10, &signal10)],
            limits(),
        )
        .is_err());
        let mut tight = limits();
        tight.maximum_total_selected_buckets = 1;
        assert!(parallel::prepare_many(
            vec![pin(&bars10, &signal10), pin(&bars20, &signal20)],
            tight,
        )
        .is_err());
        let mut tight_intervals = limits();
        tight_intervals.maximum_total_intervals = 1;
        assert!(parallel::prepare_many(
            vec![pin(&bars10, &signal10), pin(&bars20, &signal20)],
            tight_intervals,
        )
        .is_err());
        let mut changed = effective.clone();
        changed.signal_config_hash = "0".repeat(64);
        assert!(parallel::prepare_many(
            vec![parallel::Pinned {
                bars: &bars10,
                signal: &signal10,
                watchlist: None,
                screen: &screen,
                prior_close: &prior_close,
                effective: &changed,
                watchlist_policy: WatchlistPolicy::NotRequired,
            }],
            limits(),
        )
        .is_err());
    }
    #[test]
    fn live_join_uses_same_sealed_advance_and_preserves_bucket_activation() {
        let scope = crate::event_order::Scope {
            provider: 1,
            instrument: 10,
            session: 20260922,
        };
        let source_hash = "a".repeat(64);
        let make_join = |policy, expected_signal_hash: Option<&str>, minimum_move_bps| {
            let signal_config = strategy350_signal::Config {
                execution_interval: ExecutionInterval::Fixed(100_000_000),
                minimum_move_bps,
                source_algorithm_hash: "f".repeat(64),
            };
            let signal_hash = signal_config.hash().unwrap();
            StreamingJoin::new_live(
                ScreenIdentity {
                    scope,
                    session_start_ns: S,
                    session_end_ns: S + 400_000_000,
                    price_scale: 2,
                    source: exact_bars::Mode::Live,
                    source_bar_hash: source_hash.clone(),
                },
                &config(),
                &config().hash().unwrap(),
                &close(),
                signal_config,
                expected_signal_hash.unwrap_or(&signal_hash),
                policy,
            )
        };
        assert!(make_join(WatchlistPolicy::Required, None, 5).is_err());
        assert!(make_join(WatchlistPolicy::NotRequired, Some(&"0".repeat(64)), 5).is_err());
        let mut join = make_join(WatchlistPolicy::NotRequired, None, 5).unwrap();
        let first = exact_bars::Bar {
            start_ns: S,
            end_ns: S + 100_000_000,
            price_scale: 2,
            size_scale: 0,
            open: 1000,
            high: 1000,
            low: 1000,
            close: 1000,
            volume: 100,
            notional: 100_000,
            trades: 2,
            last_trade_source_ns: S + 1,
            last_trade_live_receipt_ns: Some(S + 2),
        };
        let first_advance = exact_bars::Advance {
            configuration_hash: &source_hash,
            previous_watermark_ns: S,
            watermark_ns: S + 100_000_000,
            completed: Some(first.clone()),
        };
        let first_result = join
            .observe_live_advance(&first_advance, S + 100_000_000)
            .unwrap();
        assert_eq!(first_result.len(), 1);
        assert_eq!(first_result[0].scope(), scope);
        assert!(!first_result[0].needs_refinement());
        let later = exact_bars::Bar {
            start_ns: S + 200_000_000,
            end_ns: S + 300_000_000,
            open: 1001,
            high: 1001,
            low: 1001,
            close: 1001,
            volume: 101,
            notional: 101_101,
            trades: 3,
            last_trade_source_ns: S + 200_000_001,
            last_trade_live_receipt_ns: Some(S + 200_000_002),
            ..first
        };
        let later_advance = exact_bars::Advance {
            configuration_hash: &source_hash,
            previous_watermark_ns: S + 100_000_000,
            watermark_ns: S + 300_000_000,
            completed: Some(later),
        };
        assert!(join
            .observe_live_advance(&later_advance, S + 299_000_000)
            .is_err());
        let selected = join
            .observe_live_advance(&later_advance, S + 300_000_000)
            .unwrap();
        assert_eq!(selected.len(), 2);
        assert_eq!(selected[0].start_ns(), S + 100_000_000);
        assert!(!selected[0].needs_refinement());
        assert_eq!(selected[1].start_ns(), S + 200_000_000);
        assert!(selected[1].needs_refinement());
        assert_eq!(selected[1].available_at_ns(), S + 300_000_000);
        assert_eq!(
            selected[1].configuration_hashes().0,
            crate::strategy350_effective::hash_bytes(&config().hash().unwrap()).unwrap()
        );
        assert_eq!(selected[1].identity_hash().unwrap().len(), 64);
        assert_ne!(
            selected[0].identity_hash().unwrap(),
            selected[1].identity_hash().unwrap()
        );
        let mut changed_config = make_join(WatchlistPolicy::NotRequired, None, 6).unwrap();
        changed_config
            .observe_live_advance(&first_advance, S + 100_000_000)
            .unwrap();
        let changed_selection = changed_config
            .observe_live_advance(&later_advance, S + 300_000_000)
            .unwrap();
        assert!(changed_selection[1].needs_refinement());
        assert_ne!(
            selected[1].identity_hash().unwrap(),
            changed_selection[1].identity_hash().unwrap(),
        );
        assert!(join
            .observe_live_advance(&later_advance, S + 300_000_000)
            .is_err());
    }
    #[test]
    fn only_known_true_signal_and_watchlist_schedule_refinement() {
        let b = bar();
        let signal = boolean(
            &b,
            ExecutableKind::SignalStream,
            SIGNAL,
            vec![true; 3],
            vec![true; 3],
        );
        let watch = boolean(
            &b,
            ExecutableKind::Watchlist,
            WATCHLIST,
            vec![true; 3],
            vec![true, false, true],
        );
        let selected = select(
            &b,
            &config(),
            &close(),
            &signal,
            WatchlistPolicy::Required,
            Some(&watch),
        )
        .unwrap();
        assert_eq!(selected[0].refine, vec![true, false, true]);
        assert_eq!(selected[0].scope().instrument, 10);
        assert_eq!(selected[0].refine(), &[true, false, true]);
        let first = selected[0].selected_bucket(0).unwrap().unwrap();
        let third = selected[0].selected_bucket(2).unwrap().unwrap();
        assert_eq!(first.start_ns(), S);
        assert_eq!(third.start_ns(), S + 200_000_000);
        assert_ne!(
            first.identity_hash().unwrap(),
            third.identity_hash().unwrap()
        );
        assert!(selected[0].selected_bucket(1).unwrap().is_none());
        assert!(selected[0].selected_bucket(3).is_err());
        let scope = selected[0].scope();
        let source_interval = b.request().interval;
        let plan = RefinementPlan::from_batches(scope, source_interval, &selected, 2).unwrap();
        assert_eq!(
            plan.configuration_hashes(),
            (
                crate::strategy350_effective::hash_bytes(&config().hash().unwrap()).unwrap(),
                crate::strategy350_effective::hash_bytes(
                    &signal.request().definition.implementation_hash
                )
                .unwrap(),
                Some(
                    crate::strategy350_effective::hash_bytes(
                        &watch.request().definition.implementation_hash
                    )
                    .unwrap()
                )
            )
        );
        assert_eq!(plan.scope(), scope);
        assert_eq!(plan.source_interval(), source_interval);
        assert_eq!(plan.intervals().len(), 2);
        assert_eq!(plan.intervals()[0].start, S);
        assert_eq!(plan.intervals()[0].end, S + 100_000_000);
        assert_eq!(plan.intervals()[1].start, S + 200_000_000);
        assert_eq!(plan.intervals()[1].end, S + 300_000_000);
        assert_eq!(plan.evidence_hash().len(), 64);
        let observation = |sequence: u64, at: u64| crate::events::Observation {
            key: crate::events::EventKey {
                provider: scope.provider,
                instrument: scope.instrument,
                session: scope.session,
                kind: crate::events::EventKind::Trade,
                sequence,
            },
            payload: crate::events::Payload::Trade {
                price: crate::events::Decimal {
                    atoms: 100,
                    scale: 2,
                },
                size: crate::events::Decimal { atoms: 1, scale: 0 },
                exchange: 1,
                trade_id: sequence.to_string(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: crate::events::SourceTime {
                ns: at,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: at + 1,
            receipt: None,
        };
        let source = vec![
            observation(4, S + 210_000_000),
            observation(3, S + 110_000_000),
            observation(2, S + 10_000_000),
            observation(1, S + 10_000_000),
        ];
        assert_eq!(
            plan.selected_source_indices(&source, crate::events::EventKind::Trade, 3)
                .unwrap(),
            vec![3, 2, 0]
        );
        assert!(plan
            .selected_source_indices(&source, crate::events::EventKind::Trade, 2)
            .is_err());
        let mut live = source.clone();
        live[0].receipt = Some(crate::events::Receipt {
            run_id: "live".into(),
            lane: 1,
            sequence: 1,
            utc_ns: live[0].available_at_ns,
            monotonic_ns: 1,
        });
        assert!(plan
            .selected_source_indices(&live, crate::events::EventKind::Trade, 3)
            .is_err());
        let mut modeled = source.clone();
        let mut quote = observation(5, S + 220_000_000);
        quote.key.kind = crate::events::EventKind::Quote;
        quote.payload = crate::events::Payload::Quote {
            bid: crate::events::Decimal {
                atoms: 99,
                scale: 2,
            },
            ask: crate::events::Decimal {
                atoms: 101,
                scale: 2,
            },
            bid_size: crate::events::Decimal { atoms: 1, scale: 0 },
            ask_size: crate::events::Decimal { atoms: 1, scale: 0 },
            bid_exchange: 1,
            ask_exchange: 1,
            conditions: vec![],
            indicators: vec![],
        };
        modeled.push(quote);
        modeled.sort_unstable_by_key(|event| (event.sip.ns, event.key.sequence));
        let prepared = crate::market_structure::scheduler::playback::Prepared::new(
            scope,
            "historical-model",
            vec![crate::market_structure::scheduler::playback::Frame {
                watermark_ns: source_interval.end,
                evaluated_at_ns: source_interval.end + 1,
                inputs: modeled
                    .into_iter()
                    .map(|observation| {
                        let eligible = observation.key.kind == crate::events::EventKind::Trade;
                        crate::market_structure::scheduler::playback::Input {
                            observation,
                            eligible,
                        }
                    })
                    .collect(),
            }],
            crate::market_structure::scheduler::playback::Limits {
                maximum_frames: 1,
                maximum_events: 5,
                maximum_serialized_bytes: 100_000,
            },
        )
        .unwrap();
        assert_eq!(
            plan.selected_prepared_positions(&prepared, 4).unwrap(),
            vec![(0, 0), (0, 1), (0, 3), (0, 4)]
        );
        assert!(plan.selected_prepared_positions(&prepared, 3).is_err());
        {
            use crate::{
                market_structure::scheduler::playback::sources::{Catalog, Shard},
                run_manifest::{Clock, Consumer, Execution, Manifest, Pinned},
                strategy_dispatch::{Mode, StrategyKind},
            };
            let catalog = Catalog {
                schema_version: 1,
                authority_manifest_hash: "c".repeat(64),
                clock: Clock::Historical,
                shards: vec![Shard {
                    provider: scope.provider,
                    instrument: scope.instrument,
                    session: scope.session,
                    prepared_hash: prepared.hash().into(),
                    clock_model: "historical-model".into(),
                }],
            };
            let manifest = Manifest {
                schema_version: 3,
                run_id: "screen-backtest".into(),
                mode: Mode::Backtest,
                code_release_hash: "a".repeat(64),
                source_manifest_hash: catalog.hash().unwrap(),
                reference_manifest_hash: "b".repeat(64),
                seed_manifest_hash: "d".repeat(64),
                algorithm_manifest_hash: "e".repeat(64),
                dependency_plan_hash: "f".repeat(64),
                hardware_profile_hash: "1".repeat(64),
                clock: Clock::Historical,
                execution: Execution::Simulated {
                    fill_model_hash: "2".repeat(64),
                    cost_model_hash: "3".repeat(64),
                },
                consumers: vec![Consumer {
                    account: "first".into(),
                    instrument: scope.instrument,
                    strategy_instance: "strategy-350".into(),
                    strategy_kind: StrategyKind::Strategy350,
                    execution_interval: ExecutionInterval::Fixed(100_000_000),
                    effective_config_hash: "4".repeat(64),
                }],
            };
            let pinned = Pinned::new(manifest.clone(), &manifest.hash().unwrap()).unwrap();
            let source = catalog.bind_historical(&pinned, &prepared).unwrap();
            let mut refinement = plan.bind_historical(&source, 4).unwrap();
            assert_eq!(refinement.plan_hash(), plan.evidence_hash());
            assert_eq!(refinement.remaining(), 3);
            let first = refinement.next_trade_unchecked().unwrap().unwrap();
            assert_eq!(first.run_id(), "screen-backtest");
            assert_eq!(first.manifest_hash(), pinned.hash());
            assert_eq!(first.key().sequence, 1);
            let mut changed = manifest.clone();
            changed.consumers[0].effective_config_hash = "5".repeat(64);
            let changed_pinned = Pinned::new(changed.clone(), &changed.hash().unwrap()).unwrap();
            let changed_source = catalog.bind_historical(&changed_pinned, &prepared).unwrap();
            assert_ne!(source.manifest_hash(), changed_source.manifest_hash());
            assert_ne!(
                first.identity_hash().unwrap(),
                changed_source.event(0, 0).unwrap().identity_hash().unwrap()
            );
            let selected_observation = &prepared.frames()[0].inputs[0].observation;
            let mut macd = crate::strategy350_macd::historical::test_empty_cursor(
                scope,
                source_interval.start,
                source_interval.end,
                2,
            );
            let preview = macd.preview_proof(&first, selected_observation).unwrap();
            assert_eq!(preview.outcome().event_time_ns, first.source_time_ns());
            assert!(!preview.outcome().bullish);
            assert_eq!(preview.fingerprint().len(), 64);
            let changed_run_proof = changed_source.event(0, 0).unwrap();
            let changed_run_evidence = macd
                .preview_proof(&changed_run_proof, selected_observation)
                .unwrap();
            assert_ne!(preview.fingerprint(), changed_run_evidence.fingerprint());
            let mut changed_observation = selected_observation.clone();
            if let crate::events::Payload::Trade { price, .. } = &mut changed_observation.payload {
                price.atoms += 1;
            }
            assert!(macd.preview_proof(&first, &changed_observation).is_err());
            let boundary = crate::market_structure::scheduler::Boundary {
                id: "selected",
                sequence: 1,
                evaluated_at_ns: first.evaluated_at_ns(),
                kind: crate::market_structure::scheduler::Kind::Trade {
                    observation: selected_observation,
                    eligible: true,
                },
            };
            assert!(matches_pending_trade(&first, &boundary).unwrap());
            let other_observation = &prepared.frames()[0].inputs[1].observation;
            let other_boundary = crate::market_structure::scheduler::Boundary {
                id: "other",
                sequence: 2,
                evaluated_at_ns: first.evaluated_at_ns(),
                kind: crate::market_structure::scheduler::Kind::Trade {
                    observation: other_observation,
                    eligible: true,
                },
            };
            assert!(!matches_pending_trade(&first, &other_boundary).unwrap());
            let wrong_boundary = crate::market_structure::scheduler::Boundary {
                evaluated_at_ns: first.evaluated_at_ns() + 1,
                ..boundary
            };
            assert!(matches_pending_trade(&first, &wrong_boundary).is_err());
            assert_eq!(
                refinement
                    .next_trade_unchecked()
                    .unwrap()
                    .unwrap()
                    .key()
                    .sequence,
                2
            );
            assert_eq!(
                refinement
                    .next_trade_unchecked()
                    .unwrap()
                    .unwrap()
                    .key()
                    .sequence,
                4
            );
            assert!(refinement.next_trade_unchecked().unwrap().is_none());
        }
        assert!(plan.contains_source_time(scope, S).unwrap());
        assert!(plan.contains_source_time(scope, S + 99_999_999).unwrap());
        assert!(!plan.contains_source_time(scope, S + 100_000_000).unwrap());
        assert!(plan.contains_source_time(scope, S + 200_000_000).unwrap());
        assert!(plan.contains_source_time(scope, S + 300_000_000).is_err());
        assert!(plan
            .contains_source_time(
                crate::event_order::Scope {
                    instrument: 11,
                    ..scope
                },
                S,
            )
            .is_err());
        assert!(RefinementPlan::from_batches(scope, source_interval, &selected, 1).is_err());
        assert!(RefinementPlan::from_batches(
            scope,
            Interval {
                start: S,
                end: S + 400_000_000,
            },
            &selected,
            2,
        )
        .is_err());
        let unknown = boolean(
            &b,
            ExecutableKind::Watchlist,
            WATCHLIST,
            vec![true, false, true],
            vec![true, false, true],
        );
        assert!(select(
            &b,
            &config(),
            &close(),
            &signal,
            WatchlistPolicy::Required,
            Some(&unknown)
        )
        .is_err());
        let wrong = boolean(
            &b,
            ExecutableKind::Watchlist,
            "wrong",
            vec![true; 3],
            vec![true; 3],
        );
        assert!(select(
            &b,
            &config(),
            &close(),
            &signal,
            WatchlistPolicy::Required,
            Some(&wrong)
        )
        .is_err());
        assert!(select(
            &b,
            &config(),
            &close(),
            &signal,
            WatchlistPolicy::Required,
            None
        )
        .is_err());
        let without_watch = select(
            &b,
            &config(),
            &close(),
            &signal,
            WatchlistPolicy::NotRequired,
            None,
        )
        .unwrap();
        assert_eq!(without_watch[0].refine, vec![true; 3]);
        let silent = boolean(
            &b,
            ExecutableKind::SignalStream,
            SIGNAL,
            vec![true; 3],
            vec![false; 3],
        );
        let no_candidates = select(
            &b,
            &config(),
            &close(),
            &silent,
            WatchlistPolicy::NotRequired,
            None,
        )
        .unwrap();
        let empty_plan =
            RefinementPlan::from_batches(scope, source_interval, &no_candidates, 2).unwrap();
        assert!(empty_plan.intervals().is_empty());
        assert_ne!(plan.evidence_hash(), empty_plan.evidence_hash());
    }
    #[test]
    fn carried_value_from_slower_signal_cannot_select_unevaluated_bucket() {
        let b = bar();
        let slower = boolean_with_parts_at_cadence(
            &b,
            ExecutableKind::SignalStream,
            SIGNAL,
            vec![false, true, true],
            vec![false, true, true],
            &[3],
            200_000_000,
        );
        assert!(select(
            &b,
            &config(),
            &close(),
            &slower,
            WatchlistPolicy::NotRequired,
            None,
        )
        .is_err());
    }
    #[test]
    fn evidence_is_partition_independent_and_binds_prior_close_clock() {
        let b = bar();
        let one = boolean(
            &b,
            ExecutableKind::SignalStream,
            SIGNAL,
            vec![true; 3],
            vec![true; 3],
        );
        let split = boolean_with_parts(
            &b,
            ExecutableKind::SignalStream,
            SIGNAL,
            vec![true; 3],
            vec![true; 3],
            &[1, 2],
        );
        let select_one = select(
            &b,
            &config(),
            &close(),
            &one,
            WatchlistPolicy::NotRequired,
            None,
        )
        .unwrap();
        let select_split = select(
            &b,
            &config(),
            &close(),
            &split,
            WatchlistPolicy::NotRequired,
            None,
        )
        .unwrap();
        assert_eq!(select_one[0].refine, select_split[0].refine);
        assert_eq!(select_one[0].evidence_hash, select_split[0].evidence_hash);
        assert_eq!(select_one[0].evidence_hash.len(), 64);
        let scope = select_one[0].scope();
        let interval = b.request().interval;
        assert_eq!(
            RefinementPlan::from_batches(scope, interval, &select_one, 3)
                .unwrap()
                .evidence_hash(),
            RefinementPlan::from_batches(scope, interval, &select_split, 3)
                .unwrap()
                .evidence_hash(),
        );
        let mut later_close = close();
        later_close.available_at_ns -= 1;
        let changed = select(
            &b,
            &config(),
            &later_close,
            &one,
            WatchlistPolicy::NotRequired,
            None,
        )
        .unwrap();
        assert_eq!(select_one[0].refine, changed[0].refine);
        assert_ne!(select_one[0].evidence_hash, changed[0].evidence_hash);
    }
}
