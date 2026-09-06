//! Synthetic input oracle for SQL equivalence tests; no database or service.
use qmd_core::structure_prominence::ReactionProminence;
use serde_json::{json, Value};
use std::io::{self, BufRead};

fn main() -> Result<(), String> {
    for line in io::stdin().lock().lines() {
        let rows: Vec<Vec<Value>>=serde_json::from_str(&line.map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
        let mut score=ReactionProminence::default();
        let mut prefixes=Vec::new();
        for row in rows {
            if row.len()!=8 { return Err("expected eight fixture fields".into()); }
            let n=|i:usize| row[i].as_f64().ok_or("fixture number required");
            score.apply_split(n(7)?);
            score.observe(n(0)?,n(1)?,n(2)?,n(3)? as i8,
                (n(4)?>0.).then_some(n(4)?),n(5)?!=0.,n(6)?!=0.);
            prefixes.push(json!([score.completed,score.current_best,score.frozen_range,
                score.phase,score.side,score.completed_encounters]));
        }
        println!("{}",json!(prefixes));
    }
    Ok(())
}
