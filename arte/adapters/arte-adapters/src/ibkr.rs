use arte_core::orders::{Bracket, Side};
use arte_core::{Error, Result};
use serde_json::{json, Value};
use std::collections::BTreeSet;
pub mod submission;

fn price(atoms: i64, scale: u8) -> Result<Value> {
    if atoms <= 0 || scale > 9 {
        return Err(Error::Invalid("invalid broker price".into()));
    }
    let divisor = 10_i64.pow(u32::from(scale));
    let text = if scale == 0 {
        atoms.to_string()
    } else {
        format!(
            "{}.{:0width$}",
            atoms / divisor,
            atoms % divisor,
            width = usize::from(scale)
        )
    };
    serde_json::from_str(&text).map_err(|_| Error::Invalid("broker decimal serialization".into()))
}

/// Transport-independent request graph; constructing it never submits an order.
pub fn bracket_payload(order: &Bracket, conid: u64) -> Result<Value> {
    if conid == 0
        || order.price_scale > 9
        || order.quantity == 0
        || order.account.is_empty()
        || order.command_id.is_empty()
    {
        return Err(Error::Invalid("invalid broker mapping".into()));
    }
    let (stop, target) = order
        .stop
        .zip(order.target)
        .ok_or_else(|| Error::Invalid("broker bracket incomplete".into()))?;
    if order.tick <= 0
        || [stop, target, order.entry]
            .iter()
            .any(|p| *p <= 0 || p % order.tick != 0)
    {
        return Err(Error::Invalid("invalid bracket prices".into()));
    }
    if !match order.side {
        Side::Long => stop < order.entry && order.entry < target,
        Side::Short => target < order.entry && order.entry < stop,
    } {
        return Err(Error::Invalid("invalid bracket direction".into()));
    }
    let entry_price = price(order.entry, order.price_scale)?;
    let stop_price = price(stop, order.price_scale)?;
    let target_price = price(target, order.price_scale)?;
    let (entry_side, exit_side) = match order.side {
        Side::Long => ("BUY", "SELL"),
        Side::Short => ("SELL", "BUY"),
    };
    Ok(json!({"orders":[
        {"acctId":order.account,"conid":conid,"cOID":order.command_id,"orderType":"LMT","side":entry_side,"quantity":order.quantity,"price":entry_price,"tif":"DAY"},
        {"acctId":order.account,"conid":conid,"parentId":order.command_id,"cOID":format!("{}-stop",order.command_id),"orderType":"STP","side":exit_side,"quantity":order.quantity,"price":stop_price,"tif":"GTC"},
        {"acctId":order.account,"conid":conid,"parentId":order.command_id,"cOID":format!("{}-target",order.command_id),"orderType":"LMT","side":exit_side,"quantity":order.quantity,"price":target_price,"tif":"GTC"}
    ]}))
}
#[derive(Debug, PartialEq, Eq)]
pub enum Reply {
    Acknowledged(Vec<String>),
    Confirm { id: String, categories: Vec<String> },
    Blocked,
}
pub fn interpret_reply(value: &Value, allowlist: &BTreeSet<String>) -> Result<Reply> {
    let rows = value
        .as_array()
        .ok_or_else(|| Error::Invalid("IBKR reply is not an array".into()))?;
    if rows.is_empty() {
        return Err(Error::Unready("empty IBKR acknowledgment".into()));
    }
    let mut acknowledgments = vec![];
    for row in rows {
        if row.get("error").is_some() {
            return Ok(Reply::Blocked);
        }
        if let Some(id) = row.get("id").and_then(Value::as_str) {
            if rows.len() != 1 {
                return Err(Error::Unready(
                    "ambiguous mixed confirmation response".into(),
                ));
            }
            let categories = row
                .get("messageIds")
                .and_then(Value::as_array)
                .ok_or_else(|| Error::Unready("uncategorized broker warning".into()))?;
            let categories: Vec<String> = categories
                .iter()
                .map(|v| {
                    v.as_str()
                        .map(str::to_owned)
                        .ok_or_else(|| Error::Invalid("invalid message category".into()))
                })
                .collect::<Result<_>>()?;
            if categories.is_empty() || categories.iter().any(|c| !allowlist.contains(c)) {
                return Ok(Reply::Blocked);
            }
            return Ok(Reply::Confirm {
                id: id.into(),
                categories,
            });
        }
        let id = row
            .get("order_id")
            .and_then(|v| {
                v.as_str()
                    .map(str::to_owned)
                    .or_else(|| v.as_u64().map(|n| n.to_string()))
            })
            .ok_or_else(|| Error::Unready("broker order ID missing".into()))?;
        acknowledgments.push(id);
    }
    Ok(Reply::Acknowledged(acknowledgments))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn broker_prices_preserve_decimal_atoms_without_float_rounding() {
        assert_eq!(
            price(9007199254740993, 2).unwrap().to_string(),
            "90071992547409.93"
        );
        assert_eq!(price(10001, 4).unwrap().to_string(), "1.0001");
        assert!(price(1, 10).is_err());
    }
    #[test]
    fn unknown_warning_is_not_acknowledgment() {
        let reply = json!([{"id":"x","messageIds":["unknown"]}]);
        assert_eq!(
            interpret_reply(&reply, &BTreeSet::new()).unwrap(),
            Reply::Blocked
        );
    }
    #[test]
    fn approved_prompt_still_requires_confirmation() {
        let reply = json!([{"id":"x","messageIds":["o163"]}]);
        assert!(matches!(
            interpret_reply(&reply, &BTreeSet::from(["o163".into()])).unwrap(),
            Reply::Confirm { .. }
        ));
    }
}
