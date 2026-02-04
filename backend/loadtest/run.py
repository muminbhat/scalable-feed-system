from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "app" / "backend"


AB_RPS_RE = re.compile(r"Requests per second:\s+([0-9.]+)")
AB_TPR_RE = re.compile(r"Time per request:\s+([0-9.]+)\s+\[ms\]\s+\(mean\)")
AB_TPR_ACROSS_RE = re.compile(
    r"Time per request:\s+([0-9.]+)\s+\[ms\]\s+\(mean, across all concurrent requests\)"
)
AB_CONN_TOTAL_RE = re.compile(
    r"^Total:\s+(?P<min>\d+)\s+(?P<mean>\d+)\s+(?P<sd>[0-9.]+)\s+(?P<median>\d+)\s+(?P<max>\d+)",
    re.MULTILINE,
)
AB_PERCENTILE_RE = re.compile(r"^\s*(\d+)%\s+(\d+)", re.MULTILINE)


@dataclass
class RunResult:
    timestamp_utc: str
    dataset_events: int
    endpoint: str
    method: str
    url: str
    concurrency: int
    requests: int
    rps: float | None
    time_per_request_ms_mean: float | None
    time_per_request_ms_mean_across: float | None
    conn_total_ms_min: int | None
    conn_total_ms_mean: int | None
    conn_total_ms_median: int | None
    conn_total_ms_max: int | None
    p50_ms: int | None
    p90_ms: int | None
    p95_ms: int | None
    p99_ms: int | None
    cpu_seconds: float | None
    working_set_mb: float | None
    notes: str = ""


def _utc_now() -> str:
    # Python 3.14 deprecates utcnow(); use timezone-aware UTC timestamps.
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _run(cmd: list[str], *, cwd: Path | None = None, timeout_s: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def _get_process_snapshot_windows(pid: int) -> tuple[float | None, float | None]:
    """
    Returns (cpu_seconds, working_set_mb) for a PID on Windows via PowerShell.
    """

    ps = (
        "powershell",
        "-NoProfile",
        "-Command",
        f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
        "if($null -eq $p){ exit 1 } "
        "[Console]::WriteLine(($p.CPU)); "
        "[Console]::WriteLine(($p.WorkingSet64/1MB));",
    )
    cp = _run(list(ps), timeout_s=10)
    if cp.returncode != 0:
        return (None, None)
    lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        return (None, None)
    try:
        return (float(lines[0]), float(lines[1]))
    except ValueError:
        return (None, None)


def parse_ab_output(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    m = AB_RPS_RE.search(text)
    out["rps"] = float(m.group(1)) if m else None

    m = AB_TPR_RE.search(text)
    out["tpr_ms_mean"] = float(m.group(1)) if m else None

    m = AB_TPR_ACROSS_RE.search(text)
    out["tpr_ms_mean_across"] = float(m.group(1)) if m else None

    m = AB_CONN_TOTAL_RE.search(text)
    if m:
        out["conn_total_ms_min"] = int(m.group("min"))
        out["conn_total_ms_mean"] = int(m.group("mean"))
        out["conn_total_ms_median"] = int(m.group("median"))
        out["conn_total_ms_max"] = int(m.group("max"))
    else:
        out["conn_total_ms_min"] = None
        out["conn_total_ms_mean"] = None
        out["conn_total_ms_median"] = None
        out["conn_total_ms_max"] = None

    percentiles = {int(p): int(v) for p, v in AB_PERCENTILE_RE.findall(text)}
    out["p50_ms"] = percentiles.get(50)
    out["p90_ms"] = percentiles.get(90)
    out["p95_ms"] = percentiles.get(95)
    out["p99_ms"] = percentiles.get(99)

    return out


def ensure_ab() -> str:
    # Prefer a self-contained Apache bin/ directory (ab.exe + DLLs).
    # Running a "loose" ab.exe without its sibling DLLs typically fails on Windows
    # with STATUS_DLL_NOT_FOUND (-1073741515) and produces no output.
    apache_bin = ROOT / "loadtest" / "tools" / "httpd" / "Apache24" / "bin" / "ab.exe"
    if apache_bin.exists():
        return str(apache_bin)

    # Fallback: a locally bundled single file (only works if DLL deps are satisfied).
    bundled = ROOT / "loadtest" / "tools" / "ab" / "ab.exe"
    if bundled.exists():
        return str(bundled)

    ab = shutil.which("ab")
    if ab:
        return ab

    raise SystemExit(
        "ApacheBench (ab) not found. Either put `ab` on PATH or download it into `loadtest/tools/ab/ab.exe`."
    )


def _parse_int_list(value: str, *, name: str) -> list[int]:
    """
    Parse comma-separated ints: "100,200" -> [100, 200]
    """
    if not value:
        return []
    out: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError as e:
            raise SystemExit(f"Invalid {name} value: {part!r}") from e
    return out


def _parse_str_set(value: str) -> set[str]:
    if not value:
        return set()
    return {p.strip() for p in value.split(",") if p.strip()}


def seed_dataset(python_exe: str, events: int, hot_user_id: int) -> None:
    cmd = [
        python_exe,
        "manage.py",
        "seed_events",
        "--reset",
        "--events",
        str(events),
        "--hot-user-id",
        str(hot_user_id),
    ]
    cp = _run(cmd, cwd=BACKEND_DIR, timeout_s=60 * 60)
    if cp.returncode != 0:
        raise RuntimeError(f"Seeding failed:\n{cp.stdout}\n{cp.stderr}")


def run_ab(
    ab: str,
    *,
    method: str,
    url: str,
    concurrency: int,
    requests: int,
    headers: list[str],
    body_path: Path | None,
    content_type: str | None,
) -> str:
    cmd = [ab, "-c", str(concurrency), "-n", str(requests)]
    for h in headers:
        cmd += ["-H", h]

    if method.upper() == "POST":
        if not body_path:
            raise ValueError("POST requires body_path")
        cmd += ["-p", str(body_path)]
        cmd += ["-T", content_type or "application/json"]
    cmd.append(url)

    cp = _run(cmd, cwd=ROOT, timeout_s=60 * 30)
    if cp.returncode != 0:
        # ab sometimes returns non-zero for socket errors; keep output for diagnostics.
        return cp.stdout + "\n" + cp.stderr
    return cp.stdout


def write_report(results: list[RunResult], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "results.csv"
    jsonl_path = out_dir / "results.jsonl"
    md_path = out_dir / "report.md"
    html_path = out_dir / "report.html"

    # CSV
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(results[0]).keys()))
        w.writeheader()
        for r in results:
            w.writerow(asdict(r))

    # JSONL
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    # Markdown summary
    by_endpoint: dict[str, list[RunResult]] = {}
    for r in results:
        by_endpoint.setdefault(r.endpoint, []).append(r)

    lines: list[str] = []
    lines.append("## Load test report")
    lines.append("")
    lines.append(f"- Generated: `{_utc_now()}`")
    lines.append(f"- Rows: **{len(results)}**")
    lines.append("")
    lines.append("### Summary (Requests/sec)")
    lines.append("")
    lines.append("| endpoint | dataset_events | concurrency | rps | mean ms | across-all ms | ws MB | cpu s |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        lines.append(
            f"| `{r.endpoint}` | {r.dataset_events:,} | {r.concurrency} | {r.rps or ''} | {r.time_per_request_ms_mean or ''} | {r.time_per_request_ms_mean_across or ''} | {r.working_set_mb or ''} | {r.cpu_seconds or ''} |"
        )
    lines.append("")
    lines.append("### Charts")
    lines.append("")
    lines.append("Open `loadtest/report.html` for interactive charts.")
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")

    # HTML charts (Chart.js from CDN)
    data_json = json.dumps([asdict(r) for r in results], ensure_ascii=False)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Load test report</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 20px; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 24px; max-width: 1100px; }}
    canvas {{ background: #fff; border: 1px solid #eee; }}
    code {{ background: #f6f8fa; padding: 2px 4px; border-radius: 4px; }}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
</head>
<body>
  <h2>Load test report</h2>
  <p>Generated: <code>{_utc_now()}</code></p>
  <div class="grid">
    <div>
      <h3>Requests/sec vs concurrency</h3>
      <canvas id="rpsChart" height="120"></canvas>
    </div>
    <div>
      <h3>Mean time/request (ms) vs concurrency</h3>
      <canvas id="tprChart" height="120"></canvas>
    </div>
  </div>

  <script>
    const rows = {data_json};
    function groupBy(arr, keyFn) {{
      const m = new Map();
      for (const x of arr) {{
        const k = keyFn(x);
        if (!m.has(k)) m.set(k, []);
        m.get(k).push(x);
      }}
      return m;
    }}

    // Create datasets: one line per (endpoint,dataset_events)
    const grouped = groupBy(rows, r => `${{r.endpoint}}|${{r.dataset_events}}`);
    const labels = [...new Set(rows.map(r => r.concurrency))].sort((a,b)=>a-b);

    function mkDatasets(valueKey) {{
      const out = [];
      for (const [k, arr] of grouped.entries()) {{
        arr.sort((a,b)=>a.concurrency-b.concurrency);
        const label = k;
        const map = new Map(arr.map(r => [r.concurrency, r[valueKey]]));
        out.push({{
          label,
          data: labels.map(c => map.get(c) ?? null),
          spanGaps: true,
        }});
      }}
      return out;
    }}

    new Chart(document.getElementById('rpsChart'), {{
      type: 'line',
      data: {{
        labels,
        datasets: mkDatasets('rps'),
      }},
      options: {{
        responsive: true,
        interaction: {{ mode: 'nearest', intersect: false }},
        scales: {{
          x: {{ title: {{ display: true, text: 'concurrency' }} }},
          y: {{ title: {{ display: true, text: 'requests/sec' }} }}
        }}
      }}
    }});

    new Chart(document.getElementById('tprChart'), {{
      type: 'line',
      data: {{
        labels,
        datasets: mkDatasets('time_per_request_ms_mean'),
      }},
      options: {{
        responsive: true,
        interaction: {{ mode: 'nearest', intersect: false }},
        scales: {{
          x: {{ title: {{ display: true, text: 'concurrency' }} }},
          y: {{ title: {{ display: true, text: 'mean ms/request' }} }}
        }}
      }}
    }});
  </script>
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="Example: http://127.0.0.1:8000")
    ap.add_argument("--hot-user-id", type=int, default=2)
    ap.add_argument("--python", default=sys.executable, help="Python exe to run manage.py (fst recommended).")
    ap.add_argument("--server-pid", type=int, default=0, help="PID to sample CPU/memory (optional).")
    ap.add_argument("--out-dir", default=str(ROOT / "loadtest"))
    ap.add_argument(
        "--datasets",
        default="",
        help="Optional comma-separated stored event counts for a smaller run. Example: 100000,300000",
    )
    ap.add_argument(
        "--concurrencies",
        default="",
        help="Optional comma-separated concurrencies for a smaller run. Example: 200,600",
    )
    ap.add_argument(
        "--endpoints",
        default="",
        help="Optional comma-separated endpoints to benchmark: events,feed,notifications,top",
    )
    args = ap.parse_args()

    ab = ensure_ab()
    base_url = args.base_url.rstrip("/")
    out_dir = Path(args.out_dir)

    # Payload file for POST /events
    tmp_dir = out_dir / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    post_body = tmp_dir / "event.json"
    post_body.write_text(
        json.dumps(
            {
                "actor_id": 1,
                "verb": "like",
                "object_type": "post",
                "object_id": "42",
                "target_user_ids": [args.hot_user_id],
            }
        ),
        encoding="utf-8",
    )

    datasets = _parse_int_list(args.datasets, name="datasets") or [100_000, 300_000, 500_000, 700_000, 900_000]
    concurrencies = _parse_int_list(args.concurrencies, name="concurrencies") or [200, 600, 1000, 1400, 1800]
    endpoint_filter = _parse_str_set(args.endpoints)

    endpoints = [
        ("events", "POST", f"{base_url}/api/events"),
        ("feed", "GET", f"{base_url}/api/feed?user_id={args.hot_user_id}&limit=50"),
        ("notifications", "GET", f"{base_url}/api/notifications?user_id={args.hot_user_id}&since=0&limit=50"),
        ("top", "GET", f"{base_url}/api/top?window=1m"),
    ]
    if endpoint_filter:
        unknown = endpoint_filter - {e[0] for e in endpoints}
        if unknown:
            raise SystemExit(f"Unknown endpoints: {', '.join(sorted(unknown))}")
        endpoints = [e for e in endpoints if e[0] in endpoint_filter]

    results: list[RunResult] = []

    for events in datasets:
        seed_dataset(args.python, events, args.hot_user_id)

        for endpoint_name, method, url in endpoints:
            for c in concurrencies:
                n = 10 * c

                headers = [
                    f"X-User-Id: {args.hot_user_id if endpoint_name != 'events' else 1}",
                    "Accept: application/json",
                ]

                body_path = post_body if method == "POST" else None
                content_type = "application/json" if method == "POST" else None

                cpu_before = ws_before = None
                cpu_after = ws_after = None
                if args.server_pid:
                    cpu_before, ws_before = _get_process_snapshot_windows(args.server_pid)

                output = run_ab(
                    ab,
                    method=method,
                    url=url,
                    concurrency=c,
                    requests=n,
                    headers=headers,
                    body_path=body_path,
                    content_type=content_type,
                )
                parsed = parse_ab_output(output)

                if args.server_pid:
                    cpu_after, ws_after = _get_process_snapshot_windows(args.server_pid)

                cpu_seconds = None
                working_set_mb = None
                if cpu_before is not None and cpu_after is not None:
                    cpu_seconds = max(0.0, cpu_after - cpu_before)
                if ws_after is not None:
                    working_set_mb = ws_after

                results.append(
                    RunResult(
                        timestamp_utc=_utc_now(),
                        dataset_events=events,
                        endpoint=endpoint_name,
                        method=method,
                        url=url,
                        concurrency=c,
                        requests=n,
                        rps=parsed["rps"],
                        time_per_request_ms_mean=parsed["tpr_ms_mean"],
                        time_per_request_ms_mean_across=parsed["tpr_ms_mean_across"],
                        conn_total_ms_min=parsed["conn_total_ms_min"],
                        conn_total_ms_mean=parsed["conn_total_ms_mean"],
                        conn_total_ms_median=parsed["conn_total_ms_median"],
                        conn_total_ms_max=parsed["conn_total_ms_max"],
                        p50_ms=parsed["p50_ms"],
                        p90_ms=parsed["p90_ms"],
                        p95_ms=parsed["p95_ms"],
                        p99_ms=parsed["p99_ms"],
                        cpu_seconds=cpu_seconds,
                        working_set_mb=working_set_mb,
                        notes="",
                    )
                )

                print(
                    f"[{events:,}] {endpoint_name} c={c} n={n} rps={parsed['rps']} mean_ms={parsed['tpr_ms_mean']}"
                )

    if not results:
        raise SystemExit("No results produced.")

    write_report(results, out_dir)
    print(f"Wrote: {out_dir / 'results.csv'}")
    print(f"Wrote: {out_dir / 'report.md'}")
    print(f"Wrote: {out_dir / 'report.html'}")


if __name__ == "__main__":
    main()

