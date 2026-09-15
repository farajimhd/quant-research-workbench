//! Unwindowed topographic prominence with SciPy-compatible flat-peak selection.
//! Equal-height peaks do not delimit a prominence basin. Monotone stacks find
//! strict higher boundaries; a range-minimum tree avoids rescanning periodic data.
use crate::{Error, Result};

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Peak {
    pub index: usize,
    pub prominence: f64,
}

struct MinimumTree {
    base: usize,
    values: Vec<f64>,
}
impl MinimumTree {
    fn new(values: &[f64]) -> Self {
        let base = values.len().next_power_of_two();
        let mut tree = vec![f64::INFINITY; 2 * base];
        tree[base..base + values.len()].copy_from_slice(values);
        for i in (1..base).rev() {
            tree[i] = tree[2 * i].min(tree[2 * i + 1]);
        }
        Self { base, values: tree }
    }
    fn query(&self, start: usize, end: usize) -> f64 {
        let (mut left, mut right) = (start + self.base, end + self.base);
        let mut result = f64::INFINITY;
        while left < right {
            if left % 2 == 1 {
                result = result.min(self.values[left]);
                left += 1;
            }
            if right % 2 == 1 {
                right -= 1;
                result = result.min(self.values[right]);
            }
            left /= 2;
            right /= 2;
        }
        result
    }
}
pub fn find_peaks(values: &[f64], minimum_prominence: f64) -> Result<Vec<Peak>> {
    if !minimum_prominence.is_finite()
        || minimum_prominence < 0.
        || values.iter().any(|v| !v.is_finite())
    {
        return Err(Error::Invalid("invalid prominence input".into()));
    }
    if values.len() < 3 {
        return Ok(Vec::new());
    }
    let n = values.len();
    let mut left = vec![0; n];
    let mut right = vec![n; n];
    let mut stack = Vec::<usize>::with_capacity(n);
    for i in 0..n {
        while stack.last().is_some_and(|&j| values[j] <= values[i]) {
            stack.pop();
        }
        left[i] = stack.last().map_or(0, |j| j + 1);
        stack.push(i);
    }
    stack.clear();
    for i in (0..n).rev() {
        while stack.last().is_some_and(|&j| values[j] <= values[i]) {
            stack.pop();
        }
        right[i] = stack.last().copied().unwrap_or(n);
        stack.push(i);
    }
    let tree = MinimumTree::new(values);
    let mut peaks = Vec::new();
    let mut i = 1;
    while i < n - 1 {
        if values[i - 1] < values[i] {
            let start = i;
            while i + 1 < n && values[i + 1] == values[start] {
                i += 1;
            }
            if i + 1 < n && values[i + 1] < values[start] {
                let center = start + (i - start) / 2;
                let base = tree
                    .query(left[center], center + 1)
                    .max(tree.query(center, right[center]));
                let prominence = values[center] - base;
                if !prominence.is_finite() {
                    return Err(Error::Invalid("prominence overflow".into()));
                }
                if prominence >= minimum_prominence {
                    peaks.push(Peak {
                        index: center,
                        prominence,
                    });
                }
            }
        }
        i += 1;
    }
    Ok(peaks)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn equal_peaks_do_not_bound_basin() {
        let p = find_peaks(&[0., 1., 0., 3., 1., 3., 0., 4., 0.], 0.).unwrap();
        assert_eq!(
            p,
            vec![
                Peak {
                    index: 1,
                    prominence: 1.
                },
                Peak {
                    index: 3,
                    prominence: 3.
                },
                Peak {
                    index: 5,
                    prominence: 3.
                },
                Peak {
                    index: 7,
                    prominence: 4.
                }
            ]
        );
    }
    #[test]
    fn plateau_midpoint_rounds_down_and_edges_are_not_peaks() {
        assert_eq!(
            find_peaks(&[5., 0., 2., 2., 2., 2., 0., 5.], 2.).unwrap(),
            vec![Peak {
                index: 3,
                prominence: 2.
            }]
        );
        assert!(find_peaks(&[1., 1., 1.], 0.).unwrap().is_empty());
    }
    #[test]
    fn range_tree_matches_direct_scanning_exhaustively() {
        for code in 0..3_usize.pow(7) {
            let mut x = code;
            let values: Vec<_> = (0..7)
                .map(|_| {
                    let v = (x % 3) as f64;
                    x /= 3;
                    v
                })
                .collect();
            let actual = find_peaks(&values, 0.).unwrap();
            let mut expected = Vec::new();
            for i in 1..6 {
                let mut lo = i;
                while lo > 0 && values[lo - 1] == values[i] {
                    lo -= 1;
                }
                let mut hi = i;
                while hi + 1 < 7 && values[hi + 1] == values[i] {
                    hi += 1;
                }
                if lo == 0
                    || hi == 6
                    || values[lo - 1] >= values[i]
                    || values[hi + 1] >= values[i]
                    || i != lo + (hi - lo) / 2
                {
                    continue;
                }
                let mut a = i;
                while a > 0 && values[a - 1] <= values[i] {
                    a -= 1;
                }
                let mut b = i;
                while b + 1 < 7 && values[b + 1] <= values[i] {
                    b += 1;
                }
                let lower = values[a..=i].iter().copied().fold(f64::INFINITY, f64::min);
                let upper = values[i..=b].iter().copied().fold(f64::INFINITY, f64::min);
                expected.push(Peak {
                    index: i,
                    prominence: values[i] - lower.max(upper),
                });
            }
            assert_eq!(actual, expected, "input {values:?}");
        }
    }
}
