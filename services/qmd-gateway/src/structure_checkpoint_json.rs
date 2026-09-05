//! Decode durable state without the default JSON parser's lossy decimal-to-f64 path.
//!
//! Keep the existing certification canonicalizer unchanged: historical certificates
//! bind that representation. The state itself must recover the exact binary floats
//! emitted by the serializer, including after repeated save/load cycles.
use crate::generic_structure::GenericStructureCheckpoint;
use serde_json::{value::RawValue, Map, Number, Value};

pub fn decode_checkpoint(text: &str) -> Result<GenericStructureCheckpoint, String> {
    let raw: &RawValue =
        serde_json::from_str(text).map_err(|error| format!("invalid checkpoint JSON: {error}"))?;
    serde_json::from_value(exact_value(raw, 0)?)
        .map_err(|error| format!("invalid checkpoint state: {error}"))
}

fn exact_value(raw: &RawValue, depth: usize) -> Result<Value, String> {
    if depth > 128 {
        return Err("checkpoint JSON nesting exceeds 128".into());
    }
    let text = raw.get();
    match text.as_bytes().first() {
        Some(b'{') => {
            let fields: Map<String, Value> =
                serde_json::from_str::<std::collections::BTreeMap<String, &RawValue>>(text)
                    .map_err(|e| e.to_string())?
                    .into_iter()
                    .map(|(key, value)| Ok((key, exact_value(value, depth + 1)?)))
                    .collect::<Result<_, String>>()?;
            Ok(Value::Object(fields))
        }
        Some(b'[') => Ok(Value::Array(
            serde_json::from_str::<Vec<&RawValue>>(text)
                .map_err(|e| e.to_string())?
                .into_iter()
                .map(|value| exact_value(value, depth + 1))
                .collect::<Result<_, _>>()?,
        )),
        Some(b'-' | b'0'..=b'9') => {
            // Preserve integer identity and all 64 bits of cursors/counts. Decimal
            // and exponent tokens use Rust's correctly rounded float conversion.
            if !text.contains(['.', 'e', 'E']) && text != "-0" {
                if let Ok(value) = text.parse::<u64>() {
                    return Ok(Value::from(value));
                }
                if let Ok(value) = text.parse::<i64>() {
                    return Ok(Value::from(value));
                }
            }
            let value = text.parse::<f64>().map_err(|e| e.to_string())?;
            Number::from_f64(value)
                .map(Value::Number)
                .ok_or_else(|| "non-finite checkpoint number".into())
        }
        _ => serde_json::from_str(text).map_err(|e| e.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        generic_structure::GenericStructureEngine, structure_certification::checkpoint_sha256,
    };

    #[test]
    fn checkpoint_float_bits_and_certificate_survive_repeated_roundtrips() {
        let mut state =
            serde_json::to_value(GenericStructureEngine::new("ROUNDTRIP").checkpoint()).unwrap();
        // Sweep decimal geometries, tiny values, signed zero, and large exact cursors.
        for value in [
            0.1,
            1.2345678901234567,
            4.068391243338,
            -0.0,
            f64::MIN_POSITIVE,
            f64::MAX,
            100000.00000000001,
        ] {
            state["last_reference_price"] = Value::from(value);
            state["last_arrival_sequence"] = Value::from(u64::MAX);
            let original: GenericStructureCheckpoint =
                serde_json::from_value(state.clone()).unwrap();
            let expected_hash = checkpoint_sha256(&original).unwrap();
            let mut checkpoint = original;
            for _ in 0..8 {
                checkpoint =
                    decode_checkpoint(&serde_json::to_string(&checkpoint).unwrap()).unwrap();
                let decoded = serde_json::to_value(&checkpoint).unwrap();
                assert_eq!(
                    decoded["last_reference_price"].as_f64().unwrap().to_bits(),
                    value.to_bits()
                );
                assert_eq!(checkpoint.last_arrival_sequence, u64::MAX);
                assert_eq!(checkpoint_sha256(&checkpoint).unwrap(), expected_hash);
            }
        }
    }

    #[test]
    fn reproduces_and_repairs_legacy_double_decode_hash_drift() {
        let mut state =
            serde_json::to_value(GenericStructureEngine::new("DRIFT").checkpoint()).unwrap();
        let mut bits = 0x123456789abcdef0_u64;
        let mut legacy_drifts = 0;
        for _ in 0..10000 {
            bits ^= bits << 13;
            bits ^= bits >> 7;
            bits ^= bits << 17;
            let price = f64::from_bits((bits & ((1_u64 << 52) - 1)) | ((900 + bits % 250) << 52));
            state["last_reference_price"] = Value::from(price);
            let original: GenericStructureCheckpoint =
                serde_json::from_value(state.clone()).unwrap();
            let text = serde_json::to_string(&original).unwrap();
            let legacy: GenericStructureCheckpoint = serde_json::from_str(&text).unwrap();
            let expected = checkpoint_sha256(&original).unwrap();
            if checkpoint_sha256(&legacy).unwrap() != expected {
                legacy_drifts += 1;
            }
            assert_eq!(
                checkpoint_sha256(&decode_checkpoint(&text).unwrap()).unwrap(),
                expected
            );
        }
        assert!(
            legacy_drifts > 0,
            "fixture must exercise the production failure"
        );
        eprintln!("legacy hash drift cases repaired: {legacy_drifts}/10000");
    }
}
