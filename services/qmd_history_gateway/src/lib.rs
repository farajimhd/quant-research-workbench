// Historical chart builders and execution advancement allocate concurrently.
// Avoid serializing their short-lived vectors on the Windows process heap.
#[global_allocator]
static ALLOCATOR: mimalloc::MiMalloc = mimalloc::MiMalloc;

pub const EXECUTION_RUNTIME_REVISION: u32 = 1;

pub mod api;
pub mod cache;
pub mod config;
pub mod scanner;
pub mod source;
pub mod structure_checkpoint;
pub mod watchlist_timeline;
