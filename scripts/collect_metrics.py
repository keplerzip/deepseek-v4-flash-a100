#!/usr/bin/env python3
"""Poll NVIDIA GPU telemetry into a CSV until a stop file appears."""

from __future__ import annotations

import argparse
import csv
import signal
import subprocess
import time
from pathlib import Path


QUERY_FIELDS = [
    "index",
    "timestamp",
    "memory.used",
    "memory.total",
    "utilization.gpu",
    "utilization.memory",
    "power.draw",
    "power.limit",
    "temperature.gpu",
    "clocks.sm",
    "clocks.mem",
    "pcie.link.gen.current",
    "pcie.link.width.current",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--stop-file", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    started = time.monotonic()
    failures = 0
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_utc", *QUERY_FIELDS, "collector_error"])
        while running:
            if args.stop_file and args.stop_file.exists():
                break
            if args.duration and time.monotonic() - started >= args.duration:
                break
            sampled = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            command = [
                "nvidia-smi",
                "--query-gpu=" + ",".join(QUERY_FIELDS),
                "--format=csv,noheader,nounits",
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=max(10.0, args.interval * 5),
                )
                for line in completed.stdout.splitlines():
                    values = [item.strip() for item in line.split(",")]
                    writer.writerow([sampled, *values, ""])
                failures = 0
            except (OSError, subprocess.SubprocessError) as exc:
                failures += 1
                writer.writerow([sampled, *([""] * len(QUERY_FIELDS)), str(exc)])
                if failures >= 10:
                    handle.flush()
                    return 2
            handle.flush()
            time.sleep(max(0.1, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
