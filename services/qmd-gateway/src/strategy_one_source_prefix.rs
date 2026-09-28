//! Bounded acknowledgement accounting for a producer-epoch source prefix.
//!
//! This only proves that locally numbered batches have been acknowledged. It
//! does not prove upstream delivery, ClickHouse row integrity, or a Keeper
//! fence; none of those may be inferred from this in-memory value. In
//! particular, this module must never authorize Strategy 1 live orders.

use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AcknowledgedBatch {
    pub first_sequence: u64,
    pub last_sequence: u64,
    pub event_count: u64,
}

impl AcknowledgedBatch {
    fn validate(self) -> Result<(), &'static str> {
        if self.first_sequence == 0 || self.last_sequence < self.first_sequence {
            return Err("invalid acknowledged sequence range");
        }
        if self
            .last_sequence
            .checked_sub(self.first_sequence)
            .and_then(|width| width.checked_add(1))
            != Some(self.event_count)
        {
            return Err("acknowledged batch is not a complete contiguous range");
        }
        Ok(())
    }
}

/// Tracks out-of-order worker acknowledgements without skipping an unconfirmed
/// batch. The caller must separately bind every range to a durable typed
/// receipt and the canonical rows of a fresh producer epoch.
#[derive(Debug)]
pub struct AcknowledgedPrefix {
    sealed_through: u64,
    pending: BTreeMap<u64, AcknowledgedBatch>,
    max_pending_batches: usize,
}

impl AcknowledgedPrefix {
    pub fn new(max_pending_batches: usize) -> Result<Self, &'static str> {
        if max_pending_batches == 0 {
            return Err("acknowledgement capacity must be positive");
        }
        Ok(Self {
            sealed_through: 0,
            pending: BTreeMap::new(),
            max_pending_batches,
        })
    }

    pub fn sealed_through(&self) -> u64 {
        self.sealed_through
    }

    pub fn pending_batches(&self) -> usize {
        self.pending.len()
    }

    /// Return the new contiguous prefix after a positively acknowledged
    /// batch. Invalid, overlapping, or over-capacity input leaves state intact.
    pub fn acknowledge(&mut self, batch: AcknowledgedBatch) -> Result<u64, &'static str> {
        batch.validate()?;
        if batch.first_sequence <= self.sealed_through {
            return Err("acknowledgement overlaps the sealed prefix");
        }
        // The missing head must always be admissible: it immediately drains
        // at least itself, even when later workers filled the pending bound.
        if self.pending.len() >= self.max_pending_batches
            && self.sealed_through.checked_add(1) != Some(batch.first_sequence)
        {
            return Err("acknowledgement queue capacity exceeded");
        }
        if let Some((_, prior)) = self.pending.range(..=batch.first_sequence).next_back() {
            if prior.last_sequence >= batch.first_sequence {
                return Err("acknowledgement overlaps a pending batch");
            }
        }
        if let Some((_, next)) = self.pending.range(batch.first_sequence..).next() {
            if batch.last_sequence >= next.first_sequence {
                return Err("acknowledgement overlaps a pending batch");
            }
        }
        self.pending.insert(batch.first_sequence, batch);
        while let Some(next_first) = self.sealed_through.checked_add(1) {
            let Some(next) = self.pending.remove(&next_first) else {
                break;
            };
            self.sealed_through = next.last_sequence;
        }
        Ok(self.sealed_through)
    }
}

#[cfg(test)]
mod tests {
    use super::{AcknowledgedBatch, AcknowledgedPrefix};

    fn batch(first: u64, last: u64) -> AcknowledgedBatch {
        AcknowledgedBatch {
            first_sequence: first,
            last_sequence: last,
            event_count: last - first + 1,
        }
    }

    #[test]
    fn out_of_order_acknowledgements_do_not_cross_a_gap() {
        let mut prefix = AcknowledgedPrefix::new(3).unwrap();
        assert_eq!(prefix.acknowledge(batch(3, 4)), Ok(0));
        assert_eq!(prefix.acknowledge(batch(6, 6)), Ok(0));
        assert_eq!(prefix.acknowledge(batch(1, 2)), Ok(4));
        assert_eq!(prefix.acknowledge(batch(5, 5)), Ok(6));
        assert_eq!(prefix.pending_batches(), 0);
    }

    #[test]
    fn rejects_overlap_bad_count_and_capacity_without_mutation() {
        let mut prefix = AcknowledgedPrefix::new(1).unwrap();
        assert_eq!(prefix.acknowledge(batch(2, 4)), Ok(0));
        assert!(prefix.acknowledge(batch(3, 5)).is_err());
        assert!(prefix.acknowledge(batch(5, 5)).is_err());
        assert_eq!(prefix.pending_batches(), 1);
        assert_eq!(prefix.sealed_through(), 0);
        assert!(prefix
            .acknowledge(AcknowledgedBatch {
                first_sequence: 1,
                last_sequence: 1,
                event_count: 2,
            })
            .is_err());
        assert_eq!(prefix.sealed_through(), 0);
        assert_eq!(prefix.acknowledge(batch(1, 1)), Ok(4));
        assert_eq!(prefix.pending_batches(), 0);
    }

    #[test]
    fn rejects_replayed_prefix() {
        let mut prefix = AcknowledgedPrefix::new(2).unwrap();
        assert_eq!(prefix.acknowledge(batch(1, 2)), Ok(2));
        assert!(prefix.acknowledge(batch(2, 3)).is_err());
        assert_eq!(prefix.sealed_through(), 2);
    }

    #[test]
    fn cannot_create_unbounded_or_zero_sequence_tracker() {
        assert!(AcknowledgedPrefix::new(0).is_err());
        let mut prefix = AcknowledgedPrefix::new(1).unwrap();
        assert!(prefix
            .acknowledge(AcknowledgedBatch {
                first_sequence: 0,
                last_sequence: 0,
                event_count: 1,
            })
            .is_err());
    }
}
