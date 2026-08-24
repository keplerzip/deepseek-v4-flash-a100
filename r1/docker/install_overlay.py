#!/usr/bin/env python3
"""Install the reviewed R1 Python overlay after verifying the fixed base."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(path: Path) -> dict[Path, str]:
    entries: dict[Path, str] = {}
    for number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        digest, separator, raw_name = line.partition("  ")
        name = Path(raw_name)
        if (
            not separator
            or len(digest) != 64
            or name.is_absolute()
            or ".." in name.parts
        ):
            raise ValueError(f"invalid manifest entry at {path}:{number}")
        entries[name] = digest
    if not entries:
        raise ValueError(f"manifest is empty: {path}")
    return entries


def discover_package_parent() -> Path:
    spec = importlib.util.find_spec("vllm")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("the base image does not expose an importable vllm package")
    package = Path(next(iter(spec.submodule_search_locations))).resolve()
    if package.name != "vllm":
        raise RuntimeError(f"unexpected vllm package path: {package}")
    return package.parent


def verify(root: Path, entries: dict[Path, str], label: str) -> None:
    errors: list[str] = []
    for relative, expected in entries.items():
        candidate = root / relative
        if not candidate.is_file():
            errors.append(f"{relative}: missing")
            continue
        observed = sha256(candidate)
        if observed != expected:
            errors.append(f"{relative}: expected {expected}, observed {observed}")
    if errors:
        detail = "\n  ".join(errors)
        raise RuntimeError(f"{label} verification failed:\n  {detail}")


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overlay-root", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()

    package_parent = discover_package_parent()
    base_entries = read_manifest(args.base_manifest)
    release_entries = read_manifest(args.release_manifest)
    if set(base_entries) != set(release_entries):
        raise RuntimeError("base and release manifests do not cover the same files")

    verify(package_parent, base_entries, "fixed base")
    for relative in sorted(release_entries):
        source = args.overlay_root / relative
        if not source.is_file():
            raise RuntimeError(f"overlay file is missing: {relative}")
        observed = sha256(source)
        if observed != release_entries[relative]:
            raise RuntimeError(
                f"overlay hash mismatch for {relative}: "
                f"expected {release_entries[relative]}, observed {observed}"
            )
        destination = package_parent / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    verify(package_parent, release_entries, "installed R1")
    args.record.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(
        args.record,
        {
            "installed_at": datetime.now(UTC).isoformat(),
            "package_parent": str(package_parent),
            "python": sys.version.replace("\n", " "),
            "verified_files": len(release_entries),
        },
    )
    print(f"R1_OVERLAY_INSTALL=PASS files={len(release_entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
