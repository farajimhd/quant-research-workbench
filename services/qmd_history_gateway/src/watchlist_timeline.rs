use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;

pub const WATCHLIST_TIMELINE_PLAN_SCHEMA_VERSION: u16 = 1;
pub const MAX_EVALUATIONS_PER_CHUNK: u64 = 1_800;
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
    pub schema_version: u16,
    pub source_path: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct HistoricalWatchlistPlan {
    pub schema_version: u16,
    pub watchlist_id: String,
    pub start: String,
    pub end: String,
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
            || feature.available_at.trim().is_empty()
            || feature.identity_join.trim().is_empty()
            || feature.schema_version == 0
        {
            return Err(
                "historical Watchlist external feature contract is incomplete or duplicated"
                    .to_string(),
            );
        }
    }
    verify_plan_hash(plan)?;
    let duration_ms = u64::try_from((end - start).num_milliseconds())
        .map_err(|_| "historical Watchlist duration overflowed".to_string())?;
    let evaluation_count = duration_ms.div_ceil(plan.cadence_ms);
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
    use super::{validate_plan, ExternalFeatureContract, HistoricalWatchlistPlan};
    use serde_json::json;
    use sha2::{Digest, Sha256};

    fn plan() -> HistoricalWatchlistPlan {
        let mut plan = HistoricalWatchlistPlan {
            schema_version: 1,
            watchlist_id: "core-candidates".to_string(),
            start: "2026-08-07T13:30:00+00:00".to_string(),
            end: "2026-08-07T20:00:00+00:00".to_string(),
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
                        "left_timeframe": "1d",
                        "right_source_id": "",
                        "right_timeframe": "",
                        "value": 2_000_000
                    },
                    {
                        "comparator": "less_than",
                        "condition_id": "float.small-maximum",
                        "enabled": true,
                        "left_source_id": "reference.float_shares",
                        "left_timeframe": "1d",
                        "right_source_id": "",
                        "right_timeframe": "",
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
                schema_version: 1,
                source_path: "q_live.market_security_float_v1".to_string(),
            }],
            output_mode: "initial_membership_then_transition_deltas".to_string(),
            state_carry_required: true,
            plan_hash: String::new(),
        };
        let mut value = serde_json::to_value(&plan).unwrap();
        value.as_object_mut().unwrap().remove("plan_hash");
        plan.plan_hash = format!(
            "sha256:{:x}",
            Sha256::digest(serde_json::to_vec(&value).unwrap())
        );
        assert_eq!(
            plan.plan_hash,
            "sha256:95047e760497de35bf7bcb95d8d0703adf7093a8ddaeb7772e9f443245eb5ce0"
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
}
