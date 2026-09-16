//! Opt-in requested-byte accounting, not allocator committed memory or process RSS.
//! Allocator hooks perform only atomics: no allocation, formatting, locks or resets.
use serde::Serialize;
use std::alloc::{GlobalAlloc, Layout};
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};

pub struct CountingAllocator;

struct Counters {
    live: AtomicU64,
    peak: AtomicU64,
    allocations: AtomicU64,
    bytes: AtomicU64,
    reallocations: AtomicU64,
    zeroed: AtomicU64,
    failures: AtomicU64,
}

impl Counters {
    const fn new() -> Self {
        Self {
            live: AtomicU64::new(0),
            peak: AtomicU64::new(0),
            allocations: AtomicU64::new(0),
            bytes: AtomicU64::new(0),
            reallocations: AtomicU64::new(0),
            zeroed: AtomicU64::new(0),
            failures: AtomicU64::new(0),
        }
    }

    fn succeeded(&self, old: usize, new: usize, realloc: bool, zeroed: bool) {
        // Realloc records the full new request in cumulative bytes. Only its
        // size delta affects live bytes, whether moved or grown in place.
        self.allocations.fetch_add(1, Relaxed);
        self.bytes.fetch_add(new as u64, Relaxed);
        if realloc {
            self.reallocations.fetch_add(1, Relaxed);
        }
        if zeroed {
            self.zeroed.fetch_add(1, Relaxed);
        }
        if new >= old {
            let live = self.live.fetch_add((new - old) as u64, Relaxed) + (new - old) as u64;
            self.peak.fetch_max(live, Relaxed);
        } else {
            self.live.fetch_sub((old - new) as u64, Relaxed);
        }
    }

    fn failed(&self) {
        self.failures.fetch_add(1, Relaxed);
    }
    fn freed(&self, size: usize) {
        self.live.fetch_sub(size as u64, Relaxed);
    }
}

static COUNTERS: Counters = Counters::new();

// SAFETY: Every operation is delegated with the caller's original allocation
// contract. Failed realloc leaves the old allocation and its accounting intact.
unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let ptr = unsafe { mimalloc::MiMalloc.alloc(layout) };
        if ptr.is_null() {
            COUNTERS.failed();
        } else {
            COUNTERS.succeeded(0, layout.size(), false, false);
        }
        ptr
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        let ptr = unsafe { mimalloc::MiMalloc.alloc_zeroed(layout) };
        if ptr.is_null() {
            COUNTERS.failed();
        } else {
            COUNTERS.succeeded(0, layout.size(), false, true);
        }
        ptr
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        COUNTERS.freed(layout.size());
        unsafe { mimalloc::MiMalloc.dealloc(ptr, layout) };
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        let replacement = unsafe { mimalloc::MiMalloc.realloc(ptr, layout, new_size) };
        if replacement.is_null() {
            COUNTERS.failed();
        } else {
            COUNTERS.succeeded(layout.size(), new_size, true, false);
        }
        replacement
    }
}

#[derive(Serialize)]
pub struct AllocationSnapshot {
    pub contract: &'static str,
    pub scope: &'static str,
    pub sampling: &'static str,
    pub current_requested_live_bytes: u64,
    pub peak_requested_live_bytes: u64,
    /// Successful alloc + alloc_zeroed + realloc calls.
    pub cumulative_allocations: u64,
    /// Full requested sizes, including each successful realloc's new size.
    pub cumulative_requested_bytes: u64,
    pub successful_reallocations: u64,
    pub successful_zeroed_allocations: u64,
    pub failed_allocation_calls: u64,
}

pub fn snapshot() -> AllocationSnapshot {
    AllocationSnapshot {
        contract: "rust-requested-allocation-diagnostics-1",
        scope: "Rust global allocator only; excludes native direct allocations and allocator retained pages",
        sampling: "independent atomic reads; concurrent activity may skew cross-counter comparisons",
        current_requested_live_bytes: COUNTERS.live.load(Relaxed),
        peak_requested_live_bytes: COUNTERS.peak.load(Relaxed),
        cumulative_allocations: COUNTERS.allocations.load(Relaxed),
        cumulative_requested_bytes: COUNTERS.bytes.load(Relaxed),
        successful_reallocations: COUNTERS.reallocations.load(Relaxed),
        successful_zeroed_allocations: COUNTERS.zeroed.load(Relaxed),
        failed_allocation_calls: COUNTERS.failures.load(Relaxed),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn allocation_zeroed_realloc_growth_shrink_failure_and_free() {
        let c = Counters::new();
        c.succeeded(0, 10, false, false);
        c.succeeded(0, 20, false, true);
        c.succeeded(10, 40, true, false);
        assert_eq!(c.live.load(Relaxed), 60);
        c.failed(); // Failed realloc must leave the old 40-byte block counted.
        assert_eq!(c.live.load(Relaxed), 60);
        c.succeeded(40, 5, true, false);
        assert_eq!(c.live.load(Relaxed), 25);
        assert_eq!(c.peak.load(Relaxed), 60);
        assert_eq!(c.bytes.load(Relaxed), 75);
        assert_eq!(c.allocations.load(Relaxed), 4);
        assert_eq!(c.reallocations.load(Relaxed), 2);
        assert_eq!(c.zeroed.load(Relaxed), 1);
        assert_eq!(c.failures.load(Relaxed), 1);
        c.freed(5);
        c.freed(20);
        assert_eq!(c.live.load(Relaxed), 0);
        assert_eq!(c.peak.load(Relaxed), 60);
    }
}
