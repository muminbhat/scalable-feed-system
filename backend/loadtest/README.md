## Load testing (ApacheBench)

### Prerequisites
- Python env: use your `fst` venv (or any env with project deps installed).
- ApacheBench (`ab`).
  - This repo includes a working Windows build at `loadtest/tools/httpd/Apache24/bin/ab.exe` (with required DLLs).
  - The harness will auto-detect and use it, so you **do not** need `ab` on PATH.

### What this produces
- `loadtest/results.csv`: one row per run
- `loadtest/results.jsonl`: raw parsed results per run
- `loadtest/report.md`: summary tables
- `loadtest/report.html`: interactive charts

### Quick start
From `app/backend`:

```powershell
& "..\\..\\fst\\Scripts\\python.exe" -m loadtest.run --base-url "http://127.0.0.1:8000" --hot-user-id 2
```

This will:
- Reset DB between dataset sizes
- Seed stored events at: 100k, 300k, 500k, 700k, 900k
- Benchmark endpoints with concurrency: 200, 600, 1000, 1400, 1800
- Write results + charts into `loadtest/`

### Notes
- `ab` is not suitable for long-lived SSE streams. The harness benchmarks:
  - `POST /api/events`
  - `GET /api/feed`
  - `GET /api/notifications`
  - `GET /api/top`
- SSE reliability/scale is covered separately in the design doc.

