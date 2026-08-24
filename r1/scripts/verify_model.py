#!/usr/bin/env python3
"""Perform cheap, offline structural checks on a DeepSeek V4 checkpoint."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

EXPECTED_VOCAB_SIZE = 129_280


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"expected a JSON object in {path}")
    return value


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    model_dir = args.model_dir.resolve()
    if not model_dir.is_dir():
        raise RuntimeError(f"model directory does not exist: {model_dir}")

    required = ["config.json", "tokenizer.json", "tokenizer_config.json"]
    missing = [name for name in required if not (model_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"checkpoint is missing required files: {missing}")

    config = load_json(model_dir / "config.json")
    vocab_size = config.get("vocab_size")
    if vocab_size != EXPECTED_VOCAB_SIZE:
        raise RuntimeError(
            f"config vocab_size must be {EXPECTED_VOCAB_SIZE}, observed {vocab_size}"
        )

    index_paths = sorted(model_dir.glob("*.safetensors.index.json"))
    referenced: set[str] = set()
    for index_path in index_paths:
        index = load_json(index_path)
        weight_map = index.get("weight_map")
        if not isinstance(weight_map, dict) or not weight_map:
            raise RuntimeError(f"invalid or empty weight_map: {index_path}")
        referenced.update(str(name) for name in weight_map.values())

    if referenced:
        weight_paths = [model_dir / name for name in sorted(referenced)]
    else:
        weight_paths = sorted(model_dir.glob("*.safetensors"))
    missing_weights = [str(path.name) for path in weight_paths if not path.is_file()]
    if missing_weights or not weight_paths:
        raise RuntimeError(
            f"checkpoint weight files are missing: {missing_weights or 'none found'}"
        )

    total_weight_bytes = sum(path.stat().st_size for path in weight_paths)
    if total_weight_bytes <= 0:
        raise RuntimeError("checkpoint weight files are empty")

    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "model_dir": str(model_dir),
        "model_type": config.get("model_type"),
        "architectures": config.get("architectures", []),
        "vocab_size": vocab_size,
        "weight_file_count": len(weight_paths),
        "weight_bytes": total_weight_bytes,
        "status": "pass",
    }
    if args.json_output:
        atomic_write(args.json_output, report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
