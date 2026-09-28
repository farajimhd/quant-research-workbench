//! Bounded acknowledgement accounting for a producer-epoch source prefix.
//!
//! This only proves that locally numbered batches have been acknowledged. It
//! does not prove upstream delivery, ClickHouse row integrity, or a Keeper
//! fence; none of those may be inferred from this in-memory value. In
//! particular, this module must never authorize Strategy 1 live orders.

use std::collections::BTreeSet;

/// Tracks out-of-order worker acknowledgements without skipping an unconfirmed
/// event. QMD's per-ticker reorder lane makes a persistence batch sparse in
/// globally assigned arrival sequences. The caller must separately bind each
/// exact sequence set to a durable typed receipt and canonical rows of a fresh
/// producer epoch.
#[derive(Debug)]
pub struct AcknowledgedPrefix {
    sealed_through: u64,
    pending: BTreeSet<u64>,
    max_pending_sequences: usize,
}

impl AcknowledgedPrefix {
    pub fn new(max_pending_sequences: usize) -> Result<Self, &'static str> {
        if max_pending_sequences == 0 {
            return Err("acknowledgement capacity must be positive");
        }
        Ok(Self {
            sealed_through: 0,
            pending: BTreeSet::new(),
            max_pending_sequences,
        })
    }

    pub fn sealed_through(&self) -> u64 {
        self.sealed_through
    }

    pub fn pending_sequences(&self) -> usize {
        self.pending.len()
    }

    /// Return the new contiguous prefix after a positively acknowledged
    /// sparse batch. Invalid, overlapping, or over-capacity input leaves state
    /// intact. Sequences need not be ordered; a batch cannot acknowledge an
    /// unobserved sequence in the numeric interval between its extrema.
    pub fn acknowledge(&mut self, sequences: &[u64]) -> Result<u64, &'static str> {
        if sequences.is_empty() {
            return Err("acknowledgement batch is empty");
        }
        let mut additions = BTreeSet::new();
        for &sequence in sequences {
            if sequence == 0 || sequence <= self.sealed_through {
                return Err("acknowledgement overlaps the sealed prefix");
            }
            if self.pending.contains(&sequence) || !additions.insert(sequence) {
                return Err("acknowledgement contains a duplicate sequence");
            }
        }
        // Check the post-drain bound before mutating anything. This permits a
        // missing head to arrive even when later acknowledgements filled the
        // queue, but never admits an unbounded sparse tail.
        let mut next = self.sealed_through;
        while let Some(candidate) = next.checked_add(1) {
            if !self.pending.contains(&candidate) && !additions.contains(&candidate) {
                break;
            }
            next = candidate;
        }
        let remaining = self.pending.len() + additions.len()
            - self.pending.range(..=next).count()
            - additions.range(..=next).count();
        if remaining > self.max_pending_sequences {
            return Err("acknowledgement queue capacity exceeded");
        }
        self.pending.extend(additions);
        self.pending.retain(|sequence| *sequence > next);
        self.sealed_through = next;
        Ok(self.sealed_through)
    }
}

#[cfg(test)]
mod tests {
    use super::AcknowledgedPrefix;

    #[test]
    fn out_of_order_acknowledgements_do_not_cross_a_gap() {
        let mut prefix = AcknowledgedPrefix::new(3).unwrap();
        assert_eq!(prefix.acknowledge(&[4, 3]), Ok(0));
        assert_eq!(prefix.acknowledge(&[6]), Ok(0));
        assert_eq!(prefix.acknowledge(&[1, 2]), Ok(4));
        assert_eq!(prefix.acknowledge(&[5]), Ok(6));
        assert_eq!(prefix.pending_sequences(), 0);
    }

    #[test]
    fn rejects_overlap_bad_count_and_capacity_without_mutation() {
        let mut prefix = AcknowledgedPrefix::new(1).unwrap();
        assert_eq!(prefix.acknowledge(&[2]), Ok(0));
        assert!(prefix.acknowledge(&[2, 5]).is_err());
        assert!(prefix.acknowledge(&[5]).is_err());
        assert_eq!(prefix.pending_sequences(), 1);
        assert_eq!(prefix.sealed_through(), 0);
        assert!(prefix.acknowledge(&[1, 1]).is_err());
        assert_eq!(prefix.sealed_through(), 0);
        assert_eq!(prefix.acknowledge(&[1]), Ok(2));
        assert_eq!(prefix.pending_sequences(), 0);
    }

    #[test]
    fn rejects_replayed_prefix() {
        let mut prefix = AcknowledgedPrefix::new(2).unwrap();
        assert_eq!(prefix.acknowledge(&[1, 2]), Ok(2));
        assert!(prefix.acknowledge(&[2, 3]).is_err());
        assert_eq!(prefix.sealed_through(), 2);
    }

    #[test]
    fn cannot_create_unbounded_or_zero_sequence_tracker() {
        assert!(AcknowledgedPrefix::new(0).is_err());
        let mut prefix = AcknowledgedPrefix::new(1).unwrap();
        assert!(prefix.acknowledge(&[]).is_err());
        assert!(prefix.acknowledge(&[0]).is_err());
    }

    #[test]
    fn sparse_batch_does_not_claim_unseen_middle_sequences() {
        let mut prefix = AcknowledgedPrefix::new(3).unwrap();
        assert_eq!(prefix.acknowledge(&[1, 3]), Ok(1));
        assert_eq!(prefix.pending_sequences(), 1);
        assert_eq!(prefix.acknowledge(&[2]), Ok(3));
    }
}
