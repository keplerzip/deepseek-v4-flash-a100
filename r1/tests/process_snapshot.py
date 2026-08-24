#!/usr/bin/env python3
"""Capture secret-free EngineCore process identities from a shared PID namespace."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def process_record(proc: Path) -> tuple[dict[str, object], bool] | None:
    stat = read_text(proc / "stat")
    if not stat or ") " not in stat:
        return None
    suffix = stat.rsplit(") ", 1)[1].split()
    if len(suffix) <= 19:
        return None
    comm = read_text(proc / "comm").strip()
    cmdline = read_text(proc / "cmdline").replace("\0", " ")
    searchable = f"{comm} {cmdline}".casefold().replace("_", "")
    is_engine_core = "enginecore" in searchable or "vllm::enginecor" in searchable
    is_vllm = is_engine_core or "vllm" in searchable
    if not is_vllm:
        return None
    # Do not emit cmdline: the API key is a server argument and must never enter
    # evidence.  PID, comm, and kernel start ticks are sufficient continuity IDs.
    return (
        {
            "pid": int(proc.name),
            "comm": comm,
            "start_time_ticks": int(suffix[19]),
        },
        is_engine_core,
    )


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    engine_core: list[dict[str, object]] = []
    vllm_processes: list[dict[str, object]] = []
    for proc in sorted(Path("/proc").glob("[0-9]*"), key=lambda item: int(item.name)):
        observed = process_record(proc)
        if observed is None:
            continue
        record, is_engine_core = observed
        vllm_processes.append(record)
        if is_engine_core:
            engine_core.append(record)
    payload: dict[str, object] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "engine_core_processes": engine_core,
        "vllm_processes": vllm_processes,
        "status": "pass" if engine_core else "fail",
    }
    atomic_json(args.output, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0 if engine_core else 1


if __name__ == "__main__":
    raise SystemExit(main())
