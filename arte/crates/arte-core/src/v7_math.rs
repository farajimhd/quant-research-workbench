//! Student-t objective ported from the pinned reaction-center reference.
//! This is a numerical primitive, not a replacement V7 fit/partition implementation.
use crate::{Error, Result};

pub fn objective_gradient(
    location: f64,
    log_scale: f64,
    prices: &[f64],
) -> Result<(f64, [f64; 2])> {
    if prices.is_empty()
        || !location.is_finite()
        || !log_scale.is_finite()
        || prices.iter().any(|x| !x.is_finite())
    {
        return Err(Error::Invalid("invalid Student-t objective input".into()));
    }
    let scale = log_scale.exp();
    if !scale.is_finite() || scale <= 0. {
        return Err(Error::Invalid("invalid Student-t scale".into()));
    }
    let (mut loss, mut location_sum, mut scale_sum) = (0., 0., 0.);
    // Contiguous independent arithmetic; no allocation or trait dispatch in the loop.
    for &x in prices {
        let z = (x - location) / scale;
        let z2 = z * z;
        let denominator = 4. + z2;
        loss += (z2 / 4.).ln_1p();
        location_sum += z / denominator;
        scale_sum += z2 / denominator;
    }
    let count = prices.len() as f64;
    let value = log_scale + 2.5 * loss / count;
    let gradient = [
        -5. * location_sum / count / scale,
        1. - 5. * scale_sum / count,
    ];
    if !value.is_finite() || gradient.iter().any(|g| !g.is_finite()) {
        return Err(Error::Invalid("Student-t objective overflow".into()));
    }
    Ok((value, gradient))
}

/// Batch level association over structure-of-arrays data. Stable index order is retained.
pub fn associated_levels(
    price: f64,
    radius: f64,
    centers: &[f64],
    radii: &[f64],
) -> Result<Vec<usize>> {
    if centers.len() != radii.len()
        || !price.is_finite()
        || !radius.is_finite()
        || radius < 0.
        || centers.iter().any(|x| !x.is_finite())
        || radii.iter().any(|r| !r.is_finite() || *r < 0.)
    {
        return Err(Error::Invalid("invalid level association arrays".into()));
    }
    Ok(centers
        .iter()
        .zip(radii)
        .enumerate()
        .filter_map(|(i, (&center, &r))| ((center - price).abs() <= radius.max(r)).then_some(i))
        .collect())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn analytic_gradient_matches_finite_difference() {
        let x = [-2., 0., 1., 4.];
        let (mu, log_scale) = (0.3, 0.2);
        let (_, g) = objective_gradient(mu, log_scale, &x).unwrap();
        let h = 1e-6;
        let dmu = (objective_gradient(mu + h, log_scale, &x).unwrap().0
            - objective_gradient(mu - h, log_scale, &x).unwrap().0)
            / (2. * h);
        let ds = (objective_gradient(mu, log_scale + h, &x).unwrap().0
            - objective_gradient(mu, log_scale - h, &x).unwrap().0)
            / (2. * h);
        assert!((g[0] - dmu).abs() < 1e-8);
        assert!((g[1] - ds).abs() < 1e-8);
    }
    #[test]
    fn stable_array_matching() {
        assert_eq!(
            associated_levels(10., 0.1, &[9., 10., 10.2], &[0.1, 0.1, 0.3]).unwrap(),
            vec![1, 2]
        );
    }
}
