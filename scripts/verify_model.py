#!/usr/bin/env python3
"""Verify a DeepSeek-V4 checkpoint without loading tensor payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any, Iterable


EXPECTED_SHARDS = 48
EXPECTED_ARCHITECTURES = {"DeepseekV4ForCausalLM", "DeepSeekV4ForCausalLM"}
EXPECTED_MODEL_TYPES = {"deepseek_v4", "deepseek-v4"}


class VerificationError(RuntimeError):
    pass


def human_bytes(value: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{value} B"


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"top-level JSON must be an object: {path}")
    return value


def expected_shards() -> list[str]:
    return [
        f"model-{index:05d}-of-{EXPECTED_SHARDS:05d}.safetensors"
        for index in range(1, EXPECTED_SHARDS + 1)
    ]


def read_safetensors_header(path: Path) -> tuple[dict[str, Any], int]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw_length = handle.read(8)
        if len(raw_length) != 8:
            raise VerificationError(f"truncated safetensors prefix: {path}")
        header_length = struct.unpack("<Q", raw_length)[0]
        if header_length <= 1 or header_length > min(size - 8, 1024**3):
            raise VerificationError(
                f"invalid safetensors header length {header_length}: {path}"
            )
        raw_header = handle.read(header_length)
        if len(raw_header) != header_length:
            raise VerificationError(f"truncated safetensors header: {path}")
    try:
        header = json.loads(raw_header)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"invalid safetensors header JSON: {path}: {exc}") from exc
    if not isinstance(header, dict):
        raise VerificationError(f"safetensors header is not an object: {path}")

    payload_size = size - 8 - header_length
    for key, descriptor in header.items():
        if key == "__metadata__":
            continue
        if not isinstance(descriptor, dict):
            raise VerificationError(f"invalid descriptor for {key!r} in {path.name}")
        offsets = descriptor.get("data_offsets")
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or not all(isinstance(item, int) for item in offsets)
            or offsets[0] < 0
            or offsets[0] > offsets[1]
            or offsets[1] > payload_size
        ):
            raise VerificationError(f"invalid data_offsets for {key!r} in {path.name}")
    return header, header_length


def directory_size(root: Path) -> tuple[int, int]:
    total = 0
    files = 0
    for current, _, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in names:
            path = current_path / name
            try:
                total += path.lstat().st_size
                files += 1
            except OSError as exc:
                raise VerificationError(f"cannot stat {path}: {exc}") from exc
    return total, files


def checksum_files(model_dir: Path, shards: Iterable[str]) -> list[Path]:
    fixed = [
        model_dir / "config.json",
        model_dir / "generation_config.json",
        model_dir / "model.safetensors.index.json",
    ]
    files = [path for path in fixed if path.is_file()]
    files.extend(model_dir / name for name in shards)
    for directory_name in ("encoding", "inference"):
        directory = model_dir / directory_name
        if directory.is_dir():
            files.extend(path for path in directory.rglob("*") if path.is_file())
    return sorted(set(files), key=lambda path: path.relative_to(model_dir).as_posix())


def load_partial_state(path: Path) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return state
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                item = json.loads(line)
                state[item["path"]] = item
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise VerificationError(
                    f"invalid resumable checksum state at {path}:{line_number}: {exc}"
                ) from exc
    return state


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(16 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_checksums(model_dir: Path, files: list[Path], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial.jsonl")
    state = load_partial_state(partial)
    records: list[dict[str, Any]] = []
    for index, path in enumerate(files, 1):
        relative = path.relative_to(model_dir).as_posix()
        stat = path.stat()
        prior = state.get(relative)
        if (
            prior
            and prior.get("size") == stat.st_size
            and prior.get("mtime_ns") == stat.st_mtime_ns
            and isinstance(prior.get("sha256"), str)
        ):
            record = prior
            status = "resume"
        else:
            started = time.monotonic()
            record = {
                "path": relative,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256_file(path),
            }
            status = f"hashed in {time.monotonic() - started:.1f}s"
            with partial.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        records.append(record)
        print(f"SHA256 [{index}/{len(files)}] {relative}: {status}", file=sys.stderr)

    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(f"{record['sha256']}  {record['path']}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, output)
    partial.unlink(missing_ok=True)
    print(f"SHA256_MANIFEST={output}")


def verify(model_dir: Path) -> tuple[list[str], dict[str, Any]]:
    failures: list[str] = []
    warnings: list[str] = []
    required = [
        "config.json",
        "generation_config.json",
        "model.safetensors.index.json",
    ]
    for name in required:
        if not (model_dir / name).is_file():
            failures.append(f"missing required file: {name}")
    for name in ("encoding", "inference"):
        if not (model_dir / name).is_dir():
            failures.append(f"missing required directory: {name}/")

    shards = expected_shards()
    missing_shards = [name for name in shards if not (model_dir / name).is_file()]
    if missing_shards:
        failures.append(
            f"missing {len(missing_shards)} of {EXPECTED_SHARDS} expected shards: "
            + ", ".join(missing_shards)
        )

    config: dict[str, Any] = {}
    if (model_dir / "config.json").is_file():
        try:
            config = read_json(model_dir / "config.json")
            architectures = config.get("architectures", [])
            model_type = config.get("model_type")
            if not isinstance(architectures, list) or not (
                set(architectures) & EXPECTED_ARCHITECTURES
            ):
                failures.append(f"unexpected architectures: {architectures!r}")
            if model_type not in EXPECTED_MODEL_TYPES:
                failures.append(f"unexpected model_type: {model_type!r}")
        except VerificationError as exc:
            failures.append(str(exc))

    index_references: set[str] = set()
    weight_map_keys = 0
    if (model_dir / "model.safetensors.index.json").is_file():
        try:
            index = read_json(model_dir / "model.safetensors.index.json")
            weight_map = index.get("weight_map")
            if not isinstance(weight_map, dict) or not weight_map:
                failures.append("index weight_map is missing or empty")
            else:
                weight_map_keys = len(weight_map)
                index_references = {
                    value for value in weight_map.values() if isinstance(value, str)
                }
                bad_values = len(weight_map) - sum(
                    isinstance(value, str) for value in weight_map.values()
                )
                if bad_values:
                    failures.append(f"index contains {bad_values} non-string shard values")
                missing_refs = sorted(
                    name for name in index_references if not (model_dir / name).is_file()
                )
                if missing_refs:
                    failures.append(
                        "index references missing shard files: " + ", ".join(missing_refs)
                    )
                unexpected_refs = sorted(index_references - set(shards))
                if unexpected_refs:
                    failures.append(
                        "index references unexpected shard names: "
                        + ", ".join(unexpected_refs)
                    )
                unreferenced = sorted(set(shards) - index_references)
                if unreferenced:
                    failures.append(
                        "expected shards not referenced by index: " + ", ".join(unreferenced)
                    )
        except VerificationError as exc:
            failures.append(str(exc))

    tensor_count = 0
    dspark_keys: list[str] = []
    headers_checked = 0
    for name in shards:
        path = model_dir / name
        if not path.is_file():
            continue
        try:
            header, _ = read_safetensors_header(path)
            headers_checked += 1
            tensor_keys = [key for key in header if key != "__metadata__"]
            tensor_count += len(tensor_keys)
            for key in tensor_keys:
                lowered = key.lower()
                if (
                    lowered.startswith("mtp.")
                    or ".mtp." in lowered
                    or "dspark" in lowered
                    or "speculative" in lowered
                ) and len(dspark_keys) < 20:
                    dspark_keys.append(key)
        except (OSError, VerificationError) as exc:
            failures.append(str(exc))

    if headers_checked != EXPECTED_SHARDS:
        failures.append(
            f"read metadata from {headers_checked}/{EXPECTED_SHARDS} safetensors shards"
        )
    if not dspark_keys:
        failures.append(
            "no mtp.*, dspark, or speculative-module keys found in safetensors metadata"
        )
    if weight_map_keys and tensor_count != weight_map_keys:
        warnings.append(
            f"header tensor count ({tensor_count}) differs from index weight_map "
            f"count ({weight_map_keys}); inspect aliases or index metadata"
        )

    try:
        total_size, total_files = directory_size(model_dir)
    except VerificationError as exc:
        failures.append(str(exc))
        total_size, total_files = 0, 0

    details = {
        "model_dir": str(model_dir),
        "architectures": config.get("architectures"),
        "model_type": config.get("model_type"),
        "shards_expected": EXPECTED_SHARDS,
        "shards_metadata_checked": headers_checked,
        "index_referenced_shards": len(index_references),
        "index_weight_keys": weight_map_keys,
        "header_tensor_keys": tensor_count,
        "dspark_key_examples": dspark_keys,
        "directory_files": total_files,
        "directory_bytes": total_size,
        "directory_human": human_bytes(total_size),
        "warnings": warnings,
        "failures": failures,
    }
    return shards, details


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "model_dir",
        nargs="?",
        default=os.environ.get("MODEL_DIR"),
        help="checkpoint directory (or set MODEL_DIR)",
    )
    parser.add_argument(
        "--json-output", type=Path, help="also write the verification result as JSON"
    )
    parser.add_argument(
        "--sha256-output",
        type=Path,
        help="hash model files into a resumable GNU sha256sum manifest",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.model_dir:
        print("ERROR: MODEL_DIR is not set and no model directory was passed", file=sys.stderr)
        return 2
    model_dir = Path(args.model_dir).expanduser().resolve()
    if not model_dir.is_dir():
        print(f"ERROR: model directory does not exist: {model_dir}", file=sys.stderr)
        return 2

    shards, details = verify(model_dir)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(details, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    print(json.dumps(details, indent=2, ensure_ascii=False))
    if details["failures"]:
        print("FILE_INTEGRITY=FAIL")
        print("INFERENCE_CORRECTNESS=NOT_TESTED")
        return 1

    print("FILE_INTEGRITY=PASS")
    print("DSPARK_WEIGHTS=PASS")
    print("INFERENCE_CORRECTNESS=NOT_TESTED")
    if args.sha256_output:
        write_checksums(
            model_dir,
            checksum_files(model_dir, shards),
            args.sha256_output.expanduser().resolve(),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
