# Acquisition Module

**Location**: `src/adapt/modules/acquisition/module.py`

---

## Responsibility

The acquisition module is the **data ingress boundary** of the system. Its sole job is to discover and download raw NEXRAD Level-II files from AWS S3 and deliver local file paths to the rest of the pipeline.

It runs in its own thread, independent of all processing. It has no knowledge of what happens to the files after delivery.

---

## What It Is Not Responsible For

- Parsing or reading file contents — that is the ingest module's job
- Managing the output directory structure — that is the repository's job
- Tracking which files have been processed — that is the file tracker's job
- Retrying failed downloads indefinitely — fail loudly on persistent failure
- Making decisions about which files are scientifically relevant — deliver all files, let the pipeline decide

---

## Interface

The acquisition module is a `threading.Thread` subclass, not a `BaseModule`. It runs concurrently with the processor and communicates via a thread-safe queue.

```
AwsNexradDownloader
├── Inputs (constructor):
│   ├── radar_id: str              # NEXRAD site (e.g., "KDIX")
│   ├── config: InternalConfig     # Mode, timing parameters
│   ├── output_dir: Path           # Where to write downloaded files
│   └── queue: Queue               # Channel to processor thread
│
└── Output (to queue):
    └── {"path": Path, "queued_at": float}   # One dict per file
```

---

## Operating Modes

### Realtime Mode

Checks once that the archive has the radar today or yesterday (else the run fails), takes the newest `latest_files` of the last `latest_minutes`, then lists only the keys after the last one seen. When it lists is set by the age of the last volume's S3 publication time (`schedule.next_check_at`): nothing before `first_check_sec`, then every 20 s, every 10 s from `expected_arrival_sec`, every 30 s from `late_after_sec`. While a scan waits in the queue only the newest new volume is kept; the others are logged as skipped, and `max_skipped_scans` skips stop the run.

This mode runs until externally stopped (Ctrl+C or `orchestrator.stop()`).

### Historical Mode

Lists the fixed time range (`start_time` to `end_time`) one UTC day at a time and enqueues each day before listing the next. No polling. A range without volumes, or a listing that fails its retries, fails the run.

---

## Thread Lifecycle

```
AwsNexradDownloader.run()
    │
    ├── [ Historical ] for each UTC day of the range
    │       │
    │       └── list the day → for each file: download → queue.put(message)
    │
    └── [ Realtime ] check the radar, then loop until stopped (1 s ticks)
            │
            ├── at the scheduled time: list keys after the last one seen
            ├── newest new volume = candidate; older ones skipped and logged
            └── queue empty → download the candidate → queue.put(message)
```

The thread signals completion by putting a sentinel value (`None`) into the queue. The processor thread exits its loop when it receives the sentinel.

---

## Connection to the Rest of the System

The acquisition module is the **only component that touches AWS S3**. All other components operate on local files.

```
AWS S3
  ↓
AwsNexradDownloader (thread)
  ↓  queue.put(filepath)
RadarProcessor (thread)
  ↓  queue.get()
IngestModule.run(context)
```

The queue is the only coupling between acquisition and processing. Neither thread knows anything about the other's implementation.

---

## Design Notes

**Why a thread, not a BaseModule?**

The acquisition module has a fundamentally different execution model from processing modules. It runs continuously in the background, driven by time rather than by data availability. Fitting it into the `BaseModule` interface (which runs once per file on demand) would require awkward state management. A thread with a queue is the natural, honest model.

**Why is the queue bounded (size=20)?**

Back-pressure. If the processor is slower than the downloader (e.g., during historical replay of a large archive), the downloader will block rather than accumulating an unbounded queue that consumes memory. This is a deliberate flow-control mechanism.
