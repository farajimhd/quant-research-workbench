use arrow_array::{Array, Int64Array, RecordBatch, UInt32Array, UInt64Array, UInt8Array};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use serde_json::Value;
use std::collections::VecDeque;
use std::ffi::{c_char, CStr};
use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const VERSION: &str = "rolling_loader_rust/0.4.0";

#[repr(C)]
#[derive(Clone, Copy)]
pub struct RustQueueProfileConfig {
    pub ticker_count: u32,
    pub origins_per_ticker: u32,
    pub event_stream_len: u32,
    pub event_feature_count: u32,
    pub batch_size: u32,
    pub realtime_read_workers: u32,
    pub prefetch_read_workers: u32,
    pub realtime_process_workers: u32,
    pub prefetch_process_workers: u32,
    pub prefetch_ticker_count: u32,
    pub read_sleep_us: u32,
    pub process_sleep_us: u32,
}

impl Default for RustQueueProfileConfig {
    fn default() -> Self {
        Self {
            ticker_count: 8_000,
            origins_per_ticker: 512,
            event_stream_len: 1_024,
            event_feature_count: 25,
            batch_size: 1_024,
            realtime_read_workers: 32,
            prefetch_read_workers: 16,
            realtime_process_workers: 32,
            prefetch_process_workers: 16,
            prefetch_ticker_count: 4_000,
            read_sleep_us: 0,
            process_sleep_us: 0,
        }
    }
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct RustQueueProfileStats {
    pub status: i32,
    pub elapsed_ns: u64,
    pub read_jobs_enqueued: u64,
    pub read_jobs_finished: u64,
    pub process_jobs_enqueued: u64,
    pub process_jobs_finished: u64,
    pub realtime_read_jobs: u64,
    pub prefetch_read_jobs: u64,
    pub realtime_process_jobs: u64,
    pub prefetch_process_jobs: u64,
    pub read_priority_steals: u64,
    pub process_priority_steals: u64,
    pub read_worker_ns: u64,
    pub process_worker_ns: u64,
    pub samples: u64,
    pub batches: u64,
    pub cache_tickers: u64,
    pub event_cache_rebuilds: u64,
    pub event_cache_appends: u64,
    pub event_cache_reused: u64,
    pub bytes_allocated: u64,
    pub checksum_bits: u64,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct RustRealCacheProfileConfig {
    pub part_count: u32,
    pub event_stream_len: u32,
    pub batch_size: u32,
    pub realtime_process_workers: u32,
    pub prefetch_process_workers: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct RustRealCachePart {
    pub ticker_id: u64,
    pub event_rows: u64,
    pub origin_count: u64,
    pub feature_count: u32,
    pub priority: u32,
    pub ordinals: *const u64,
    pub features: *const f32,
    pub origin_offsets: *const i64,
    pub origin_ordinals: *const u64,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct RustRealCacheProfileStats {
    pub status: i32,
    pub elapsed_ns: u64,
    pub process_jobs_enqueued: u64,
    pub process_jobs_finished: u64,
    pub realtime_process_jobs: u64,
    pub prefetch_process_jobs: u64,
    pub process_priority_steals: u64,
    pub process_worker_ns: u64,
    pub parts: u64,
    pub event_rows: u64,
    pub origins_seen: u64,
    pub samples: u64,
    pub batches: u64,
    pub invalid_origins: u64,
    pub ordinal_mismatches: u64,
    pub event_cache_rebuilds: u64,
    pub event_cache_appends: u64,
    pub event_cache_reused: u64,
    pub bytes_input: u64,
    pub checksum_bits: u64,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct RustTensorAssemblyConfig {
    pub tensor_count: u32,
    pub realtime_workers: u32,
    pub prefetch_workers: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct RustTensorAssemblySpec {
    pub source: *const u8,
    pub dest: *mut u8,
    pub row_indices: *const u64,
    pub rows: u64,
    pub source_rows: u64,
    pub row_width_bytes: u64,
    pub priority: u32,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct RustTensorAssemblyStats {
    pub status: i32,
    pub elapsed_ns: u64,
    pub jobs_enqueued: u64,
    pub jobs_finished: u64,
    pub realtime_jobs: u64,
    pub prefetch_jobs: u64,
    pub priority_steals: u64,
    pub worker_ns: u64,
    pub tensors: u64,
    pub rows_copied: u64,
    pub bytes_copied: u64,
    pub contiguous_tensors: u64,
    pub gathered_tensors: u64,
    pub invalid_specs: u64,
    pub checksum_bits: u64,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct RustNativeCacheProfileConfig {
    pub cache_root: *const c_char,
    pub month: *const c_char,
    pub ticker_limit: u32,
    pub batch_size: u32,
    pub max_batches: u32,
    pub event_stream_len: u32,
    pub read_workers: u32,
    pub strict: u32,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct RustNativeCacheProfileStats {
    pub status: i32,
    pub elapsed_ns: u64,
    pub packages_discovered: u64,
    pub packages_processed: u64,
    pub parts_processed: u64,
    pub parquet_files_opened: u64,
    pub parquet_rows_seen: u64,
    pub event_rows: u64,
    pub origin_rows: u64,
    pub samples: u64,
    pub batches: u64,
    pub invalid_event_windows: u64,
    pub ordinal_mismatches: u64,
    pub ticker_news_rows: u64,
    pub market_news_rows: u64,
    pub sec_filing_rows: u64,
    pub xbrl_rows: u64,
    pub corporate_action_rows: u64,
    pub ticker_daily_bar_rows: u64,
    pub global_daily_bar_rows: u64,
    pub intraday_base_bar_rows: u64,
    pub scanner_rows: u64,
    pub text_selected: u64,
    pub xbrl_selected: u64,
    pub corporate_action_selected: u64,
    pub ticker_daily_bar_selected: u64,
    pub global_daily_bar_selected: u64,
    pub scanner_dates_touched: u64,
    pub schema_errors: u64,
    pub io_errors: u64,
    pub read_ns: u64,
    pub event_ns: u64,
    pub context_ns: u64,
    pub checksum_bits: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Priority {
    Realtime,
    Prefetch,
}

struct ReadJob {
    ticker_id: u32,
    origins: u32,
    priority: Priority,
}

struct LoadedTickerData {
    ticker_id: u32,
    origins: u32,
    stream_len: usize,
    feature_count: usize,
    events: Arc<Vec<f32>>,
    ordinals: Arc<Vec<u64>>,
    priority: Priority,
}

struct ProcessJob {
    data: LoadedTickerData,
}

struct JobQueue<T> {
    state: Mutex<VecDeque<T>>,
    cv: Condvar,
}

impl<T> JobQueue<T> {
    fn new() -> Self {
        Self {
            state: Mutex::new(VecDeque::new()),
            cv: Condvar::new(),
        }
    }

    fn push(&self, value: T) {
        let mut guard = self.state.lock().expect("queue mutex poisoned");
        guard.push_back(value);
        self.cv.notify_one();
    }

    fn try_pop(&self) -> Option<T> {
        self.state.lock().expect("queue mutex poisoned").pop_front()
    }

    fn is_empty(&self) -> bool {
        self.state.lock().expect("queue mutex poisoned").is_empty()
    }
}

struct QueuePair<T> {
    realtime: Arc<JobQueue<T>>,
    prefetch: Arc<JobQueue<T>>,
}

impl<T> QueuePair<T> {
    fn new() -> Self {
        Self {
            realtime: Arc::new(JobQueue::new()),
            prefetch: Arc::new(JobQueue::new()),
        }
    }

    fn is_empty(&self) -> bool {
        self.realtime.is_empty() && self.prefetch.is_empty()
    }
}

struct EventTickerState {
    stream: Vec<f32>,
    ordinals: Vec<u64>,
    last_ordinal: u64,
}

struct RuntimeShared {
    cfg: RustQueueProfileConfig,
    read_queues: QueuePair<ReadJob>,
    process_queues: QueuePair<ProcessJob>,
    shutdown: AtomicBool,
    read_jobs_enqueued: AtomicU64,
    read_jobs_finished: AtomicU64,
    process_jobs_enqueued: AtomicU64,
    process_jobs_finished: AtomicU64,
    realtime_read_jobs: AtomicU64,
    prefetch_read_jobs: AtomicU64,
    realtime_process_jobs: AtomicU64,
    prefetch_process_jobs: AtomicU64,
    read_priority_steals: AtomicU64,
    process_priority_steals: AtomicU64,
    read_worker_ns: AtomicU64,
    process_worker_ns: AtomicU64,
    samples: AtomicU64,
    event_cache_rebuilds: AtomicU64,
    event_cache_appends: AtomicU64,
    event_cache_reused: AtomicU64,
    bytes_allocated: AtomicU64,
    checksum_bits: AtomicU64,
    event_cache: Vec<Mutex<Option<EventTickerState>>>,
}

impl RuntimeShared {
    fn new(cfg: RustQueueProfileConfig) -> Arc<Self> {
        let cache_slots = cfg
            .ticker_count
            .saturating_add(cfg.prefetch_ticker_count)
            .max(1) as usize;
        Arc::new(Self {
            cfg,
            read_queues: QueuePair::new(),
            process_queues: QueuePair::new(),
            shutdown: AtomicBool::new(false),
            read_jobs_enqueued: AtomicU64::new(0),
            read_jobs_finished: AtomicU64::new(0),
            process_jobs_enqueued: AtomicU64::new(0),
            process_jobs_finished: AtomicU64::new(0),
            realtime_read_jobs: AtomicU64::new(0),
            prefetch_read_jobs: AtomicU64::new(0),
            realtime_process_jobs: AtomicU64::new(0),
            prefetch_process_jobs: AtomicU64::new(0),
            read_priority_steals: AtomicU64::new(0),
            process_priority_steals: AtomicU64::new(0),
            read_worker_ns: AtomicU64::new(0),
            process_worker_ns: AtomicU64::new(0),
            samples: AtomicU64::new(0),
            event_cache_rebuilds: AtomicU64::new(0),
            event_cache_appends: AtomicU64::new(0),
            event_cache_reused: AtomicU64::new(0),
            bytes_allocated: AtomicU64::new(0),
            checksum_bits: AtomicU64::new(0),
            event_cache: (0..cache_slots).map(|_| Mutex::new(None)).collect(),
        })
    }
}

fn sanitize_config(mut cfg: RustQueueProfileConfig) -> RustQueueProfileConfig {
    let default = RustQueueProfileConfig::default();
    if cfg.ticker_count == 0 {
        cfg.ticker_count = default.ticker_count;
    }
    if cfg.origins_per_ticker == 0 {
        cfg.origins_per_ticker = default.origins_per_ticker;
    }
    if cfg.event_stream_len == 0 {
        cfg.event_stream_len = default.event_stream_len;
    }
    if cfg.event_feature_count == 0 {
        cfg.event_feature_count = default.event_feature_count;
    }
    if cfg.batch_size == 0 {
        cfg.batch_size = default.batch_size;
    }
    cfg.realtime_read_workers = cfg.realtime_read_workers.max(1);
    cfg.prefetch_read_workers = cfg.prefetch_read_workers.max(1);
    cfg.realtime_process_workers = cfg.realtime_process_workers.max(1);
    cfg.prefetch_process_workers = cfg.prefetch_process_workers.max(1);
    cfg
}

fn pop_with_priority<T>(
    queues: &QueuePair<T>,
    worker_is_prefetch: bool,
    steal_counter: &AtomicU64,
) -> Option<T> {
    if let Some(job) = queues.realtime.try_pop() {
        if worker_is_prefetch {
            steal_counter.fetch_add(1, Ordering::Relaxed);
        }
        return Some(job);
    }
    queues.prefetch.try_pop()
}

fn enqueue_read(shared: &RuntimeShared, job: ReadJob) {
    shared.read_jobs_enqueued.fetch_add(1, Ordering::Relaxed);
    match job.priority {
        Priority::Realtime => shared.read_queues.realtime.push(job),
        Priority::Prefetch => shared.read_queues.prefetch.push(job),
    }
}

fn enqueue_process(shared: &RuntimeShared, job: ProcessJob) {
    shared.process_jobs_enqueued.fetch_add(1, Ordering::Relaxed);
    match job.data.priority {
        Priority::Realtime => shared.process_queues.realtime.push(job),
        Priority::Prefetch => shared.process_queues.prefetch.push(job),
    }
}

fn read_worker(shared: Arc<RuntimeShared>, worker_is_prefetch: bool) {
    loop {
        if shared.shutdown.load(Ordering::Acquire) && shared.read_queues.is_empty() {
            return;
        }
        let Some(job) = pop_with_priority(
            &shared.read_queues,
            worker_is_prefetch,
            &shared.read_priority_steals,
        ) else {
            thread::sleep(Duration::from_micros(200));
            continue;
        };
        if shared.cfg.read_sleep_us > 0 {
            thread::sleep(Duration::from_micros(shared.cfg.read_sleep_us as u64));
        }
        match job.priority {
            Priority::Realtime => shared.realtime_read_jobs.fetch_add(1, Ordering::Relaxed),
            Priority::Prefetch => shared.prefetch_read_jobs.fetch_add(1, Ordering::Relaxed),
        };
        let started = Instant::now();
        let data = load_ticker_data(&shared.cfg, job);
        shared
            .read_worker_ns
            .fetch_add(started.elapsed().as_nanos() as u64, Ordering::Relaxed);
        shared.bytes_allocated.fetch_add(
            ((data.events.len() * std::mem::size_of::<f32>())
                + (data.ordinals.len() * std::mem::size_of::<u64>())) as u64,
            Ordering::Relaxed,
        );
        enqueue_process(&shared, ProcessJob { data });
        shared.read_jobs_finished.fetch_add(1, Ordering::Release);
    }
}

fn process_worker(shared: Arc<RuntimeShared>, worker_is_prefetch: bool) {
    loop {
        if shared.shutdown.load(Ordering::Acquire) && shared.process_queues.is_empty() {
            return;
        }
        let Some(job) = pop_with_priority(
            &shared.process_queues,
            worker_is_prefetch,
            &shared.process_priority_steals,
        ) else {
            thread::sleep(Duration::from_micros(200));
            continue;
        };
        if shared.cfg.process_sleep_us > 0 {
            thread::sleep(Duration::from_micros(shared.cfg.process_sleep_us as u64));
        }
        match job.data.priority {
            Priority::Realtime => shared.realtime_process_jobs.fetch_add(1, Ordering::Relaxed),
            Priority::Prefetch => shared.prefetch_process_jobs.fetch_add(1, Ordering::Relaxed),
        };
        let started = Instant::now();
        process_ticker_data(&shared, job.data);
        shared
            .process_worker_ns
            .fetch_add(started.elapsed().as_nanos() as u64, Ordering::Relaxed);
        shared.process_jobs_finished.fetch_add(1, Ordering::Release);
    }
}

fn load_ticker_data(cfg: &RustQueueProfileConfig, job: ReadJob) -> LoadedTickerData {
    let stream_len = cfg.event_stream_len.max(1) as usize;
    let feature_count = cfg.event_feature_count.max(1) as usize;
    let origins = job.origins.max(1);
    let rows = stream_len + origins as usize;
    let mut events = vec![0.0_f32; rows * feature_count];
    let mut ordinals = vec![0_u64; rows];
    let ticker_base = job.ticker_id as f32 * 0.001;
    for row in 0..rows {
        ordinals[row] = row as u64 + 1;
        let row_base = row * feature_count;
        for col in 0..feature_count {
            events[row_base + col] = ticker_base + row as f32 * 0.000_001 + col as f32 * 0.01;
        }
    }
    LoadedTickerData {
        ticker_id: job.ticker_id,
        origins,
        stream_len,
        feature_count,
        events: Arc::new(events),
        ordinals: Arc::new(ordinals),
        priority: job.priority,
    }
}

fn process_ticker_data(shared: &RuntimeShared, data: LoadedTickerData) {
    let mut scratch = vec![0.0_f32; data.stream_len * data.feature_count];
    let mut local_samples = 0_u64;
    let mut local_checksum = 0_u64;
    let mut local_rebuilds = 0_u64;
    let mut local_appends = 0_u64;
    let mut local_reused = 0_u64;
    if let Some(slot) = shared.event_cache.get(data.ticker_id as usize) {
        let mut guard = slot.lock().expect("event cache mutex poisoned");
        if guard.is_none() {
            local_rebuilds += 1;
            let end = data.stream_len;
            *guard = Some(EventTickerState {
                stream: data.events[0..end * data.feature_count].to_vec(),
                ordinals: data.ordinals[0..end].to_vec(),
                last_ordinal: data.ordinals[end - 1],
            });
        }
        let state = guard.as_mut().expect("ticker cache initialized");
        for origin_index in 0..data.origins as usize {
            let target_offset = data.stream_len - 1 + origin_index;
            let target_ordinal = data.ordinals[target_offset];
            if target_ordinal > state.last_ordinal {
                append_event_rows(state, &data, target_offset);
                local_appends += target_ordinal.saturating_sub(state.last_ordinal).max(1);
                state.last_ordinal = target_ordinal;
            } else {
                local_reused += 1;
            }
            scratch.copy_from_slice(&state.stream);
            local_checksum ^= sample_checksum_bits(&scratch, data.feature_count);
            local_samples += 1;
        }
    }
    shared.samples.fetch_add(local_samples, Ordering::Relaxed);
    shared
        .event_cache_rebuilds
        .fetch_add(local_rebuilds, Ordering::Relaxed);
    shared
        .event_cache_appends
        .fetch_add(local_appends, Ordering::Relaxed);
    shared
        .event_cache_reused
        .fetch_add(local_reused, Ordering::Relaxed);
    shared
        .checksum_bits
        .fetch_xor(local_checksum, Ordering::Relaxed);
}

fn append_event_rows(state: &mut EventTickerState, data: &LoadedTickerData, target_offset: usize) {
    let target_ordinal = data.ordinals[target_offset];
    if target_ordinal <= state.last_ordinal {
        return;
    }
    let count = (target_ordinal - state.last_ordinal) as usize;
    let stream_len = data.stream_len;
    let features = data.feature_count;
    let append_start = target_offset + 1 - count;
    if count >= stream_len {
        let start = (target_offset + 1 - stream_len) * features;
        let end = (target_offset + 1) * features;
        state.stream.copy_from_slice(&data.events[start..end]);
        state
            .ordinals
            .copy_from_slice(&data.ordinals[target_offset + 1 - stream_len..target_offset + 1]);
        return;
    }
    state
        .stream
        .copy_within(count * features..stream_len * features, 0);
    let target_tail_start = (stream_len - count) * features;
    let source_start = append_start * features;
    let source_end = (target_offset + 1) * features;
    state.stream[target_tail_start..].copy_from_slice(&data.events[source_start..source_end]);
    state.ordinals.copy_within(count..stream_len, 0);
    state.ordinals[stream_len - count..]
        .copy_from_slice(&data.ordinals[append_start..target_offset + 1]);
}

fn sample_checksum_bits(values: &[f32], feature_count: usize) -> u64 {
    if values.is_empty() {
        return 0;
    }
    let mut acc = 0_u64;
    let step = feature_count.max(1);
    for index in (0..values.len()).step_by(step * 64).take(32) {
        acc ^= values[index].to_bits() as u64;
        acc = acc.rotate_left(7);
    }
    acc
}

fn collect_stats(shared: &RuntimeShared, elapsed_ns: u64, status: i32) -> RustQueueProfileStats {
    let cache_tickers = shared
        .event_cache
        .iter()
        .filter(|slot| slot.lock().map(|guard| guard.is_some()).unwrap_or(false))
        .count() as u64;
    let samples = shared.samples.load(Ordering::Relaxed);
    let batches = samples / shared.cfg.batch_size.max(1) as u64;
    RustQueueProfileStats {
        status,
        elapsed_ns,
        read_jobs_enqueued: shared.read_jobs_enqueued.load(Ordering::Relaxed),
        read_jobs_finished: shared.read_jobs_finished.load(Ordering::Relaxed),
        process_jobs_enqueued: shared.process_jobs_enqueued.load(Ordering::Relaxed),
        process_jobs_finished: shared.process_jobs_finished.load(Ordering::Relaxed),
        realtime_read_jobs: shared.realtime_read_jobs.load(Ordering::Relaxed),
        prefetch_read_jobs: shared.prefetch_read_jobs.load(Ordering::Relaxed),
        realtime_process_jobs: shared.realtime_process_jobs.load(Ordering::Relaxed),
        prefetch_process_jobs: shared.prefetch_process_jobs.load(Ordering::Relaxed),
        read_priority_steals: shared.read_priority_steals.load(Ordering::Relaxed),
        process_priority_steals: shared.process_priority_steals.load(Ordering::Relaxed),
        read_worker_ns: shared.read_worker_ns.load(Ordering::Relaxed),
        process_worker_ns: shared.process_worker_ns.load(Ordering::Relaxed),
        samples,
        batches,
        cache_tickers,
        event_cache_rebuilds: shared.event_cache_rebuilds.load(Ordering::Relaxed),
        event_cache_appends: shared.event_cache_appends.load(Ordering::Relaxed),
        event_cache_reused: shared.event_cache_reused.load(Ordering::Relaxed),
        bytes_allocated: shared.bytes_allocated.load(Ordering::Relaxed),
        checksum_bits: shared.checksum_bits.load(Ordering::Relaxed),
    }
}

fn run_profile(cfg: RustQueueProfileConfig) -> RustQueueProfileStats {
    let cfg = sanitize_config(cfg);
    let shared = RuntimeShared::new(cfg);
    let started = Instant::now();
    let mut handles = Vec::new();
    for _ in 0..cfg.realtime_read_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || read_worker(worker_shared, false)));
    }
    for _ in 0..cfg.prefetch_read_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || read_worker(worker_shared, true)));
    }
    for _ in 0..cfg.realtime_process_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || process_worker(worker_shared, false)));
    }
    for _ in 0..cfg.prefetch_process_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || process_worker(worker_shared, true)));
    }
    for ticker in 0..cfg.ticker_count {
        enqueue_read(
            &shared,
            ReadJob {
                ticker_id: ticker,
                origins: cfg.origins_per_ticker,
                priority: Priority::Realtime,
            },
        );
    }
    for offset in 0..cfg.prefetch_ticker_count {
        enqueue_read(
            &shared,
            ReadJob {
                ticker_id: cfg.ticker_count + offset,
                origins: cfg.origins_per_ticker,
                priority: Priority::Prefetch,
            },
        );
    }
    let total_read = cfg.ticker_count as u64 + cfg.prefetch_ticker_count as u64;
    loop {
        let read_done = shared.read_jobs_finished.load(Ordering::Acquire);
        let process_enqueued = shared.process_jobs_enqueued.load(Ordering::Acquire);
        let process_done = shared.process_jobs_finished.load(Ordering::Acquire);
        if read_done >= total_read && process_done >= process_enqueued {
            break;
        }
        thread::sleep(Duration::from_millis(1));
    }
    shared.shutdown.store(true, Ordering::Release);
    for handle in handles {
        let _ = handle.join();
    }
    collect_stats(&shared, started.elapsed().as_nanos() as u64, 0)
}

#[derive(Clone, Copy)]
struct RealPartView {
    ticker_id: u64,
    event_rows: usize,
    origin_count: usize,
    feature_count: usize,
    priority: Priority,
    ordinals: *const u64,
    features: *const f32,
    origin_offsets: *const i64,
    origin_ordinals: *const u64,
}

unsafe impl Send for RealPartView {}
unsafe impl Sync for RealPartView {}

struct RealProcessJob {
    part_index: usize,
}

struct RealRuntimeShared {
    cfg: RustRealCacheProfileConfig,
    parts: Arc<Vec<RealPartView>>,
    process_queues: QueuePair<RealProcessJob>,
    shutdown: AtomicBool,
    process_jobs_enqueued: AtomicU64,
    process_jobs_finished: AtomicU64,
    realtime_process_jobs: AtomicU64,
    prefetch_process_jobs: AtomicU64,
    process_priority_steals: AtomicU64,
    process_worker_ns: AtomicU64,
    origins_seen: AtomicU64,
    samples: AtomicU64,
    invalid_origins: AtomicU64,
    ordinal_mismatches: AtomicU64,
    event_cache_rebuilds: AtomicU64,
    event_cache_appends: AtomicU64,
    event_cache_reused: AtomicU64,
    checksum_bits: AtomicU64,
}

impl RealRuntimeShared {
    fn new(cfg: RustRealCacheProfileConfig, parts: Vec<RealPartView>) -> Arc<Self> {
        Arc::new(Self {
            cfg,
            parts: Arc::new(parts),
            process_queues: QueuePair::new(),
            shutdown: AtomicBool::new(false),
            process_jobs_enqueued: AtomicU64::new(0),
            process_jobs_finished: AtomicU64::new(0),
            realtime_process_jobs: AtomicU64::new(0),
            prefetch_process_jobs: AtomicU64::new(0),
            process_priority_steals: AtomicU64::new(0),
            process_worker_ns: AtomicU64::new(0),
            origins_seen: AtomicU64::new(0),
            samples: AtomicU64::new(0),
            invalid_origins: AtomicU64::new(0),
            ordinal_mismatches: AtomicU64::new(0),
            event_cache_rebuilds: AtomicU64::new(0),
            event_cache_appends: AtomicU64::new(0),
            event_cache_reused: AtomicU64::new(0),
            checksum_bits: AtomicU64::new(0),
        })
    }
}

struct RealPartCounters {
    origins_seen: u64,
    samples: u64,
    invalid_origins: u64,
    ordinal_mismatches: u64,
    rebuilds: u64,
    appends: u64,
    reused: u64,
    checksum_bits: u64,
}

impl RealPartCounters {
    fn new() -> Self {
        Self {
            origins_seen: 0,
            samples: 0,
            invalid_origins: 0,
            ordinal_mismatches: 0,
            rebuilds: 0,
            appends: 0,
            reused: 0,
            checksum_bits: 0,
        }
    }
}

struct RealEventCacheState {
    stream: Vec<f32>,
    last_offset: usize,
}

fn enqueue_real_process(shared: &RealRuntimeShared, job: RealProcessJob, priority: Priority) {
    shared.process_jobs_enqueued.fetch_add(1, Ordering::Relaxed);
    match priority {
        Priority::Realtime => shared.process_queues.realtime.push(job),
        Priority::Prefetch => shared.process_queues.prefetch.push(job),
    }
}

fn real_process_worker(shared: Arc<RealRuntimeShared>, worker_is_prefetch: bool) {
    loop {
        if shared.shutdown.load(Ordering::Acquire) && shared.process_queues.is_empty() {
            return;
        }
        let Some(job) = pop_with_priority(
            &shared.process_queues,
            worker_is_prefetch,
            &shared.process_priority_steals,
        ) else {
            thread::sleep(Duration::from_micros(200));
            continue;
        };
        let Some(part) = shared.parts.get(job.part_index).copied() else {
            shared.invalid_origins.fetch_add(1, Ordering::Relaxed);
            shared.process_jobs_finished.fetch_add(1, Ordering::Release);
            continue;
        };
        match part.priority {
            Priority::Realtime => shared.realtime_process_jobs.fetch_add(1, Ordering::Relaxed),
            Priority::Prefetch => shared.prefetch_process_jobs.fetch_add(1, Ordering::Relaxed),
        };
        let started = Instant::now();
        let counters = process_real_cache_part(&shared.cfg, part);
        shared
            .process_worker_ns
            .fetch_add(started.elapsed().as_nanos() as u64, Ordering::Relaxed);
        shared
            .origins_seen
            .fetch_add(counters.origins_seen, Ordering::Relaxed);
        shared
            .samples
            .fetch_add(counters.samples, Ordering::Relaxed);
        shared
            .invalid_origins
            .fetch_add(counters.invalid_origins, Ordering::Relaxed);
        shared
            .ordinal_mismatches
            .fetch_add(counters.ordinal_mismatches, Ordering::Relaxed);
        shared
            .event_cache_rebuilds
            .fetch_add(counters.rebuilds, Ordering::Relaxed);
        shared
            .event_cache_appends
            .fetch_add(counters.appends, Ordering::Relaxed);
        shared
            .event_cache_reused
            .fetch_add(counters.reused, Ordering::Relaxed);
        shared
            .checksum_bits
            .fetch_xor(counters.checksum_bits, Ordering::Relaxed);
        shared.process_jobs_finished.fetch_add(1, Ordering::Release);
    }
}

fn process_real_cache_part(
    cfg: &RustRealCacheProfileConfig,
    part: RealPartView,
) -> RealPartCounters {
    let mut counters = RealPartCounters::new();
    counters.checksum_bits ^= part.ticker_id.rotate_left(11);
    let stream_len = cfg.event_stream_len.max(1) as usize;
    let features = part.feature_count.max(1);
    if part.event_rows < stream_len || part.origin_count == 0 {
        counters.invalid_origins = part.origin_count as u64;
        counters.origins_seen = part.origin_count as u64;
        return counters;
    }
    let mut state: Option<RealEventCacheState> = None;
    let mut scratch = vec![0.0_f32; stream_len * features];
    for origin_index in 0..part.origin_count {
        counters.origins_seen += 1;
        let offset = unsafe { *part.origin_offsets.add(origin_index) };
        if offset < 0 {
            counters.invalid_origins += 1;
            continue;
        }
        let offset = offset as usize;
        if offset >= part.event_rows || offset + 1 < stream_len {
            counters.invalid_origins += 1;
            continue;
        }
        let event_ordinal = unsafe { *part.ordinals.add(offset) };
        let origin_ordinal = unsafe { *part.origin_ordinals.add(origin_index) };
        if event_ordinal != origin_ordinal {
            counters.ordinal_mismatches += 1;
            continue;
        }
        if state
            .as_ref()
            .map(|cache| offset < cache.last_offset)
            .unwrap_or(true)
        {
            let mut stream = vec![0.0_f32; stream_len * features];
            copy_real_window(&mut stream, part, offset + 1 - stream_len, offset + 1);
            state = Some(RealEventCacheState {
                stream,
                last_offset: offset,
            });
            counters.rebuilds += 1;
        } else if let Some(cache) = state.as_mut() {
            if offset > cache.last_offset {
                let appended = append_real_rows(cache, part, offset, stream_len, features);
                counters.appends += appended as u64;
            } else {
                counters.reused += 1;
            }
        }
        if let Some(cache) = state.as_ref() {
            scratch.copy_from_slice(&cache.stream);
            counters.checksum_bits ^= sample_checksum_bits(&scratch, features);
            counters.samples += 1;
        }
    }
    counters
}

fn copy_real_window(dst: &mut [f32], part: RealPartView, start: usize, end: usize) {
    let features = part.feature_count.max(1);
    let len = (end - start) * features;
    let src = unsafe { std::slice::from_raw_parts(part.features.add(start * features), len) };
    dst.copy_from_slice(src);
}

fn append_real_rows(
    cache: &mut RealEventCacheState,
    part: RealPartView,
    target_offset: usize,
    stream_len: usize,
    features: usize,
) -> usize {
    let count = target_offset.saturating_sub(cache.last_offset);
    if count == 0 {
        return 0;
    }
    if count >= stream_len {
        copy_real_window(
            &mut cache.stream,
            part,
            target_offset + 1 - stream_len,
            target_offset + 1,
        );
        cache.last_offset = target_offset;
        return count;
    }
    cache
        .stream
        .copy_within(count * features..stream_len * features, 0);
    let target_tail_start = (stream_len - count) * features;
    let source_start = (target_offset + 1 - count) * features;
    let source_len = count * features;
    let source = unsafe { std::slice::from_raw_parts(part.features.add(source_start), source_len) };
    cache.stream[target_tail_start..].copy_from_slice(source);
    cache.last_offset = target_offset;
    count
}

fn run_real_cache_profile(
    cfg: RustRealCacheProfileConfig,
    parts: Vec<RealPartView>,
) -> RustRealCacheProfileStats {
    let cfg = RustRealCacheProfileConfig {
        part_count: cfg.part_count,
        event_stream_len: cfg.event_stream_len.max(1),
        batch_size: cfg.batch_size.max(1),
        realtime_process_workers: cfg.realtime_process_workers.max(1),
        prefetch_process_workers: cfg.prefetch_process_workers.max(1),
    };
    let event_rows = parts.iter().map(|part| part.event_rows as u64).sum::<u64>();
    let bytes_input = parts
        .iter()
        .map(|part| {
            (part.event_rows as u64 * part.feature_count as u64 * std::mem::size_of::<f32>() as u64)
                + (part.event_rows as u64 * std::mem::size_of::<u64>() as u64)
                + (part.origin_count as u64 * std::mem::size_of::<i64>() as u64)
                + (part.origin_count as u64 * std::mem::size_of::<u64>() as u64)
        })
        .sum::<u64>();
    let shared = RealRuntimeShared::new(cfg, parts);
    let started = Instant::now();
    let mut handles = Vec::new();
    for _ in 0..cfg.realtime_process_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || {
            real_process_worker(worker_shared, false)
        }));
    }
    for _ in 0..cfg.prefetch_process_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || {
            real_process_worker(worker_shared, true)
        }));
    }
    for part_index in 0..shared.parts.len() {
        let priority = shared.parts[part_index].priority;
        enqueue_real_process(&shared, RealProcessJob { part_index }, priority);
    }
    let total_jobs = shared.parts.len() as u64;
    loop {
        let done = shared.process_jobs_finished.load(Ordering::Acquire);
        if done >= total_jobs {
            break;
        }
        thread::sleep(Duration::from_millis(1));
    }
    shared.shutdown.store(true, Ordering::Release);
    for handle in handles {
        let _ = handle.join();
    }
    let samples = shared.samples.load(Ordering::Relaxed);
    RustRealCacheProfileStats {
        status: 0,
        elapsed_ns: started.elapsed().as_nanos() as u64,
        process_jobs_enqueued: shared.process_jobs_enqueued.load(Ordering::Relaxed),
        process_jobs_finished: shared.process_jobs_finished.load(Ordering::Relaxed),
        realtime_process_jobs: shared.realtime_process_jobs.load(Ordering::Relaxed),
        prefetch_process_jobs: shared.prefetch_process_jobs.load(Ordering::Relaxed),
        process_priority_steals: shared.process_priority_steals.load(Ordering::Relaxed),
        process_worker_ns: shared.process_worker_ns.load(Ordering::Relaxed),
        parts: shared.parts.len() as u64,
        event_rows,
        origins_seen: shared.origins_seen.load(Ordering::Relaxed),
        samples,
        batches: samples / cfg.batch_size.max(1) as u64,
        invalid_origins: shared.invalid_origins.load(Ordering::Relaxed),
        ordinal_mismatches: shared.ordinal_mismatches.load(Ordering::Relaxed),
        event_cache_rebuilds: shared.event_cache_rebuilds.load(Ordering::Relaxed),
        event_cache_appends: shared.event_cache_appends.load(Ordering::Relaxed),
        event_cache_reused: shared.event_cache_reused.load(Ordering::Relaxed),
        bytes_input,
        checksum_bits: shared.checksum_bits.load(Ordering::Relaxed),
    }
}

#[derive(Clone, Copy)]
struct TensorAssemblyView {
    source: *const u8,
    dest: *mut u8,
    row_indices: *const u64,
    rows: usize,
    source_rows: usize,
    row_width_bytes: usize,
    priority: Priority,
}

unsafe impl Send for TensorAssemblyView {}
unsafe impl Sync for TensorAssemblyView {}

struct TensorAssemblyJob {
    tensor_index: usize,
}

struct TensorAssemblyShared {
    tensors: Arc<Vec<TensorAssemblyView>>,
    queues: QueuePair<TensorAssemblyJob>,
    shutdown: AtomicBool,
    jobs_enqueued: AtomicU64,
    jobs_finished: AtomicU64,
    realtime_jobs: AtomicU64,
    prefetch_jobs: AtomicU64,
    priority_steals: AtomicU64,
    worker_ns: AtomicU64,
    rows_copied: AtomicU64,
    bytes_copied: AtomicU64,
    contiguous_tensors: AtomicU64,
    gathered_tensors: AtomicU64,
    invalid_specs: AtomicU64,
    checksum_bits: AtomicU64,
}

impl TensorAssemblyShared {
    fn new(tensors: Vec<TensorAssemblyView>) -> Arc<Self> {
        Arc::new(Self {
            tensors: Arc::new(tensors),
            queues: QueuePair::new(),
            shutdown: AtomicBool::new(false),
            jobs_enqueued: AtomicU64::new(0),
            jobs_finished: AtomicU64::new(0),
            realtime_jobs: AtomicU64::new(0),
            prefetch_jobs: AtomicU64::new(0),
            priority_steals: AtomicU64::new(0),
            worker_ns: AtomicU64::new(0),
            rows_copied: AtomicU64::new(0),
            bytes_copied: AtomicU64::new(0),
            contiguous_tensors: AtomicU64::new(0),
            gathered_tensors: AtomicU64::new(0),
            invalid_specs: AtomicU64::new(0),
            checksum_bits: AtomicU64::new(0),
        })
    }
}

struct TensorAssemblyCounters {
    rows_copied: u64,
    bytes_copied: u64,
    contiguous_tensors: u64,
    gathered_tensors: u64,
    invalid_specs: u64,
    checksum_bits: u64,
}

impl TensorAssemblyCounters {
    fn invalid() -> Self {
        Self {
            rows_copied: 0,
            bytes_copied: 0,
            contiguous_tensors: 0,
            gathered_tensors: 0,
            invalid_specs: 1,
            checksum_bits: 0,
        }
    }
}

fn enqueue_tensor_assembly(
    shared: &TensorAssemblyShared,
    job: TensorAssemblyJob,
    priority: Priority,
) {
    shared.jobs_enqueued.fetch_add(1, Ordering::Relaxed);
    match priority {
        Priority::Realtime => shared.queues.realtime.push(job),
        Priority::Prefetch => shared.queues.prefetch.push(job),
    }
}

fn tensor_assembly_worker(shared: Arc<TensorAssemblyShared>, worker_is_prefetch: bool) {
    loop {
        if shared.shutdown.load(Ordering::Acquire) && shared.queues.is_empty() {
            return;
        }
        let Some(job) =
            pop_with_priority(&shared.queues, worker_is_prefetch, &shared.priority_steals)
        else {
            thread::sleep(Duration::from_micros(200));
            continue;
        };
        let Some(tensor) = shared.tensors.get(job.tensor_index).copied() else {
            shared.invalid_specs.fetch_add(1, Ordering::Relaxed);
            shared.jobs_finished.fetch_add(1, Ordering::Release);
            continue;
        };
        match tensor.priority {
            Priority::Realtime => shared.realtime_jobs.fetch_add(1, Ordering::Relaxed),
            Priority::Prefetch => shared.prefetch_jobs.fetch_add(1, Ordering::Relaxed),
        };
        let started = Instant::now();
        let counters = assemble_tensor(tensor);
        shared
            .worker_ns
            .fetch_add(started.elapsed().as_nanos() as u64, Ordering::Relaxed);
        shared
            .rows_copied
            .fetch_add(counters.rows_copied, Ordering::Relaxed);
        shared
            .bytes_copied
            .fetch_add(counters.bytes_copied, Ordering::Relaxed);
        shared
            .contiguous_tensors
            .fetch_add(counters.contiguous_tensors, Ordering::Relaxed);
        shared
            .gathered_tensors
            .fetch_add(counters.gathered_tensors, Ordering::Relaxed);
        shared
            .invalid_specs
            .fetch_add(counters.invalid_specs, Ordering::Relaxed);
        shared
            .checksum_bits
            .fetch_xor(counters.checksum_bits, Ordering::Relaxed);
        shared.jobs_finished.fetch_add(1, Ordering::Release);
    }
}

fn assemble_tensor(tensor: TensorAssemblyView) -> TensorAssemblyCounters {
    if tensor.source.is_null()
        || tensor.dest.is_null()
        || tensor.rows == 0
        || tensor.source_rows == 0
        || tensor.row_width_bytes == 0
    {
        return TensorAssemblyCounters::invalid();
    }
    let bytes = tensor.rows.saturating_mul(tensor.row_width_bytes);
    let mut checksum = tensor.row_width_bytes as u64 ^ (tensor.rows as u64).rotate_left(17);
    if tensor.row_indices.is_null() {
        let source_bytes = tensor.source_rows.saturating_mul(tensor.row_width_bytes);
        if bytes > source_bytes {
            return TensorAssemblyCounters::invalid();
        }
        unsafe {
            ptr::copy_nonoverlapping(tensor.source, tensor.dest, bytes);
        }
        checksum ^= checksum_bytes(tensor.dest as *const u8, bytes);
        return TensorAssemblyCounters {
            rows_copied: tensor.rows as u64,
            bytes_copied: bytes as u64,
            contiguous_tensors: 1,
            gathered_tensors: 0,
            invalid_specs: 0,
            checksum_bits: checksum,
        };
    }
    for row in 0..tensor.rows {
        let source_row = unsafe { *tensor.row_indices.add(row) } as usize;
        if source_row >= tensor.source_rows {
            return TensorAssemblyCounters::invalid();
        }
        let src = unsafe { tensor.source.add(source_row * tensor.row_width_bytes) };
        let dst = unsafe { tensor.dest.add(row * tensor.row_width_bytes) };
        unsafe {
            ptr::copy_nonoverlapping(src, dst, tensor.row_width_bytes);
        }
        if row < 32 || row + 32 >= tensor.rows || row % 256 == 0 {
            checksum ^= checksum_bytes(dst as *const u8, tensor.row_width_bytes)
                .rotate_left((row % 63) as u32);
        }
    }
    TensorAssemblyCounters {
        rows_copied: tensor.rows as u64,
        bytes_copied: bytes as u64,
        contiguous_tensors: 0,
        gathered_tensors: 1,
        invalid_specs: 0,
        checksum_bits: checksum,
    }
}

fn checksum_bytes(ptr: *const u8, len: usize) -> u64 {
    if ptr.is_null() || len == 0 {
        return 0;
    }
    let bytes = unsafe { std::slice::from_raw_parts(ptr, len) };
    let mut acc = len as u64;
    let step = (len / 32).max(1);
    let mut index = 0_usize;
    while index < len {
        acc ^= bytes[index] as u64;
        acc = acc.rotate_left(5).wrapping_mul(0x9E37_79B1_85EB_CA87);
        index = index.saturating_add(step);
    }
    if len > 1 {
        acc ^= bytes[len - 1] as u64;
    }
    acc
}

fn run_tensor_assembly(
    cfg: RustTensorAssemblyConfig,
    tensors: Vec<TensorAssemblyView>,
) -> RustTensorAssemblyStats {
    let cfg = RustTensorAssemblyConfig {
        tensor_count: tensors.len() as u32,
        realtime_workers: cfg.realtime_workers.max(1),
        prefetch_workers: cfg.prefetch_workers.max(1),
    };
    let shared = TensorAssemblyShared::new(tensors);
    let started = Instant::now();
    let mut handles = Vec::new();
    for _ in 0..cfg.realtime_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || {
            tensor_assembly_worker(worker_shared, false)
        }));
    }
    for _ in 0..cfg.prefetch_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || {
            tensor_assembly_worker(worker_shared, true)
        }));
    }
    for tensor_index in 0..shared.tensors.len() {
        let priority = shared.tensors[tensor_index].priority;
        enqueue_tensor_assembly(&shared, TensorAssemblyJob { tensor_index }, priority);
    }
    let total_jobs = shared.tensors.len() as u64;
    loop {
        let done = shared.jobs_finished.load(Ordering::Acquire);
        if done >= total_jobs {
            break;
        }
        thread::sleep(Duration::from_millis(1));
    }
    shared.shutdown.store(true, Ordering::Release);
    for handle in handles {
        let _ = handle.join();
    }
    RustTensorAssemblyStats {
        status: 0,
        elapsed_ns: started.elapsed().as_nanos() as u64,
        jobs_enqueued: shared.jobs_enqueued.load(Ordering::Relaxed),
        jobs_finished: shared.jobs_finished.load(Ordering::Relaxed),
        realtime_jobs: shared.realtime_jobs.load(Ordering::Relaxed),
        prefetch_jobs: shared.prefetch_jobs.load(Ordering::Relaxed),
        priority_steals: shared.priority_steals.load(Ordering::Relaxed),
        worker_ns: shared.worker_ns.load(Ordering::Relaxed),
        tensors: shared.tensors.len() as u64,
        rows_copied: shared.rows_copied.load(Ordering::Relaxed),
        bytes_copied: shared.bytes_copied.load(Ordering::Relaxed),
        contiguous_tensors: shared.contiguous_tensors.load(Ordering::Relaxed),
        gathered_tensors: shared.gathered_tensors.load(Ordering::Relaxed),
        invalid_specs: shared.invalid_specs.load(Ordering::Relaxed),
        checksum_bits: shared.checksum_bits.load(Ordering::Relaxed),
    }
}

fn run_native_cache_profile(
    cache_root: PathBuf,
    month: String,
    cfg: RustNativeCacheProfileConfig,
) -> RustNativeCacheProfileStats {
    let started = Instant::now();
    let mut base = RustNativeCacheProfileStats {
        status: 0,
        ..RustNativeCacheProfileStats::default()
    };
    let month_dir = cache_root.join(format!("month={month}"));
    let package_dirs = match discover_native_packages(&month_dir, cfg.ticker_limit as usize) {
        Ok(items) => items,
        Err(_) => {
            base.status = -3;
            base.io_errors = 1;
            base.elapsed_ns = started.elapsed().as_nanos() as u64;
            return base;
        }
    };
    base.packages_discovered = package_dirs.len() as u64;
    if package_dirs.is_empty() {
        base.elapsed_ns = started.elapsed().as_nanos() as u64;
        return base;
    }

    let workers = (cfg.read_workers.max(1) as usize).min(package_dirs.len().max(1));
    let target_samples =
        (cfg.batch_size.max(1) as u64).saturating_mul(cfg.max_batches.max(1) as u64);
    let queue = Arc::new(Mutex::new(VecDeque::from(package_dirs)));
    let stats = Arc::new(Mutex::new(base));
    let global_samples = Arc::new(AtomicU64::new(0));
    let strict = cfg.strict != 0;
    let stream_len = cfg.event_stream_len.max(1) as usize;
    let mut handles = Vec::with_capacity(workers);

    for _ in 0..workers {
        let queue = Arc::clone(&queue);
        let stats = Arc::clone(&stats);
        let samples = Arc::clone(&global_samples);
        let cache_root = cache_root.clone();
        let month = month.clone();
        handles.push(thread::spawn(move || loop {
            if samples.load(Ordering::Acquire) >= target_samples {
                break;
            }
            let package_dir = {
                let mut guard = queue.lock().expect("native package queue poisoned");
                guard.pop_front()
            };
            let Some(package_dir) = package_dir else {
                break;
            };
            let local = process_native_package(
                &cache_root,
                &month,
                &package_dir,
                stream_len,
                target_samples,
                &samples,
                strict,
            );
            let mut guard = stats.lock().expect("native stats mutex poisoned");
            combine_native_stats(&mut guard, &local);
            if strict && local.status != 0 {
                guard.status = local.status;
                break;
            }
        }));
    }
    for handle in handles {
        let _ = handle.join();
    }
    let mut out = *stats.lock().expect("native stats mutex poisoned");
    out.samples = global_samples.load(Ordering::Relaxed);
    out.batches = out.samples / cfg.batch_size.max(1) as u64;
    out.elapsed_ns = started.elapsed().as_nanos() as u64;
    out
}

fn discover_native_packages(month_dir: &Path, ticker_limit: usize) -> Result<Vec<PathBuf>, String> {
    let mut dirs = Vec::new();
    for entry in fs::read_dir(month_dir).map_err(|err| err.to_string())? {
        let entry = entry.map_err(|err| err.to_string())?;
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if !name.starts_with("ticker=") {
            continue;
        }
        if path.join("manifest.json").exists() {
            dirs.push(path);
        }
    }
    dirs.sort();
    if ticker_limit > 0 && dirs.len() > ticker_limit {
        dirs.truncate(ticker_limit);
    }
    Ok(dirs)
}

fn process_native_package(
    cache_root: &Path,
    month: &str,
    package_dir: &Path,
    stream_len: usize,
    target_samples: u64,
    global_samples: &AtomicU64,
    strict: bool,
) -> RustNativeCacheProfileStats {
    let mut stats = RustNativeCacheProfileStats {
        status: 0,
        packages_processed: 1,
        ..RustNativeCacheProfileStats::default()
    };
    let manifest_path = package_dir.join("manifest.json");
    let manifest_text = match fs::read_to_string(&manifest_path) {
        Ok(text) => text,
        Err(_) => {
            stats.status = -3;
            stats.io_errors += 1;
            return stats;
        }
    };
    let manifest: Value = match serde_json::from_str(&manifest_text) {
        Ok(value) => value,
        Err(_) => {
            stats.status = -4;
            stats.schema_errors += 1;
            return stats;
        }
    };
    let Some(parts) = manifest.get("parts").and_then(|value| value.as_array()) else {
        stats.status = -4;
        stats.schema_errors += 1;
        return stats;
    };
    let mut event_parts: Vec<&Value> = parts
        .iter()
        .filter(|part| {
            part.get("modality").and_then(|value| value.as_str()) == Some("events")
                && part
                    .get("event_path")
                    .and_then(|value| value.as_str())
                    .is_some()
                && part
                    .get("event_rows")
                    .and_then(|value| value.as_u64())
                    .unwrap_or(0)
                    > 0
        })
        .collect();
    event_parts.sort_by_key(|part| {
        (
            part.get("ordinal_min")
                .and_then(|value| value.as_u64())
                .unwrap_or(0),
            part.get("part_id")
                .and_then(|value| value.as_u64())
                .unwrap_or(0),
        )
    });
    let mut all_event_ordinals: Vec<u64> = Vec::new();
    let mut package_samples = 0_u64;
    for part in event_parts {
        if global_samples.load(Ordering::Acquire) >= target_samples {
            break;
        }
        let Some(event_rel) = part.get("event_path").and_then(|value| value.as_str()) else {
            stats.schema_errors += 1;
            if strict {
                stats.status = -4;
                return stats;
            }
            continue;
        };
        let event_prefix = all_event_ordinals.len() as i64;
        let event_read_started = Instant::now();
        let event_path = cache_root.join(event_rel);
        match read_u64_column(&event_path, "ordinal", &mut stats) {
            Ok(mut values) => {
                stats.event_rows += values.len() as u64;
                all_event_ordinals.append(&mut values);
            }
            Err(_) => {
                if strict {
                    stats.status = -5;
                    stats.event_ns += event_read_started.elapsed().as_nanos() as u64;
                    return stats;
                }
                continue;
            }
        }
        stats.event_ns += event_read_started.elapsed().as_nanos() as u64;
        if part.get("kind").and_then(|value| value.as_str()) != Some("origin") {
            continue;
        }
        let origin_rows = part
            .get("origin_rows")
            .and_then(|value| value.as_u64())
            .unwrap_or(0);
        let event_rows = part
            .get("event_rows")
            .and_then(|value| value.as_u64())
            .unwrap_or(0);
        if origin_rows == 0 || event_rows == 0 {
            continue;
        }
        let Some(origin_rel) = part.get("origin_path").and_then(|value| value.as_str()) else {
            stats.schema_errors += 1;
            if strict {
                stats.status = -4;
                return stats;
            }
            continue;
        };
        let source_date = source_date_from_part(part);
        let origin_path = cache_root.join(origin_rel);

        let origin_started = Instant::now();
        let origin_ordinals = match read_u64_column(&origin_path, "origin_ordinal", &mut stats) {
            Ok(values) => values,
            Err(_) => {
                if strict {
                    stats.status = -5;
                    return stats;
                }
                continue;
            }
        };
        let origin_offsets = match read_i64_column(&origin_path, "event_row_offset", &mut stats) {
            Ok(values) => values,
            Err(_) => {
                if strict {
                    stats.status = -5;
                    return stats;
                }
                continue;
            }
        };
        let origin_timestamps =
            match read_u64_column(&origin_path, "origin_timestamp_us", &mut stats) {
                Ok(values) => values,
                Err(_) => {
                    if strict {
                        stats.status = -5;
                        return stats;
                    }
                    continue;
                }
            };
        let origin_offsets: Vec<i64> = origin_offsets
            .into_iter()
            .map(|value| value.saturating_add(event_prefix))
            .collect();
        stats.origin_rows += origin_ordinals.len() as u64;
        stats.read_ns += origin_started.elapsed().as_nanos() as u64;
        let part_samples = validate_native_event_windows(
            &all_event_ordinals,
            &origin_ordinals,
            &origin_offsets,
            &origin_timestamps,
            stream_len,
            target_samples,
            global_samples,
            &mut stats,
        );
        package_samples += part_samples;
        if part_samples > 0 {
            touch_native_part_day_context(
                cache_root,
                month,
                package_dir,
                &source_date,
                &origin_timestamps,
                &mut stats,
            );
        }
        stats.parts_processed += 1;
    }
    if package_samples > 0 {
        touch_native_context_files(cache_root, month, &manifest, &mut stats);
    }
    stats
}

fn validate_native_event_windows(
    event_ordinals: &[u64],
    origin_ordinals: &[u64],
    origin_offsets: &[i64],
    origin_timestamps: &[u64],
    stream_len: usize,
    target_samples: u64,
    global_samples: &AtomicU64,
    stats: &mut RustNativeCacheProfileStats,
) -> u64 {
    let count = origin_ordinals
        .len()
        .min(origin_offsets.len())
        .min(origin_timestamps.len());
    let mut accepted = 0_u64;
    for row in 0..count {
        if global_samples.load(Ordering::Acquire) >= target_samples {
            break;
        }
        let offset = origin_offsets[row];
        if offset < 0 {
            stats.invalid_event_windows += 1;
            continue;
        }
        let offset = offset as usize;
        if offset >= event_ordinals.len() || offset + 1 < stream_len {
            stats.invalid_event_windows += 1;
            continue;
        }
        if event_ordinals[offset] != origin_ordinals[row] {
            stats.ordinal_mismatches += 1;
            continue;
        }
        let start = offset + 1 - stream_len;
        let end = offset;
        if event_ordinals[end].saturating_sub(event_ordinals[start]) != (stream_len - 1) as u64 {
            stats.invalid_event_windows += 1;
            continue;
        }
        let mut contiguous = true;
        for pos in start + 1..=end {
            if event_ordinals[pos] != event_ordinals[pos - 1].saturating_add(1) {
                contiguous = false;
                break;
            }
        }
        if !contiguous {
            stats.invalid_event_windows += 1;
            continue;
        }
        if !try_reserve_native_sample(global_samples, target_samples) {
            break;
        }
        accepted += 1;
        stats.checksum_bits ^= origin_ordinals[row].rotate_left((row % 63) as u32)
            ^ origin_timestamps[row].rotate_left(((row + 7) % 63) as u32)
            ^ event_ordinals[start].rotate_left(((row + 13) % 63) as u32);
    }
    accepted
}

fn try_reserve_native_sample(global_samples: &AtomicU64, target_samples: u64) -> bool {
    let mut current = global_samples.load(Ordering::Acquire);
    loop {
        if current >= target_samples {
            return false;
        }
        match global_samples.compare_exchange_weak(
            current,
            current.saturating_add(1),
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => return true,
            Err(next) => current = next,
        }
    }
}

fn touch_native_context_files(
    cache_root: &Path,
    month: &str,
    manifest: &Value,
    stats: &mut RustNativeCacheProfileStats,
) {
    let Some(parts) = manifest
        .get("modality_parts")
        .and_then(|value| value.as_array())
    else {
        return;
    };
    for part in parts {
        let Some(paths) = part.get("output_paths").and_then(|value| value.as_object()) else {
            continue;
        };
        for (name, rel) in paths {
            let Some(rel) = rel.as_str() else {
                continue;
            };
            let path = cache_root.join(rel);
            let started = Instant::now();
            let row_count =
                match parquet_row_count_and_validate(&path, required_columns_for_modality(name)) {
                    Ok(rows) => rows,
                    Err(_) => {
                        stats.schema_errors += 1;
                        continue;
                    }
                };
            stats.context_ns += started.elapsed().as_nanos() as u64;
            stats.parquet_files_opened += 1;
            stats.parquet_rows_seen += row_count;
            match name.as_str() {
                "news_embeddings" => stats.ticker_news_rows += row_count,
                "sec_embeddings" => stats.sec_filing_rows += row_count,
                "xbrl" => stats.xbrl_rows += row_count,
                "corporate_actions" => stats.corporate_action_rows += row_count,
                "macro_bars" => stats.ticker_daily_bar_rows += row_count,
                "intraday_base_bars" => stats.intraday_base_bar_rows += row_count,
                _ => {}
            }
        }
    }
    let global_dir = cache_root.join(format!("month={month}")).join("global");
    if let Some(path) = first_parquet_in(&global_dir.join("market_news_embeddings")) {
        if let Ok(rows) = parquet_row_count_and_validate(
            &path,
            &["timestamp_us", "token_chunk_index", "embedding"],
        ) {
            stats.parquet_files_opened += 1;
            stats.parquet_rows_seen += rows;
            stats.market_news_rows += rows;
        }
    }
    if let Some(path) = first_parquet_in(&global_dir.join("global_macro_bars")) {
        if let Ok(rows) = parquet_row_count_and_validate(
            &path,
            &["sym", "bar_start_ms", "bar_family", "open", "close"],
        ) {
            stats.parquet_files_opened += 1;
            stats.parquet_rows_seen += rows;
            stats.global_daily_bar_rows += rows;
        }
    }
}

fn touch_native_part_day_context(
    cache_root: &Path,
    month: &str,
    package_dir: &Path,
    source_date: &str,
    origin_timestamps: &[u64],
    stats: &mut RustNativeCacheProfileStats,
) {
    if source_date.is_empty() || origin_timestamps.is_empty() {
        return;
    }
    let started = Instant::now();
    for dir in [
        "news_embeddings",
        "sec_embeddings",
        "xbrl",
        "corporate_actions",
        "macro_bars",
    ] {
        if let Some(path) = first_parquet_in(&package_dir.join(dir)) {
            let timestamp_col = if dir == "corporate_actions" {
                "available_timestamp_us"
            } else if dir == "macro_bars" {
                "bar_start_ms"
            } else {
                "timestamp_us"
            };
            if let Ok(timestamps) = read_i64_or_u64_column(&path, timestamp_col, stats) {
                let selected = count_asof_selected(
                    &timestamps,
                    origin_timestamps,
                    max_items_for_context_dir(dir),
                );
                match dir {
                    "news_embeddings" => stats.text_selected += selected,
                    "sec_embeddings" => stats.text_selected += selected,
                    "xbrl" => stats.xbrl_selected += selected,
                    "corporate_actions" => stats.corporate_action_selected += selected,
                    "macro_bars" => stats.ticker_daily_bar_selected += selected,
                    _ => {}
                }
            }
        }
    }
    let global_dir = cache_root.join(format!("month={month}")).join("global");
    if let Some(path) = first_parquet_in(&global_dir.join("market_news_embeddings")) {
        if let Ok(timestamps) = read_i64_or_u64_column(&path, "timestamp_us", stats) {
            stats.text_selected += count_asof_selected(&timestamps, origin_timestamps, 16);
        }
    }
    if let Some(path) = first_parquet_in(&global_dir.join("global_macro_bars")) {
        if let Ok(timestamps_ms) = read_i64_or_u64_column(&path, "bar_start_ms", stats) {
            let timestamps_us: Vec<i64> = timestamps_ms
                .iter()
                .map(|value| value.saturating_mul(1000))
                .collect();
            stats.global_daily_bar_selected +=
                count_asof_selected(&timestamps_us, origin_timestamps, 3);
        }
    }
    let scanner_path = global_dir
        .join("scanner")
        .join(format!("scanner_{source_date}.parquet"));
    if scanner_path.exists() {
        if let Ok(rows) = parquet_row_count_and_validate(
            &scanner_path,
            &[
                "source_date",
                "ticker",
                "scanner_bucket",
                "scanner_timestamp_us",
            ],
        ) {
            stats.parquet_files_opened += 1;
            stats.parquet_rows_seen += rows;
            stats.scanner_rows += rows;
            stats.scanner_dates_touched += 1;
        }
    }
    stats.context_ns += started.elapsed().as_nanos() as u64;
}

fn source_date_from_part(part: &Value) -> String {
    if let Some(job_id) = part.get("job_id").and_then(|value| value.as_str()) {
        for token in job_id.split('|') {
            if token.len() == 10
                && token.as_bytes().get(4) == Some(&b'-')
                && token.as_bytes().get(7) == Some(&b'-')
            {
                return token.to_string();
            }
        }
    }
    String::new()
}

fn required_columns_for_modality(name: &str) -> &'static [&'static str] {
    match name {
        "news_embeddings" | "sec_embeddings" => &["timestamp_us", "token_chunk_index", "embedding"],
        "xbrl" => &["timestamp_us", "value", "taxonomy_id", "tag_id"],
        "corporate_actions" => &[
            "available_timestamp_us",
            "effective_timestamp_us",
            "action_type_id",
        ],
        "macro_bars" => &["sym", "bar_start_ms", "bar_family", "open", "close"],
        "intraday_base_bars" => &[
            "local_date",
            "label_resolution_us",
            "bucket_index",
            "bar_family",
            "open",
            "close",
            "event_count",
        ],
        "intraday_condition_events" => &["timestamp_us"],
        _ => &[],
    }
}

fn max_items_for_context_dir(dir: &str) -> usize {
    match dir {
        "news_embeddings" => 8,
        "sec_embeddings" => 4,
        "xbrl" => 4096,
        "corporate_actions" => 128,
        "macro_bars" => 200,
        _ => 1,
    }
}

fn first_parquet_in(dir: &Path) -> Option<PathBuf> {
    let entries = fs::read_dir(dir).ok()?;
    let mut paths = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|value| value.to_str()) == Some("parquet") {
            paths.push(path);
        }
    }
    paths.sort();
    paths.into_iter().next()
}

fn parquet_row_count_and_validate(path: &Path, required: &[&str]) -> Result<u64, String> {
    let file = File::open(path).map_err(|err| err.to_string())?;
    let builder = ParquetRecordBatchReaderBuilder::try_new(file).map_err(|err| err.to_string())?;
    let schema = builder.schema();
    for column in required {
        if schema.index_of(column).is_err() {
            return Err(format!(
                "missing required column {column} in {}",
                path.display()
            ));
        }
    }
    Ok(builder.metadata().file_metadata().num_rows().max(0) as u64)
}

fn read_batches(
    path: &Path,
    stats: &mut RustNativeCacheProfileStats,
) -> Result<Vec<RecordBatch>, String> {
    let file = File::open(path).map_err(|err| {
        stats.io_errors += 1;
        err.to_string()
    })?;
    let builder = ParquetRecordBatchReaderBuilder::try_new(file).map_err(|err| {
        stats.schema_errors += 1;
        err.to_string()
    })?;
    let mut reader = builder.with_batch_size(65_536).build().map_err(|err| {
        stats.schema_errors += 1;
        err.to_string()
    })?;
    let mut batches = Vec::new();
    while let Some(batch) = reader.next() {
        let batch = batch.map_err(|err| {
            stats.io_errors += 1;
            err.to_string()
        })?;
        stats.parquet_rows_seen += batch.num_rows() as u64;
        batches.push(batch);
    }
    stats.parquet_files_opened += 1;
    Ok(batches)
}

fn read_u64_column(
    path: &Path,
    column: &str,
    stats: &mut RustNativeCacheProfileStats,
) -> Result<Vec<u64>, String> {
    let mut values = Vec::new();
    for batch in read_batches(path, stats)? {
        append_u64_from_batch(&batch, column, &mut values)?;
    }
    Ok(values)
}

fn read_i64_column(
    path: &Path,
    column: &str,
    stats: &mut RustNativeCacheProfileStats,
) -> Result<Vec<i64>, String> {
    let mut values = Vec::new();
    for batch in read_batches(path, stats)? {
        append_i64_from_batch(&batch, column, &mut values)?;
    }
    Ok(values)
}

fn read_i64_or_u64_column(
    path: &Path,
    column: &str,
    stats: &mut RustNativeCacheProfileStats,
) -> Result<Vec<i64>, String> {
    let mut values = Vec::new();
    for batch in read_batches(path, stats)? {
        if append_i64_from_batch(&batch, column, &mut values).is_ok() {
            continue;
        }
        let mut unsigned = Vec::new();
        append_u64_from_batch(&batch, column, &mut unsigned)?;
        values.extend(
            unsigned
                .into_iter()
                .map(|value| value.min(i64::MAX as u64) as i64),
        );
    }
    values.sort_unstable();
    Ok(values)
}

fn append_u64_from_batch(
    batch: &RecordBatch,
    column: &str,
    values: &mut Vec<u64>,
) -> Result<(), String> {
    let index = batch
        .schema()
        .index_of(column)
        .map_err(|_| format!("missing column {column}"))?;
    let array = batch.column(index);
    if let Some(array) = array.as_any().downcast_ref::<UInt64Array>() {
        for row in 0..array.len() {
            values.push(if array.is_null(row) {
                0
            } else {
                array.value(row)
            });
        }
        return Ok(());
    }
    if let Some(array) = array.as_any().downcast_ref::<UInt32Array>() {
        for row in 0..array.len() {
            values.push(if array.is_null(row) {
                0
            } else {
                array.value(row) as u64
            });
        }
        return Ok(());
    }
    if let Some(array) = array.as_any().downcast_ref::<UInt8Array>() {
        for row in 0..array.len() {
            values.push(if array.is_null(row) {
                0
            } else {
                array.value(row) as u64
            });
        }
        return Ok(());
    }
    if let Some(array) = array.as_any().downcast_ref::<Int64Array>() {
        for row in 0..array.len() {
            let value = if array.is_null(row) {
                0
            } else {
                array.value(row).max(0) as u64
            };
            values.push(value);
        }
        return Ok(());
    }
    Err(format!("column {column} is not integer-compatible"))
}

fn append_i64_from_batch(
    batch: &RecordBatch,
    column: &str,
    values: &mut Vec<i64>,
) -> Result<(), String> {
    let index = batch
        .schema()
        .index_of(column)
        .map_err(|_| format!("missing column {column}"))?;
    let array = batch.column(index);
    if let Some(array) = array.as_any().downcast_ref::<Int64Array>() {
        for row in 0..array.len() {
            values.push(if array.is_null(row) {
                0
            } else {
                array.value(row)
            });
        }
        return Ok(());
    }
    if let Some(array) = array.as_any().downcast_ref::<UInt64Array>() {
        for row in 0..array.len() {
            values.push(if array.is_null(row) {
                0
            } else {
                array.value(row).min(i64::MAX as u64) as i64
            });
        }
        return Ok(());
    }
    if let Some(array) = array.as_any().downcast_ref::<UInt32Array>() {
        for row in 0..array.len() {
            values.push(if array.is_null(row) {
                0
            } else {
                array.value(row) as i64
            });
        }
        return Ok(());
    }
    Err(format!("column {column} is not integer-compatible"))
}

fn count_asof_selected(timestamps: &[i64], origins_us: &[u64], max_items: usize) -> u64 {
    if timestamps.is_empty() || max_items == 0 {
        return 0;
    }
    let mut selected = 0_u64;
    for origin in origins_us {
        let origin = (*origin).min(i64::MAX as u64) as i64;
        let right = timestamps.partition_point(|value| *value <= origin);
        selected += right.min(max_items) as u64;
    }
    selected
}

fn combine_native_stats(dst: &mut RustNativeCacheProfileStats, src: &RustNativeCacheProfileStats) {
    if src.status != 0 && dst.status == 0 {
        dst.status = src.status;
    }
    dst.packages_processed += src.packages_processed;
    dst.parts_processed += src.parts_processed;
    dst.parquet_files_opened += src.parquet_files_opened;
    dst.parquet_rows_seen += src.parquet_rows_seen;
    dst.event_rows += src.event_rows;
    dst.origin_rows += src.origin_rows;
    dst.invalid_event_windows += src.invalid_event_windows;
    dst.ordinal_mismatches += src.ordinal_mismatches;
    dst.ticker_news_rows += src.ticker_news_rows;
    dst.market_news_rows += src.market_news_rows;
    dst.sec_filing_rows += src.sec_filing_rows;
    dst.xbrl_rows += src.xbrl_rows;
    dst.corporate_action_rows += src.corporate_action_rows;
    dst.ticker_daily_bar_rows += src.ticker_daily_bar_rows;
    dst.global_daily_bar_rows += src.global_daily_bar_rows;
    dst.intraday_base_bar_rows += src.intraday_base_bar_rows;
    dst.scanner_rows += src.scanner_rows;
    dst.text_selected += src.text_selected;
    dst.xbrl_selected += src.xbrl_selected;
    dst.corporate_action_selected += src.corporate_action_selected;
    dst.ticker_daily_bar_selected += src.ticker_daily_bar_selected;
    dst.global_daily_bar_selected += src.global_daily_bar_selected;
    dst.scanner_dates_touched += src.scanner_dates_touched;
    dst.schema_errors += src.schema_errors;
    dst.io_errors += src.io_errors;
    dst.read_ns += src.read_ns;
    dst.event_ns += src.event_ns;
    dst.context_ns += src.context_ns;
    dst.checksum_bits ^= src.checksum_bits;
}

fn c_string(ptr: *const c_char) -> Result<String, String> {
    if ptr.is_null() {
        return Err("null string pointer".to_string());
    }
    let value = unsafe { CStr::from_ptr(ptr) };
    value
        .to_str()
        .map(|text| text.to_string())
        .map_err(|err| err.to_string())
}

#[no_mangle]
pub extern "C" fn rolling_loader_rust_profile(
    config: *const RustQueueProfileConfig,
    stats: *mut RustQueueProfileStats,
) -> i32 {
    if stats.is_null() {
        return -1;
    }
    let cfg = if config.is_null() {
        RustQueueProfileConfig::default()
    } else {
        unsafe { *config }
    };
    let result = run_profile(cfg);
    unsafe {
        ptr::write(stats, result);
    }
    0
}

#[no_mangle]
pub extern "C" fn rolling_loader_rust_profile_real_cache(
    config: *const RustRealCacheProfileConfig,
    parts: *const RustRealCachePart,
    stats: *mut RustRealCacheProfileStats,
) -> i32 {
    if stats.is_null() || config.is_null() || parts.is_null() {
        return -1;
    }
    let cfg = unsafe { *config };
    if cfg.part_count == 0 {
        unsafe {
            ptr::write(
                stats,
                RustRealCacheProfileStats {
                    status: 0,
                    ..RustRealCacheProfileStats::default()
                },
            );
        }
        return 0;
    }
    let raw_parts = unsafe { std::slice::from_raw_parts(parts, cfg.part_count as usize) };
    let mut views = Vec::with_capacity(raw_parts.len());
    for raw in raw_parts {
        if raw.ordinals.is_null()
            || raw.features.is_null()
            || raw.origin_offsets.is_null()
            || raw.origin_ordinals.is_null()
            || raw.event_rows == 0
            || raw.origin_count == 0
            || raw.feature_count == 0
        {
            return -2;
        }
        views.push(RealPartView {
            ticker_id: raw.ticker_id,
            event_rows: raw.event_rows as usize,
            origin_count: raw.origin_count as usize,
            feature_count: raw.feature_count as usize,
            priority: if raw.priority == 0 {
                Priority::Realtime
            } else {
                Priority::Prefetch
            },
            ordinals: raw.ordinals,
            features: raw.features,
            origin_offsets: raw.origin_offsets,
            origin_ordinals: raw.origin_ordinals,
        });
    }
    let result = run_real_cache_profile(cfg, views);
    unsafe {
        ptr::write(stats, result);
    }
    0
}

#[no_mangle]
pub extern "C" fn rolling_loader_rust_assemble_tensors(
    config: *const RustTensorAssemblyConfig,
    tensors: *const RustTensorAssemblySpec,
    stats: *mut RustTensorAssemblyStats,
) -> i32 {
    if stats.is_null() || config.is_null() || tensors.is_null() {
        return -1;
    }
    let cfg = unsafe { *config };
    if cfg.tensor_count == 0 {
        unsafe {
            ptr::write(
                stats,
                RustTensorAssemblyStats {
                    status: 0,
                    ..RustTensorAssemblyStats::default()
                },
            );
        }
        return 0;
    }
    let raw_tensors = unsafe { std::slice::from_raw_parts(tensors, cfg.tensor_count as usize) };
    let mut views = Vec::with_capacity(raw_tensors.len());
    for raw in raw_tensors {
        if raw.source.is_null()
            || raw.dest.is_null()
            || raw.rows == 0
            || raw.source_rows == 0
            || raw.row_width_bytes == 0
        {
            unsafe {
                ptr::write(
                    stats,
                    RustTensorAssemblyStats {
                        status: -2,
                        invalid_specs: 1,
                        ..RustTensorAssemblyStats::default()
                    },
                );
            }
            return -2;
        }
        views.push(TensorAssemblyView {
            source: raw.source,
            dest: raw.dest,
            row_indices: raw.row_indices,
            rows: raw.rows as usize,
            source_rows: raw.source_rows as usize,
            row_width_bytes: raw.row_width_bytes as usize,
            priority: if raw.priority == 0 {
                Priority::Realtime
            } else {
                Priority::Prefetch
            },
        });
    }
    let result = run_tensor_assembly(cfg, views);
    unsafe {
        ptr::write(stats, result);
    }
    0
}

#[no_mangle]
pub extern "C" fn rolling_loader_rust_profile_native_cache(
    config: *const RustNativeCacheProfileConfig,
    stats: *mut RustNativeCacheProfileStats,
) -> i32 {
    if stats.is_null() || config.is_null() {
        return -1;
    }
    let cfg = unsafe { *config };
    let cache_root = match c_string(cfg.cache_root) {
        Ok(value) => PathBuf::from(value),
        Err(_) => {
            unsafe {
                ptr::write(
                    stats,
                    RustNativeCacheProfileStats {
                        status: -2,
                        ..RustNativeCacheProfileStats::default()
                    },
                );
            }
            return -2;
        }
    };
    let month = match c_string(cfg.month) {
        Ok(value) => value,
        Err(_) => {
            unsafe {
                ptr::write(
                    stats,
                    RustNativeCacheProfileStats {
                        status: -2,
                        ..RustNativeCacheProfileStats::default()
                    },
                );
            }
            return -2;
        }
    };
    let result = run_native_cache_profile(cache_root, month, cfg);
    let status = result.status;
    unsafe {
        ptr::write(stats, result);
    }
    status
}

#[no_mangle]
pub extern "C" fn rolling_loader_rust_version(buffer: *mut c_char, buffer_len: usize) -> usize {
    let bytes = VERSION.as_bytes();
    if buffer.is_null() || buffer_len == 0 {
        return bytes.len();
    }
    let copy_len = bytes.len().min(buffer_len.saturating_sub(1));
    unsafe {
        ptr::copy_nonoverlapping(bytes.as_ptr(), buffer as *mut u8, copy_len);
        *buffer.add(copy_len) = 0;
    }
    bytes.len()
}
