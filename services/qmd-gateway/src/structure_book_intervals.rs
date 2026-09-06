//! End-of-session output intervals and causal split versioning.
//! This narrow output is not sufficient to resume the structural engine.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct ClosingIntervals {
    pub open: BTreeMap<u64, Value>,
    pub applied_splits: BTreeMap<String, i64>,
    pub last_close_ms: Option<i64>,
    #[serde(default)]
    pub split_audit: Vec<SplitAudit>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SplitAudit {
    pub identity: String,
    pub effective_ms: i64,
    pub price_factor: f64,
    pub affected_rows: usize,
    pub before_sha256: String,
    pub after_sha256: String,
}

impl ClosingIntervals {
    fn row(mut level: Value, boundary: i64) -> Value {
        level["valid_from_ms"]=boundary.into();
        level["valid_to_ms"]=Value::Null;
        level
    }

    /// Only completed sessions call this method. Intraday transients do not
    /// acquire permanent rows. An empty close explicitly ends all prior rows.
    pub fn close(&mut self, levels: Vec<Value>, boundary: i64) -> Result<Vec<Value>, String> {
        if self.last_close_ms.is_some_and(|t| t>=boundary) {
            return Err("closing boundaries must advance strictly".into());
        }
        let mut next=BTreeMap::new();
        let mut closed=Vec::new();
        for level in levels {
            let id=level["episode_id"].as_u64().ok_or("missing compact episode identity")?;
            let row=Self::row(level,boundary);
            if next.insert(id,row).is_some() { return Err("duplicate compact episode identity".into()); }
        }
        for (id,old) in &self.open {
            if let Some(new)=next.get_mut(id) {
                let mut old_state=old.clone();
                old_state["valid_from_ms"]=boundary.into();
                if old_state==*new { *new=old.clone(); continue; }
            }
            let mut ending=old.clone();
            ending["valid_to_ms"]=boundary.into();
            if ending["valid_from_ms"].as_i64().unwrap()<boundary { closed.push(ending); }
        }
        self.open=next;
        self.last_close_ms=Some(boundary);
        Ok(closed)
    }

    /// Duplicate only prior closing survivors at a split's effective time.
    /// The engine separately adjusts its complete working/continuation state.
    pub fn split(&mut self, identity: &str, effective_ms: i64, factor: f64)
        -> Result<Vec<Value>, String> {
        if !factor.is_finite() || factor<=0. { return Err("invalid split factor".into()); }
        if self.applied_splits.contains_key(identity) {
            if !self.split_audit.iter().any(|a| a.identity==identity
                && a.effective_ms==effective_ms && a.price_factor==factor) {
                return Err("split identity reused with different terms or missing audit".into());
            }
            return Ok(Vec::new());
        }
        if self.last_close_ms.is_some_and(|t| effective_ms<=t) {
            return Err("late split requires rebuilding from its preceding close".into());
        }
        let before=crate::structure_certification::canonical_json_sha256(
            &serde_json::to_value(&self.open).map_err(|e|e.to_string())?)?;
        // Validate/transform a copy, so failure cannot leave a partially scaled book.
        let mut adjusted=self.open.clone();
        let mut closed=Vec::new();
        for row in adjusted.values_mut() {
            if row["valid_from_ms"].as_i64().unwrap()>effective_ms {
                return Err("split predates current price basis".into());
            }
            if row["valid_from_ms"].as_i64().unwrap()<effective_ms {
                let mut old=row.clone();
                old["valid_to_ms"]=effective_ms.into();
                closed.push(old);
            }
            row["valid_from_ms"]=effective_ms.into();
            for field in ["price","lower","upper"] {
                let scaled=row[field].as_f64().ok_or("invalid geometry")?*factor;
                if !scaled.is_finite() { return Err("split geometry overflow".into()); }
                row[field]=scaled.into();
            }
        }
        let after=crate::structure_certification::canonical_json_sha256(
            &serde_json::to_value(&adjusted).map_err(|e|e.to_string())?)?;
        self.split_audit.push(SplitAudit { identity:identity.into(), effective_ms,
            price_factor:factor, affected_rows:adjusted.len(), before_sha256:before,
            after_sha256:after });
        self.open=adjusted;
        self.applied_splits.insert(identity.into(),effective_ms);
        Ok(closed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn level(id:u64,p:f64)->Value { json!({"episode_id":id,"price":p,"lower":p-0.1,"upper":p+0.1,"prominence":2.}) }

    #[test]
    fn split_closes_original_and_duplicates_survivors_exactly_once() {
        let mut book=ClosingIntervals::default();
        book.close(vec![level(1,2.),level(2,3.)],100).unwrap();
        let old=book.split("split-1",200,75.).unwrap();
        assert_eq!(old.len(),2);
        assert_eq!(old[0]["price"],2.);
        assert_eq!(old[0]["valid_to_ms"],200);
        assert_eq!(book.open[&1]["price"],150.);
        assert_eq!(book.open[&2]["price"],225.);
        assert_eq!(book.open[&1]["valid_from_ms"],200);
        assert_eq!(book.open[&1]["prominence"],2.);
        assert_eq!(book.split_audit.len(),1);
        assert_ne!(book.split_audit[0].before_sha256,book.split_audit[0].after_sha256);
        let mut restored:ClosingIntervals=serde_json::from_str(&serde_json::to_string(&book).unwrap()).unwrap();
        assert!(restored.split("split-1",200,75.).unwrap().is_empty());
        assert_eq!(restored.open[&1]["price"],150.);
        assert_eq!(restored.split_audit.len(),1);
        assert!(restored.split("split-1",200,2.).is_err());
        let closed=restored.close(Vec::new(),300).unwrap();
        assert_eq!(closed.len(),2);
        assert!(restored.open.is_empty());
    }

    #[test]
    fn unchanged_closes_coalesce_and_score_changes_version() {
        let mut book=ClosingIntervals::default();
        assert!(book.close(vec![level(1,2.)],100).unwrap().is_empty());
        assert!(book.close(vec![level(1,2.)],200).unwrap().is_empty());
        let mut changed=level(1,2.); changed["prominence"]=3.0.into();
        let closed=book.close(vec![changed],300).unwrap();
        assert_eq!(closed[0]["valid_from_ms"],100);
        assert_eq!(closed[0]["valid_to_ms"],300);
        assert_eq!(book.open[&1]["valid_from_ms"],300);
        assert!(book.close(Vec::new(),300).is_err());
        assert!(book.split("late",250,2.).is_err());
    }
}
