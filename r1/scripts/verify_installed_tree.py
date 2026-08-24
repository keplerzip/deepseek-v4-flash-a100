#!/usr/bin/env python3
"""Verify selected files in an installed vLLM package against a manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_parent() -> Path:
    spec = importlib.util.find_spec("vllm")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("vllm is not importable")
    package = Path(next(iter(spec.submodule_search_locations))).resolve()
    if package.name != "vllm":
        raise RuntimeError(f"unexpected vllm package path: {package}")
    return package.parent


def manifest_entries(path: Path) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        digest, separator, raw_name = line.partition("  ")
        relative = Path(raw_name)
        if (
            not separator
            or len(digest) != 64
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise ValueError(f"invalid manifest line: {line!r}")
        result.append((digest, relative))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--package-parent", type=Path)
    args = parser.parse_args()

    root = args.package_parent.resolve() if args.package_parent else package_parent()
    failures: list[str] = []
    entries = manifest_entries(args.manifest)
    for expected, relative in entries:
        target = root / relative
        if not target.is_file():
            failures.append(f"{relative}: missing")
            continue
        observed = sha256(target)
        if observed != expected:
            failures.append(f"{relative}: expected {expected}, observed {observed}")
    if failures:
        raise RuntimeError("installed tree mismatch:\n  " + "\n  ".join(failures))
    print(f"INSTALLED_TREE_VERIFY=PASS files={len(entries)} root={root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
