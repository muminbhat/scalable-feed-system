## Load test report

- Generated: `2026-02-04T13:28:21Z`
- Rows: **4**

### Summary (Requests/sec)

| endpoint | dataset_events | concurrency | rps | mean ms | across-all ms | ws MB | cpu s |
|---|---:|---:|---:|---:|---:|---:|---:|
| `events` | 100,000 | 20 | 3.29 | 6072.326 | 303.616 |  |  |
| `feed` | 100,000 | 20 | 25.83 | 774.291 | 38.715 |  |  |
| `notifications` | 100,000 | 20 | 25.13 | 795.987 | 39.799 |  |  |
| `top` | 100,000 | 20 | 27.65 | 723.268 | 36.163 |  |  |

### Charts

Open `loadtest/report.html` for interactive charts.
