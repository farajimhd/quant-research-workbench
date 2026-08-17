use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

pub const WATCHLIST_TIMELINE_PLAN_SCHEMA_VERSION: u16 = 3;
pub const MAX_EVALUATIONS_PER_CHUNK: u64 = 1_800;
pub const MAX_MEMBERSHIP_SLOTS_PER_CHUNK: u64 = 2_000_000;
const QMD_SOURCES: [&str; 6] = [
    "indicator.vwap.value",
    "liquidity-rank",
    "market.change_pct",
    "market.last_price",
    "market.relative_volume",
    "market.volume",
];

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ExternalFeatureContract {
    pub available_at: String,
    pub event_at: String,
    pub field_id: String,
    pub identity_join: String,
    pub owner: String,
    pub query_plan_id: String,
    pub query_plan_version: u16,
    pub schema_version: u16,
    pub source_path: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WatchlistEvaluationWindow {
    pub end: String,
    pub start: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct HistoricalWatchlistPlan {
    pub schema_version: u16,
    pub watchlist_id: String,
    pub start: String,
    pub end: String,
    pub evaluation_windows: Vec<WatchlistEvaluationWindow>,
    pub cadence_ms: u64,
    pub chunk_duration_ms: u64,
    pub max_evaluations_per_chunk: u64,
    pub source_scan_id: String,
    pub inclusion_operator: String,
    pub inclusion_rule_sets: Vec<String>,
    pub exclusion_rule_sets: Vec<String>,
    pub rule_sets: Vec<Value>,
    pub ranking_field: String,
    pub ranking_direction: String,
    pub maximum_size: usize,
    pub focused_seed_multiplier: usize,
    pub membership_expiry: String,
    pub membership_ttl_ms: u64,
    pub manual_inclusions: Vec<String>,
    pub manual_exclusions: Vec<String>,
    pub qmd_sources: Vec<String>,
    pub external_features: Vec<ExternalFeatureContract>,
    pub output_mode: String,
    pub state_carry_required: bool,
    pub plan_hash: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalWatchlistPlanValidation {
    pub schema_version: u16,
    pub plan_hash: String,
    pub watchlist_id: String,
    pub evaluation_count: u64,
    pub chunk_count: u64,
    pub cadence_ms: u64,
    pub external_feature_count: usize,
    pub qmd_source_count: usize,
    pub valid: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WatchlistCandidate {
    pub ticker: String,
    pub values: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WatchlistCandidateFrame {
    pub effective_at: String,
    pub candidates: Vec<WatchlistCandidate>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WatchlistCandidateDeltaFrame {
    pub effective_at: String,
    pub removals: Vec<String>,
    pub upserts: Vec<WatchlistCandidate>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WatchlistTimelineMember {
    pub evidence: BTreeMap<String, Value>,
    pub membership_reason: String,
    pub rank: usize,
    pub score: Option<f64>,
    pub ticker: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WatchlistTimelineState {
    pub candidates: BTreeMap<String, WatchlistCandidate>,
    pub members: BTreeMap<String, WatchlistTimelineMember>,
    pub next_evaluation_index: u64,
    pub plan_hash: String,
    pub schema_version: u16,
}

#[derive(Clone, Debug, Serialize)]
pub struct WatchlistMembershipTransition {
    pub effective_at: String,
    pub event: &'static str,
    pub evidence: BTreeMap<String, Value>,
    pub prior_rank: Option<usize>,
    pub rank: Option<usize>,
    pub reason: String,
    pub score: Option<f64>,
    pub ticker: String,
    pub watchlist_id: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct WatchlistTimelineChunk {
    pub cadence_ms: u64,
    pub end_evaluation_index: u64,
    pub next_state: WatchlistTimelineState,
    pub plan_hash: String,
    pub schema_version: u16,
    pub start_evaluation_index: u64,
    pub transitions: Vec<WatchlistMembershipTransition>,
    pub watchlist_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ExternalFeatureRevisionEvidence {
    pub complete: bool,
    pub field_id: String,
    pub query_plan_id: String,
    pub query_plan_version: u16,
    pub schema_version: u16,
    pub source_revision: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ExternalFeatureValueInterval {
    pub end: Option<String>,
    pub field_id: String,
    pub start: String,
    pub ticker: String,
    pub value: Value,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct HistoricalWatchlistTimelineRequest {
    pub external_feature_intervals: Vec<ExternalFeatureValueInterval>,
    pub external_feature_revisions: Vec<ExternalFeatureRevisionEvidence>,
    pub plan: HistoricalWatchlistPlan,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct HistoricalWatchlistTimelineBatchRequest {
    pub requests: Vec<HistoricalWatchlistTimelineRequest>,
}

pub struct WatchlistTimelineReducer<'a> {
    allowed_sources: BTreeSet<String>,
    candidates: BTreeMap<String, WatchlistCandidate>,
    eligible: BTreeMap<String, WatchlistTimelineMember>,
    expected_count: usize,
    frames_applied: usize,
    members: BTreeMap<String, WatchlistTimelineMember>,
    plan: &'a HistoricalWatchlistPlan,
    ranked: BTreeSet<RankKey>,
    start_index: u64,
    transitions: Vec<WatchlistMembershipTransition>,
}

#[derive(Clone, Debug)]
struct RankKey {
    missing: bool,
    normalized_score: f64,
    ticker: String,
}

impl PartialEq for RankKey {
    fn eq(&self, other: &Self) -> bool {
        self.missing == other.missing
            && self.normalized_score.to_bits() == other.normalized_score.to_bits()
            && self.ticker == other.ticker
    }
}

impl Eq for RankKey {}

impl PartialOrd for RankKey {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for RankKey {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.missing
            .cmp(&other.missing)
            .then_with(|| self.normalized_score.total_cmp(&other.normalized_score))
            .then_with(|| self.ticker.cmp(&other.ticker))
    }
}

pub fn validate_plan(
    plan: &HistoricalWatchlistPlan,
) -> Result<HistoricalWatchlistPlanValidation, String> {
    if plan.schema_version != WATCHLIST_TIMELINE_PLAN_SCHEMA_VERSION {
        return Err(format!(
            "unsupported historical Watchlist plan schema_version={}",
            plan.schema_version
        ));
    }
    if plan.watchlist_id.trim().is_empty() {
        return Err("historical Watchlist plan requires watchlist_id".to_string());
    }
    let start = parse_time(&plan.start, "start")?;
    let end = parse_time(&plan.end, "end")?;
    if end <= start {
        return Err("historical Watchlist plan end must follow start".to_string());
    }
    if plan.cadence_ms == 0 {
        return Err("historical Watchlist cadence_ms must be positive".to_string());
    }
    if plan.max_evaluations_per_chunk == 0
        || plan.max_evaluations_per_chunk > MAX_EVALUATIONS_PER_CHUNK
    {
        return Err(format!(
            "historical Watchlist max_evaluations_per_chunk must be 1..={MAX_EVALUATIONS_PER_CHUNK}"
        ));
    }
    if plan.chunk_duration_ms
        != plan
            .cadence_ms
            .saturating_mul(plan.max_evaluations_per_chunk)
    {
        return Err(
            "historical Watchlist chunk duration does not match cadence budget".to_string(),
        );
    }
    if plan.maximum_size == 0 || plan.maximum_size > 5_000 {
        return Err("historical Watchlist maximum_size must be 1..=5000".to_string());
    }
    if plan.focused_seed_multiplier == 0 || plan.focused_seed_multiplier > 20 {
        return Err("historical Watchlist focused_seed_multiplier must be 1..=20".to_string());
    }
    if plan
        .max_evaluations_per_chunk
        .saturating_mul(plan.maximum_size as u64)
        > MAX_MEMBERSHIP_SLOTS_PER_CHUNK
    {
        return Err(format!(
            "historical Watchlist chunk exceeds membership-slot budget={MAX_MEMBERSHIP_SLOTS_PER_CHUNK}"
        ));
    }
    if plan.output_mode != "initial_membership_then_transition_deltas" || !plan.state_carry_required
    {
        return Err(
            "historical Watchlist plan requires transition output with state carry".to_string(),
        );
    }
    if !matches!(plan.inclusion_operator.as_str(), "all" | "any") {
        return Err("historical Watchlist inclusion_operator must be all or any".to_string());
    }
    if !matches!(plan.ranking_direction.as_str(), "ascending" | "descending") {
        return Err("historical Watchlist ranking_direction is invalid".to_string());
    }
    if plan.source_scan_id != "qmd-core-scan" {
        return Err("historical Watchlist source_scan_id must be qmd-core-scan".to_string());
    }
    if !matches!(
        plan.membership_expiry.as_str(),
        "end_of_trading_day" | "time_to_live" | "never"
    ) {
        return Err("historical Watchlist membership_expiry is invalid".to_string());
    }
    if plan.membership_expiry == "time_to_live" && plan.membership_ttl_ms == 0 {
        return Err("historical Watchlist time_to_live requires membership_ttl_ms".to_string());
    }
    let manual_inclusions = plan
        .manual_inclusions
        .iter()
        .map(|value| value.trim().to_ascii_uppercase())
        .collect::<BTreeSet<_>>();
    let manual_exclusions = plan
        .manual_exclusions
        .iter()
        .map(|value| value.trim().to_ascii_uppercase())
        .collect::<BTreeSet<_>>();
    if manual_inclusions.len() != plan.manual_inclusions.len()
        || manual_exclusions.len() != plan.manual_exclusions.len()
        || manual_inclusions.contains("")
        || manual_exclusions.contains("")
        || !manual_inclusions.is_disjoint(&manual_exclusions)
    {
        return Err(
            "historical Watchlist manual inclusions/exclusions are invalid or overlap".to_string(),
        );
    }
    let allowed = QMD_SOURCES.into_iter().collect::<BTreeSet<_>>();
    let qmd_sources = plan
        .qmd_sources
        .iter()
        .map(|value| value.as_str())
        .collect::<BTreeSet<_>>();
    if qmd_sources.len() != plan.qmd_sources.len() || !qmd_sources.is_subset(&allowed) {
        return Err(
            "historical Watchlist plan has duplicate or unsupported QMD sources".to_string(),
        );
    }
    let mut feature_ids = BTreeSet::new();
    for feature in &plan.external_features {
        if !feature_ids.insert(feature.field_id.as_str())
            || feature.owner.trim().is_empty()
            || feature.query_plan_id.trim().is_empty()
            || feature.query_plan_version == 0
            || feature.available_at.trim().is_empty()
            || feature.event_at.trim().is_empty()
            || feature.identity_join.trim().is_empty()
            || feature.source_path.trim().is_empty()
            || feature.schema_version == 0
        {
            return Err(
                "historical Watchlist external feature contract is incomplete or duplicated"
                    .to_string(),
            );
        }
    }
    validate_rule_graph(plan, &qmd_sources, &feature_ids)?;
    verify_plan_hash(plan)?;
    let evaluation_count = validate_evaluation_windows(plan, start, end)?;
    let chunk_count = evaluation_count.div_ceil(plan.max_evaluations_per_chunk);
    Ok(HistoricalWatchlistPlanValidation {
        schema_version: WATCHLIST_TIMELINE_PLAN_SCHEMA_VERSION,
        plan_hash: plan.plan_hash.clone(),
        watchlist_id: plan.watchlist_id.clone(),
        evaluation_count,
        chunk_count,
        cadence_ms: plan.cadence_ms,
        external_feature_count: plan.external_features.len(),
        qmd_source_count: plan.qmd_sources.len(),
        valid: true,
    })
}

fn validate_evaluation_windows(
    plan: &HistoricalWatchlistPlan,
    plan_start: DateTime<Utc>,
    plan_end: DateTime<Utc>,
) -> Result<u64, String> {
    if plan.evaluation_windows.is_empty() {
        return Err("historical Watchlist plan requires evaluation_windows".to_string());
    }
    let mut prior_end = None;
    let mut evaluation_count = 0_u64;
    for window in &plan.evaluation_windows {
        let start = parse_time(&window.start, "evaluation window start")?;
        let end = parse_time(&window.end, "evaluation window end")?;
        if start < plan_start || end > plan_end || end <= start {
            return Err(
                "historical Watchlist evaluation window is outside plan bounds or reversed"
                    .to_string(),
            );
        }
        if prior_end.is_some_and(|prior| start < prior) {
            return Err(
                "historical Watchlist evaluation windows overlap or are out of order".to_string(),
            );
        }
        let duration_ms = u64::try_from((end - start).num_milliseconds())
            .map_err(|_| "historical Watchlist evaluation window overflowed".to_string())?;
        evaluation_count = evaluation_count.saturating_add(duration_ms.div_ceil(plan.cadence_ms));
        prior_end = Some(end);
    }
    Ok(evaluation_count)
}

pub fn plan_evaluation_clock(
    plan: &HistoricalWatchlistPlan,
    index: u64,
) -> Result<DateTime<Utc>, String> {
    let mut remaining = index;
    for window in &plan.evaluation_windows {
        let start = parse_time(&window.start, "evaluation window start")?;
        let end = parse_time(&window.end, "evaluation window end")?;
        let duration_ms = u64::try_from((end - start).num_milliseconds())
            .map_err(|_| "historical Watchlist evaluation window overflowed".to_string())?;
        let count = duration_ms.div_ceil(plan.cadence_ms);
        if remaining < count {
            return Ok(start
                + chrono::Duration::milliseconds(
                    i64::try_from(remaining.saturating_mul(plan.cadence_ms))
                        .map_err(|_| "historical Watchlist cadence clock overflowed".to_string())?,
                ));
        }
        remaining = remaining.saturating_sub(count);
    }
    Err(format!(
        "historical Watchlist evaluation index={index} exceeds its window schedule"
    ))
}

pub fn materialize_candidate_chunk(
    plan: &HistoricalWatchlistPlan,
    frames: &[WatchlistCandidateFrame],
    prior_state: Option<WatchlistTimelineState>,
) -> Result<WatchlistTimelineChunk, String> {
    let mut reducer = WatchlistTimelineReducer::new(plan, prior_state)?;
    for frame in frames {
        reducer.apply(frame)?;
    }
    reducer.finish()
}

impl<'a> WatchlistTimelineReducer<'a> {
    pub fn new(
        plan: &'a HistoricalWatchlistPlan,
        prior_state: Option<WatchlistTimelineState>,
    ) -> Result<Self, String> {
        let validation = validate_plan(plan)?;
        let start_index = prior_state
            .as_ref()
            .map(|state| {
                if state.schema_version != WATCHLIST_TIMELINE_PLAN_SCHEMA_VERSION
                    || state.plan_hash != plan.plan_hash
                {
                    return Err(
                        "historical Watchlist timeline state does not match the admitted plan"
                            .to_string(),
                    );
                }
                Ok(state.next_evaluation_index)
            })
            .transpose()?
            .unwrap_or(0);
        let remaining = validation.evaluation_count.saturating_sub(start_index);
        let expected_count = remaining.min(plan.max_evaluations_per_chunk) as usize;
        let allowed_sources = plan
            .qmd_sources
            .iter()
            .cloned()
            .chain(
                plan.external_features
                    .iter()
                    .map(|feature| feature.field_id.clone()),
            )
            .collect::<BTreeSet<_>>();
        let (members, candidates) = prior_state
            .map(|state| (state.members, state.candidates))
            .unwrap_or_default();
        let mut reducer = Self {
            allowed_sources,
            candidates,
            eligible: BTreeMap::new(),
            expected_count,
            frames_applied: 0,
            members,
            plan,
            ranked: BTreeSet::new(),
            start_index,
            transitions: Vec::new(),
        };
        reducer.rebuild_rank_index()?;
        Ok(reducer)
    }

    pub fn apply(&mut self, frame: &WatchlistCandidateFrame) -> Result<(), String> {
        let incoming = frame
            .candidates
            .iter()
            .map(|candidate| candidate.ticker.trim().to_ascii_uppercase())
            .collect::<BTreeSet<_>>();
        if incoming.len() != frame.candidates.len() || incoming.contains("") {
            return Err(
                "historical Watchlist candidate tickers must be non-empty and unique".to_string(),
            );
        }
        let removals = self
            .candidates
            .keys()
            .filter(|ticker| !incoming.contains(*ticker))
            .cloned()
            .collect::<Vec<_>>();
        self.apply_delta(&WatchlistCandidateDeltaFrame {
            effective_at: frame.effective_at.clone(),
            removals,
            upserts: frame.candidates.clone(),
        })
    }

    pub fn apply_delta(&mut self, frame: &WatchlistCandidateDeltaFrame) -> Result<(), String> {
        if self.frames_applied >= self.expected_count {
            return Err("historical Watchlist chunk received too many cadence frames".to_string());
        }
        let evaluation_index = self.start_index.saturating_add(self.frames_applied as u64);
        let expected_at = plan_evaluation_clock(self.plan, evaluation_index)?;
        let effective_at = parse_time(&frame.effective_at, "candidate delta effective_at")?;
        if effective_at != expected_at {
            return Err(format!(
                "historical Watchlist cadence frame drift: expected={} actual={}",
                expected_at.to_rfc3339(),
                effective_at.to_rfc3339()
            ));
        }
        let removals = frame
            .removals
            .iter()
            .map(|ticker| ticker.trim().to_ascii_uppercase())
            .collect::<BTreeSet<_>>();
        let upsert_tickers = frame
            .upserts
            .iter()
            .map(|candidate| candidate.ticker.trim().to_ascii_uppercase())
            .collect::<BTreeSet<_>>();
        if removals.len() != frame.removals.len()
            || upsert_tickers.len() != frame.upserts.len()
            || removals.contains("")
            || upsert_tickers.contains("")
            || !removals.is_disjoint(&upsert_tickers)
        {
            return Err(
                "historical Watchlist delta tickers are invalid, duplicated, or overlap"
                    .to_string(),
            );
        }
        for ticker in removals {
            self.candidates.remove(&ticker);
            self.remove_eligible(&ticker);
        }
        for candidate in &frame.upserts {
            self.upsert_candidate(candidate.clone())?;
        }
        self.ensure_manual_missing();
        let current = self.current_members();
        append_transitions(
            self.plan,
            effective_at,
            &self.members,
            &current,
            &mut self.transitions,
        );
        if self.transitions.len() as u64 > MAX_MEMBERSHIP_SLOTS_PER_CHUNK {
            return Err(format!(
                "historical Watchlist transitions exceeded budget={MAX_MEMBERSHIP_SLOTS_PER_CHUNK}"
            ));
        }
        self.members = current;
        self.frames_applied += 1;
        Ok(())
    }

    fn rebuild_rank_index(&mut self) -> Result<(), String> {
        let candidates = self.candidates.values().cloned().collect::<Vec<_>>();
        self.candidates.clear();
        self.eligible.clear();
        self.ranked.clear();
        for candidate in candidates {
            self.upsert_candidate(candidate)?;
        }
        self.ensure_manual_missing();
        Ok(())
    }

    fn upsert_candidate(&mut self, mut candidate: WatchlistCandidate) -> Result<(), String> {
        let ticker = candidate.ticker.trim().to_ascii_uppercase();
        if ticker.is_empty()
            || candidate
                .values
                .keys()
                .any(|source| !self.allowed_sources.contains(source))
        {
            return Err(format!(
                "historical Watchlist candidate {ticker} is invalid or contains an undeclared source"
            ));
        }
        candidate.ticker = ticker.clone();
        self.remove_eligible(&ticker);
        self.candidates.insert(ticker.clone(), candidate.clone());
        if let Some(member) = evaluate_candidate(self.plan, &candidate)? {
            self.ranked.insert(rank_key(self.plan, &member));
            self.eligible.insert(ticker, member);
        }
        Ok(())
    }

    fn remove_eligible(&mut self, ticker: &str) {
        if let Some(member) = self.eligible.remove(ticker) {
            self.ranked.remove(&rank_key(self.plan, &member));
        }
    }

    fn ensure_manual_missing(&mut self) {
        let exclusions = self
            .plan
            .manual_exclusions
            .iter()
            .map(|value| value.to_ascii_uppercase())
            .collect::<BTreeSet<_>>();
        for ticker in self
            .plan
            .manual_inclusions
            .iter()
            .map(|value| value.to_ascii_uppercase())
        {
            if self.candidates.contains_key(&ticker) || exclusions.contains(&ticker) {
                continue;
            }
            self.remove_eligible(&ticker);
            let member = WatchlistTimelineMember {
                evidence: BTreeMap::new(),
                membership_reason: "manual inclusion; scanner evidence unavailable".to_string(),
                rank: 0,
                score: None,
                ticker: ticker.clone(),
            };
            self.ranked.insert(rank_key(self.plan, &member));
            self.eligible.insert(ticker, member);
        }
    }

    fn current_members(&self) -> BTreeMap<String, WatchlistTimelineMember> {
        self.ranked
            .iter()
            .take(self.plan.maximum_size)
            .enumerate()
            .filter_map(|(index, key)| {
                self.eligible.get(&key.ticker).map(|member| {
                    let mut member = member.clone();
                    member.rank = index + 1;
                    (member.ticker.clone(), member)
                })
            })
            .collect()
    }

    pub fn member_tickers(&self) -> impl Iterator<Item = &String> {
        self.members.keys()
    }

    pub fn finish(self) -> Result<WatchlistTimelineChunk, String> {
        if self.frames_applied != self.expected_count {
            return Err(format!(
                "historical Watchlist chunk requires {} cadence frames, received {}",
                self.expected_count, self.frames_applied
            ));
        }
        let end_index = self.start_index.saturating_add(self.frames_applied as u64);
        let next_state = WatchlistTimelineState {
            candidates: self.candidates,
            members: self.members,
            next_evaluation_index: end_index,
            plan_hash: self.plan.plan_hash.clone(),
            schema_version: WATCHLIST_TIMELINE_PLAN_SCHEMA_VERSION,
        };
        Ok(WatchlistTimelineChunk {
            cadence_ms: self.plan.cadence_ms,
            end_evaluation_index: end_index,
            next_state,
            plan_hash: self.plan.plan_hash.clone(),
            schema_version: WATCHLIST_TIMELINE_PLAN_SCHEMA_VERSION,
            start_evaluation_index: self.start_index,
            transitions: self.transitions,
            watchlist_id: self.plan.watchlist_id.clone(),
        })
    }
}

fn evaluate_candidate(
    plan: &HistoricalWatchlistPlan,
    candidate: &WatchlistCandidate,
) -> Result<Option<WatchlistTimelineMember>, String> {
    let rules = plan
        .rule_sets
        .iter()
        .map(|raw| {
            let object = raw
                .as_object()
                .ok_or_else(|| "historical Watchlist rule_set must be an object".to_string())?;
            Ok((required_string(object, "rule_set_id")?, object))
        })
        .collect::<Result<BTreeMap<_, _>, String>>()?;
    let manual_inclusions = plan
        .manual_inclusions
        .iter()
        .map(|value| value.to_ascii_uppercase())
        .collect::<BTreeSet<_>>();
    let manual_exclusions = plan
        .manual_exclusions
        .iter()
        .map(|value| value.to_ascii_uppercase())
        .collect::<BTreeSet<_>>();
    let ticker = candidate.ticker.to_ascii_uppercase();
    if manual_exclusions.contains(&ticker) {
        return Ok(None);
    }
    let include_results = plan
        .inclusion_rule_sets
        .iter()
        .map(|rule_id| rule_matches(rules.get(rule_id.as_str()).copied(), &candidate.values))
        .collect::<Vec<_>>();
    let included = include_results.is_empty()
        || if plan.inclusion_operator == "any" {
            include_results.iter().any(|value| *value)
        } else {
            include_results.iter().all(|value| *value)
        };
    let excluded = plan
        .exclusion_rule_sets
        .iter()
        .any(|rule_id| rule_matches(rules.get(rule_id.as_str()).copied(), &candidate.values));
    if !((included && !excluded) || manual_inclusions.contains(&ticker)) {
        return Ok(None);
    }
    Ok(Some(WatchlistTimelineMember {
        evidence: candidate.values.clone(),
        membership_reason: if manual_inclusions.contains(&ticker) {
            "manual inclusion".to_string()
        } else {
            "rules passed".to_string()
        },
        rank: 0,
        score: numeric_value(candidate.values.get(&plan.ranking_field)),
        ticker,
    }))
}

fn rank_key(plan: &HistoricalWatchlistPlan, member: &WatchlistTimelineMember) -> RankKey {
    RankKey {
        missing: member.score.is_none(),
        normalized_score: member.score.map_or(0.0, |score| {
            if plan.ranking_direction == "descending" {
                -score
            } else {
                score
            }
        }),
        ticker: member.ticker.clone(),
    }
}

fn rule_matches(
    rule: Option<&serde_json::Map<String, Value>>,
    values: &BTreeMap<String, Value>,
) -> bool {
    let Some(rule) = rule else {
        return false;
    };
    if rule.get("enabled").and_then(Value::as_bool) == Some(false) {
        return false;
    }
    let results = rule
        .get("conditions")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter(|condition| condition.get("enabled").and_then(Value::as_bool) != Some(false))
        .map(|condition| condition_matches(condition, values))
        .collect::<Vec<_>>();
    if results.is_empty() {
        return false;
    }
    match optional_string(rule, "operator").unwrap_or("all") {
        "any" => results.iter().any(|value| *value),
        "score" => {
            let score =
                results.iter().filter(|value| **value).count() as f64 / results.len().max(1) as f64;
            score
                >= rule
                    .get("required_score")
                    .and_then(Value::as_f64)
                    .unwrap_or(1.0)
        }
        _ => results.iter().all(|value| *value),
    }
}

fn condition_matches(
    condition: &serde_json::Map<String, Value>,
    values: &BTreeMap<String, Value>,
) -> bool {
    let Some(left_source) = optional_string(condition, "left_source_id") else {
        return false;
    };
    let left = values.get(left_source);
    let comparator = optional_string(condition, "comparator").unwrap_or("");
    if comparator == "is_true" {
        return left.and_then(Value::as_bool) == Some(true);
    }
    let right_source = optional_string(condition, "right_source_id").unwrap_or("");
    let right = if right_source.is_empty() {
        condition.get("value")
    } else {
        values.get(right_source)
    };
    if comparator == "equals" {
        return left.is_some() && left == right;
    }
    let (Some(left), Some(right)) = (numeric_value(left), numeric_value(right)) else {
        return false;
    };
    match comparator {
        "above_by_bps" => {
            let bps = condition
                .get("value")
                .and_then(Value::as_f64)
                .unwrap_or(0.0);
            right > 0.0 && left >= right * (1.0 + bps / 10_000.0)
        }
        "greater_or_equal" => left >= right,
        "greater_than" => left > right,
        "less_or_equal" => left <= right,
        "less_than" => left < right,
        _ => false,
    }
}

fn numeric_value(value: Option<&Value>) -> Option<f64> {
    value
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
}

fn append_transitions(
    plan: &HistoricalWatchlistPlan,
    effective_at: DateTime<Utc>,
    previous: &BTreeMap<String, WatchlistTimelineMember>,
    current: &BTreeMap<String, WatchlistTimelineMember>,
    transitions: &mut Vec<WatchlistMembershipTransition>,
) {
    let clock = effective_at.to_rfc3339();
    for (ticker, prior) in previous {
        if !current.contains_key(ticker) {
            transitions.push(WatchlistMembershipTransition {
                effective_at: clock.clone(),
                event: "removed",
                evidence: prior.evidence.clone(),
                prior_rank: Some(prior.rank),
                rank: None,
                reason: "rules no longer passed".to_string(),
                score: prior.score,
                ticker: ticker.clone(),
                watchlist_id: plan.watchlist_id.clone(),
            });
        }
    }
    for (ticker, member) in current {
        match previous.get(ticker) {
            None => transitions.push(WatchlistMembershipTransition {
                effective_at: clock.clone(),
                event: "added",
                evidence: member.evidence.clone(),
                prior_rank: None,
                rank: Some(member.rank),
                reason: member.membership_reason.clone(),
                score: member.score,
                ticker: ticker.clone(),
                watchlist_id: plan.watchlist_id.clone(),
            }),
            Some(prior) if prior.rank != member.rank => {
                transitions.push(WatchlistMembershipTransition {
                    effective_at: clock.clone(),
                    event: "rank_changed",
                    evidence: member.evidence.clone(),
                    prior_rank: Some(prior.rank),
                    rank: Some(member.rank),
                    reason: "cross-sectional rank changed".to_string(),
                    score: member.score,
                    ticker: ticker.clone(),
                    watchlist_id: plan.watchlist_id.clone(),
                });
            }
            _ => {}
        }
    }
}

fn validate_rule_graph(
    plan: &HistoricalWatchlistPlan,
    qmd_sources: &BTreeSet<&str>,
    external_sources: &BTreeSet<&str>,
) -> Result<(), String> {
    let available_sources = qmd_sources
        .iter()
        .copied()
        .chain(external_sources.iter().copied())
        .collect::<BTreeSet<_>>();
    if !available_sources.contains(plan.ranking_field.as_str()) {
        return Err(
            "historical Watchlist ranking_field is absent from the declared source union"
                .to_string(),
        );
    }
    let mut rules = BTreeMap::new();
    let mut referenced_sources = BTreeSet::from([plan.ranking_field.as_str()]);
    for raw_rule in &plan.rule_sets {
        let rule = raw_rule
            .as_object()
            .ok_or_else(|| "historical Watchlist rule_set must be an object".to_string())?;
        let rule_id = required_string(rule, "rule_set_id")?;
        if rules.insert(rule_id, rule).is_some() {
            return Err("historical Watchlist rule_set_id is duplicated".to_string());
        }
        let operator = optional_string(rule, "operator").unwrap_or("all");
        if !matches!(operator, "all" | "any" | "score") {
            return Err(format!(
                "historical Watchlist rule {rule_id} has unsupported operator={operator}"
            ));
        }
        if operator == "score" {
            let required_score = rule
                .get("required_score")
                .and_then(Value::as_f64)
                .unwrap_or(1.0);
            if !required_score.is_finite() || !(0.0..=1.0).contains(&required_score) {
                return Err(format!(
                    "historical Watchlist rule {rule_id} has invalid required_score"
                ));
            }
        }
        let conditions = rule
            .get("conditions")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("historical Watchlist rule {rule_id} requires conditions"))?;
        let mut condition_ids = BTreeSet::new();
        for condition in conditions {
            let condition = condition.as_object().ok_or_else(|| {
                format!("historical Watchlist rule {rule_id} condition must be an object")
            })?;
            if condition.get("enabled").and_then(Value::as_bool) == Some(false) {
                continue;
            }
            let condition_id = required_string(condition, "condition_id")?;
            if !condition_ids.insert(condition_id) {
                return Err(format!(
                    "historical Watchlist rule {rule_id} duplicates condition_id={condition_id}"
                ));
            }
            let comparator = required_string(condition, "comparator")?;
            if !matches!(
                comparator,
                "above_by_bps"
                    | "equals"
                    | "greater_or_equal"
                    | "greater_than"
                    | "is_true"
                    | "less_or_equal"
                    | "less_than"
            ) {
                return Err(format!(
                    "historical Watchlist condition {condition_id} has unsupported comparator"
                ));
            }
            let left = required_string(condition, "left_source_id")?;
            if optional_string(condition, "left_value_selection").unwrap_or("latest") != "latest" {
                return Err(format!(
                    "historical Watchlist condition {condition_id} supports only latest left value selection"
                ));
            }
            referenced_sources.insert(left);
            let right = optional_string(condition, "right_source_id").unwrap_or("");
            if !right.is_empty() {
                if optional_string(condition, "right_value_selection").unwrap_or("latest")
                    != "latest"
                {
                    return Err(format!(
                        "historical Watchlist condition {condition_id} supports only latest right value selection"
                    ));
                }
                referenced_sources.insert(right);
            }
            if comparator == "above_by_bps" && right.is_empty() {
                return Err(format!(
                    "historical Watchlist condition {condition_id} requires right_source_id"
                ));
            }
            if comparator != "is_true"
                && right.is_empty()
                && condition.get("value").is_none_or(Value::is_null)
            {
                return Err(format!(
                    "historical Watchlist condition {condition_id} requires value or right_source_id"
                ));
            }
        }
    }
    let referenced_rules = plan
        .inclusion_rule_sets
        .iter()
        .chain(plan.exclusion_rule_sets.iter())
        .map(String::as_str)
        .collect::<BTreeSet<_>>();
    if referenced_rules.len() != plan.inclusion_rule_sets.len() + plan.exclusion_rule_sets.len()
        || referenced_rules != rules.keys().copied().collect::<BTreeSet<_>>()
    {
        return Err(
            "historical Watchlist rule_sets must exactly match unique inclusion/exclusion references"
                .to_string(),
        );
    }
    if referenced_sources != available_sources {
        return Err(
            "historical Watchlist declared sources must exactly match rule and ranking dependencies"
                .to_string(),
        );
    }
    Ok(())
}

fn required_string<'a>(
    object: &'a serde_json::Map<String, Value>,
    field: &str,
) -> Result<&'a str, String> {
    optional_string(object, field)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| format!("historical Watchlist contract requires {field}"))
}

fn optional_string<'a>(object: &'a serde_json::Map<String, Value>, field: &str) -> Option<&'a str> {
    object.get(field).and_then(Value::as_str)
}

fn verify_plan_hash(plan: &HistoricalWatchlistPlan) -> Result<(), String> {
    let mut value = serde_json::to_value(plan)
        .map_err(|error| format!("historical Watchlist plan serialization failed: {error}"))?;
    value
        .as_object_mut()
        .ok_or_else(|| "historical Watchlist plan was not an object".to_string())?
        .remove("plan_hash");
    let encoded = serde_json::to_vec(&value)
        .map_err(|error| format!("historical Watchlist plan encoding failed: {error}"))?;
    let actual = format!("sha256:{:x}", Sha256::digest(encoded));
    if actual != plan.plan_hash {
        return Err(format!(
            "historical Watchlist plan hash mismatch: expected={} actual={actual}",
            plan.plan_hash
        ));
    }
    Ok(())
}

fn parse_time(value: &str, field: &str) -> Result<DateTime<Utc>, String> {
    DateTime::parse_from_rfc3339(value)
        .map(|parsed| parsed.with_timezone(&Utc))
        .map_err(|error| format!("invalid historical Watchlist {field}: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{
        materialize_candidate_chunk, plan_evaluation_clock, validate_plan, ExternalFeatureContract,
        HistoricalWatchlistPlan, WatchlistCandidate, WatchlistCandidateFrame,
        WatchlistEvaluationWindow,
    };
    use serde_json::json;
    use sha2::{Digest, Sha256};
    use std::collections::BTreeMap;

    fn rehash(plan: &mut HistoricalWatchlistPlan) {
        let mut value = serde_json::to_value(&*plan).unwrap();
        value.as_object_mut().unwrap().remove("plan_hash");
        plan.plan_hash = format!(
            "sha256:{:x}",
            Sha256::digest(serde_json::to_vec(&value).unwrap())
        );
    }

    fn plan() -> HistoricalWatchlistPlan {
        let mut plan = HistoricalWatchlistPlan {
            schema_version: 3,
            watchlist_id: "core-candidates".to_string(),
            start: "2026-08-07T13:30:00+00:00".to_string(),
            end: "2026-08-07T20:00:00+00:00".to_string(),
            evaluation_windows: vec![WatchlistEvaluationWindow {
                start: "2026-08-07T09:30:00-04:00".to_string(),
                end: "2026-08-07T16:00:00-04:00".to_string(),
            }],
            cadence_ms: 1_000,
            chunk_duration_ms: 1_800_000,
            max_evaluations_per_chunk: 1_800,
            source_scan_id: "qmd-core-scan".to_string(),
            inclusion_operator: "all".to_string(),
            inclusion_rule_sets: vec!["watchlist-float-small".to_string()],
            exclusion_rule_sets: Vec::new(),
            rule_sets: vec![json!({
                "conditions": [
                    {
                        "comparator": "greater_or_equal",
                        "condition_id": "float.small-minimum",
                        "enabled": true,
                        "left_source_id": "reference.float_shares",
                        "left_value_selection": "latest",
                        "right_source_id": "",
                        "value": 2_000_000
                    },
                    {
                        "comparator": "less_than",
                        "condition_id": "float.small-maximum",
                        "enabled": true,
                        "left_source_id": "reference.float_shares",
                        "left_value_selection": "latest",
                        "right_source_id": "",
                        "value": 5_000_000
                    }
                ],
                "description": "Public float from 2 million up to 5 million shares.",
                "enabled": true,
                "name": "Small Float",
                "operator": "all",
                "required_score": 1.0,
                "rule_set_id": "watchlist-float-small",
                "scope": "watchlist"
            })],
            ranking_field: "liquidity-rank".to_string(),
            ranking_direction: "descending".to_string(),
            maximum_size: 250,
            focused_seed_multiplier: 5,
            membership_expiry: "end_of_trading_day".to_string(),
            membership_ttl_ms: 300_000,
            manual_inclusions: Vec::new(),
            manual_exclusions: Vec::new(),
            qmd_sources: vec!["liquidity-rank".to_string()],
            external_features: vec![ExternalFeatureContract {
                available_at: "source publication timestamp".to_string(),
                event_at: "source effective timestamp".to_string(),
                field_id: "reference.float_shares".to_string(),
                identity_join: "point-in-time symbol/security/issuer identity".to_string(),
                owner: "reference_gateway".to_string(),
                query_plan_id: "reference.scanner_asof.v1".to_string(),
                query_plan_version: 2,
                schema_version: 1,
                source_path: "q_live.market_security_float_v1".to_string(),
            }],
            output_mode: "initial_membership_then_transition_deltas".to_string(),
            state_carry_required: true,
            plan_hash: String::new(),
        };
        rehash(&mut plan);
        assert_eq!(
            plan.plan_hash,
            "sha256:8b0871442876967333914bf7a2c736bbce59e27482b0a9740aa66dc82bb72012"
        );
        plan
    }

    #[test]
    fn accepts_content_hashed_bounded_plan() {
        let validation = validate_plan(&plan()).unwrap();
        assert!(validation.valid);
        assert_eq!(validation.evaluation_count, 23_400);
        assert_eq!(validation.chunk_count, 13);
        assert_eq!(validation.external_feature_count, 1);
    }

    #[test]
    fn rejects_hash_or_resource_broadening() {
        let mut invalid = plan();
        invalid.maximum_size = 5_001;
        assert!(validate_plan(&invalid)
            .unwrap_err()
            .contains("maximum_size"));
        let mut invalid = plan();
        invalid.plan_hash = "sha256:wrong".to_string();
        assert!(validate_plan(&invalid)
            .unwrap_err()
            .contains("hash mismatch"));
    }

    #[test]
    fn rejects_hashed_but_semantically_inconsistent_rule_graph() {
        let mut invalid = plan();
        invalid.qmd_sources.push("market.volume".to_string());
        rehash(&mut invalid);
        assert!(validate_plan(&invalid)
            .unwrap_err()
            .contains("declared sources"));

        let mut invalid = plan();
        invalid.inclusion_rule_sets = vec!["missing-rule".to_string()];
        rehash(&mut invalid);
        assert!(validate_plan(&invalid)
            .unwrap_err()
            .contains("exactly match"));

        let mut invalid = plan();
        invalid.rule_sets[0]["conditions"][0]["left_value_selection"] = json!("oldest");
        rehash(&mut invalid);
        assert!(validate_plan(&invalid)
            .unwrap_err()
            .contains("supports only latest left value selection"));
    }

    fn candidate(ticker: &str, liquidity: f64, public_float: f64) -> WatchlistCandidate {
        WatchlistCandidate {
            ticker: ticker.to_string(),
            values: BTreeMap::from([
                ("liquidity-rank".to_string(), json!(liquidity)),
                ("reference.float_shares".to_string(), json!(public_float)),
            ]),
        }
    }

    #[test]
    fn materializes_bounded_transition_only_chunks_with_state_carry() {
        let mut plan = plan();
        plan.end = "2026-08-07T13:30:03+00:00".to_string();
        plan.evaluation_windows[0].end = plan.end.clone();
        plan.max_evaluations_per_chunk = 2;
        plan.chunk_duration_ms = 2_000;
        rehash(&mut plan);
        let first = materialize_candidate_chunk(
            &plan,
            &[
                WatchlistCandidateFrame {
                    effective_at: "2026-08-07T13:30:00+00:00".to_string(),
                    candidates: vec![
                        candidate("AAPL", 10.0, 3_000_000.0),
                        candidate("MSFT", 20.0, 6_000_000.0),
                    ],
                },
                WatchlistCandidateFrame {
                    effective_at: "2026-08-07T13:30:01+00:00".to_string(),
                    candidates: vec![
                        candidate("AAPL", 10.0, 3_000_000.0),
                        candidate("MSFT", 20.0, 4_000_000.0),
                    ],
                },
            ],
            None,
        )
        .unwrap();
        assert_eq!(first.start_evaluation_index, 0);
        assert_eq!(first.end_evaluation_index, 2);
        assert_eq!(
            first
                .transitions
                .iter()
                .map(|event| (event.event, event.ticker.as_str(), event.rank))
                .collect::<Vec<_>>(),
            vec![
                ("added", "AAPL", Some(1)),
                ("rank_changed", "AAPL", Some(2)),
                ("added", "MSFT", Some(1)),
            ]
        );

        let second = materialize_candidate_chunk(
            &plan,
            &[WatchlistCandidateFrame {
                effective_at: "2026-08-07T13:30:02+00:00".to_string(),
                candidates: vec![
                    candidate("AAPL", 10.0, 6_000_000.0),
                    candidate("MSFT", 20.0, 4_000_000.0),
                ],
            }],
            Some(first.next_state),
        )
        .unwrap();
        assert_eq!(second.start_evaluation_index, 2);
        assert_eq!(second.end_evaluation_index, 3);
        assert_eq!(second.transitions.len(), 1);
        assert_eq!(second.transitions[0].event, "removed");
        assert_eq!(second.transitions[0].ticker, "AAPL");
        assert_eq!(second.next_state.members.len(), 1);
    }

    #[test]
    fn evaluation_clock_skips_closed_overnight_and_weekend_windows() {
        let mut plan = plan();
        plan.end = "2026-08-10T13:30:02+00:00".to_string();
        plan.evaluation_windows = vec![
            WatchlistEvaluationWindow {
                start: "2026-08-07T09:30:00-04:00".to_string(),
                end: "2026-08-07T09:30:02-04:00".to_string(),
            },
            WatchlistEvaluationWindow {
                start: "2026-08-10T09:30:00-04:00".to_string(),
                end: "2026-08-10T09:30:02-04:00".to_string(),
            },
        ];
        plan.max_evaluations_per_chunk = 4;
        plan.chunk_duration_ms = 4_000;
        rehash(&mut plan);

        let validation = validate_plan(&plan).unwrap();
        assert_eq!(validation.evaluation_count, 4);
        assert_eq!(
            plan_evaluation_clock(&plan, 2).unwrap().to_rfc3339(),
            "2026-08-10T13:30:00+00:00"
        );
    }
}
