// Historical chart builders and execution advancement allocate concurrently.
// Avoid serializing their short-lived vectors on the Windows process heap.
#[global_allocator]
#[cfg(not(feature = "allocation-diagnostics"))]
static ALLOCATOR: mimalloc::MiMalloc = mimalloc::MiMalloc;

#[cfg(feature = "allocation-diagnostics")]
pub mod allocation_diagnostics;
#[cfg(feature = "allocation-diagnostics")]
#[global_allocator]
static ALLOCATOR: allocation_diagnostics::CountingAllocator = allocation_diagnostics::CountingAllocator;

pub const EXECUTION_RUNTIME_REVISION: u32 = 1;

pub mod api;
pub mod cache;
pub mod config;
pub mod scanner;
pub mod relative_volume;
pub mod source;
pub mod structure_checkpoint;
pub mod watchlist_timeline;
