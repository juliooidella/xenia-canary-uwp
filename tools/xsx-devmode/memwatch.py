#!/usr/bin/env python3
"""Poll the Xbox Device Portal memory counters while Xenia runs (stdlib only).

Writes one CSV row per sample with the system commit charge
(/api/resourcemanager/systemperf) and the working set of the emulator process
(/api/resourcemanager/processes). The system commit is the counter that fills
up on Series S: the UWP app counter (MemoryManager::AppMemoryUsage, logged by
Xenia as "UWP app memory usage") does not include the reserved guest address
space, so it stays small right up to the `BaseHeap::Alloc failed to alloc
range from host` crash.

Columns: t_s, iso_time, commit_mib, commit_limit_mib, available_mib,
         total_mib, proc_running, proc_ws_mib, cpu_load

Examples:
  memwatch.py --host 192.168.0.50 --user dev --password secret --out runs/boot.csv --duration 300
  memwatch.py ... --out runs/game.csv --until-exit     # stop when xeniacanary.exe exits
  memwatch.py --summary runs/game.csv                   # peak/delta of a saved CSV
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

from devportal import DevicePortal

MIB = 2 ** 20


def sample(dp: DevicePortal, process: str) -> dict:
    perf = dp._json("GET", "/api/resourcemanager/systemperf")
    page = perf.get("PageSize", 4096)
    procs = dp._json("GET", "/api/resourcemanager/processes").get("Processes", [])
    mine = [p for p in procs if (p.get("ImageName") or "").lower() == process.lower()]
    return {
        "commit_mib": perf.get("CommittedPages", 0) * page // MIB,
        "commit_limit_mib": perf.get("CommitLimit", 0) * page // MIB,
        "available_mib": perf.get("AvailablePages", 0) * page // MIB,
        "total_mib": perf.get("TotalPages", 0) * page // MIB,
        "proc_running": int(bool(mine)),
        "proc_ws_mib": sum(p.get("WorkingSetSize", 0) for p in mine) // MIB,
        "cpu_load": perf.get("CpuLoad", 0),
    }


FIELDS = ["t_s", "iso_time", "commit_mib", "commit_limit_mib", "available_mib", "total_mib",
          "proc_running", "proc_ws_mib", "cpu_load"]


def watch(dp: DevicePortal, out: Path, interval: float, duration: float, process: str,
          until_exit: bool, quiet: bool) -> list[dict]:
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    t0 = time.time()
    seen_running = False
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        while True:
            now = time.time()
            try:
                s = sample(dp, process)
            except Exception as e:  # portal hiccup (e.g. console rebooting): keep going
                if not quiet:
                    print(f"[{now - t0:6.0f}s] sample failed: {e}", file=sys.stderr)
                time.sleep(interval)
                if duration and now - t0 > duration:
                    break
                continue
            row = {"t_s": round(now - t0, 1), "iso_time": datetime.now().isoformat(timespec="seconds"), **s}
            rows.append(row)
            w.writerow(row)
            fh.flush()
            if not quiet:
                print(f"[{row['t_s']:6.0f}s] commit {s['commit_mib']}/{s['commit_limit_mib']} MiB "
                      f"avail {s['available_mib']} MiB  {process}: "
                      f"{'running ' + str(s['proc_ws_mib']) + ' MiB' if s['proc_running'] else 'not running'}")
            if s["proc_running"]:
                seen_running = True
            elif until_exit and seen_running:
                break
            if duration and now - t0 > duration:
                break
            time.sleep(interval)
    return rows


def summary(rows: list[dict]) -> str:
    if not rows:
        return "no samples"
    commit = [int(r["commit_mib"]) for r in rows]
    ws = [int(r["proc_ws_mib"]) for r in rows]
    limit = max(int(r["commit_limit_mib"]) for r in rows)
    running = [r for r in rows if int(r["proc_running"])]
    first_run = running[0] if running else None
    lines = [
        f"samples: {len(rows)}  span: {float(rows[-1]['t_s']):.0f}s",
        f"commit: first {commit[0]} MiB, peak {max(commit)} MiB, last {commit[-1]} MiB  (limit {limit} MiB)",
        f"delta first->peak: {max(commit) - commit[0]} MiB",
        f"process working set peak: {max(ws)} MiB",
    ]
    if first_run:
        lines.append(f"process first seen at t={first_run['t_s']}s with commit {first_run['commit_mib']} MiB")
    if running and not int(rows[-1]["proc_running"]):
        last = running[-1]
        lines.append(f"process last seen at t={last['t_s']}s with commit {last['commit_mib']} MiB "
                     f"(headroom {limit - int(last['commit_mib'])} MiB)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host")
    ap.add_argument("--port", type=int, default=11443)
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--out", type=Path, help="CSV to write")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between samples")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = no limit)")
    ap.add_argument("--process", default="xeniacanary.exe", help="image name to track")
    ap.add_argument("--until-exit", action="store_true", help="stop once the process has run and exited")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--summary", type=Path, help="print the summary of an existing CSV and exit")
    args = ap.parse_args(argv)

    if args.summary:
        with args.summary.open(newline="") as fh:
            print(summary(list(csv.DictReader(fh))))
        return 0
    if not (args.host and args.user and args.password and args.out):
        ap.error("--host, --user, --password and --out are required to watch")
    dp = DevicePortal(args.host, args.user, args.password, args.port)
    rows = watch(dp, args.out, args.interval, args.duration, args.process, args.until_exit, args.quiet)
    print(summary(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
