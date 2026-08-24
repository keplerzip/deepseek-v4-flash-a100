#!/usr/bin/env python3
"""Merge container and EngineCore continuity evidence into a live-test summary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def identities(snapshot: dict[str, Any]) -> set[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for process in snapshot.get("engine_core_processes", []):
        result.add((int(process["pid"]), int(process["start_time_ticks"])))
    return result


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--process-before", type=Path, required=True)
    parser.add_argument("--process-after", type=Path, required=True)
    parser.add_argument("--container-before", type=Path, required=True)
    parser.add_argument("--container-after", type=Path, required=True)
    parser.add_argument("--harness-exit", type=int, required=True)
    args = parser.parse_args()

    harness_summary_present = args.summary.is_file()
    summary = (
        load(args.summary)
        if harness_summary_present
        else {
            "status": "fail",
            "failure_category": "harness_summary_missing",
            "failure_detail": "The live harness did not produce its summary.",
        }
    )
    process_before = load(args.process_before)
    process_after = load(args.process_after)
    container_before = load(args.container_before)
    container_after = load(args.container_after)

    before_ids = identities(process_before)
    after_ids = identities(process_after)
    container_id = container_before.get("id")
    container_same = (
        bool(container_id)
        and container_id == container_after.get("id")
        and container_before.get("started_at") == container_after.get("started_at")
        and container_before.get("status") == "running"
        and container_after.get("status") == "running"
    )
    restart_delta = int(container_after.get("restart_count", 0)) - int(
        container_before.get("restart_count", 0)
    )
    engine_same = bool(before_ids) and before_ids == after_ids
    oom_free = not bool(container_before.get("oom_killed")) and not bool(
        container_after.get("oom_killed")
    )
    continuity_pass = container_same and restart_delta == 0 and engine_same and oom_free
    detected_restarts = 0 if continuity_pass else max(1, restart_delta)
    initial_status = summary.get("status")
    final_status = (
        "pass"
        if initial_status == "pass" and args.harness_exit == 0 and continuity_pass
        else "fail"
    )
    continuity = {
        "status": "pass" if continuity_pass else "fail",
        "container_same": container_same,
        "container_restart_delta": restart_delta,
        "engine_core_identity_same": engine_same,
        "engine_core_before": sorted(before_ids),
        "engine_core_after": sorted(after_ids),
        "oom_free": oom_free,
        "engine_core_restart_count": detected_restarts,
    }
    summary.update(
        {
            "status": final_status,
            "runtime_continuity_status": continuity["status"],
            "engine_core_restart_count": detected_restarts,
            "runtime_continuity_evidence": "runtime-continuity.json",
            "harness_exit_code": args.harness_exit,
            "harness_summary_present": harness_summary_present,
        }
    )
    atomic_json(args.summary, summary)
    if final_status != "pass":
        os.chmod(args.summary, 0o600)
    atomic_json(args.summary.parent / "runtime-continuity.json", continuity)
    print(json.dumps(continuity, indent=2, sort_keys=True))
    return 0 if final_status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
