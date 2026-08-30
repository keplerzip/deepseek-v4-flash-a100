#!/usr/bin/env python3
"""Verify the installed DeepSeek V4 backend import contract without a GPU."""

from __future__ import annotations

import ast
import json
import site
import sysconfig
from pathlib import Path


def find_vllm_root() -> Path:
    candidates = {
        Path(path) / "vllm"
        for path in (
            *site.getsitepackages(),
            site.getusersitepackages(),
            sysconfig.get_paths()["purelib"],
            sysconfig.get_paths()["platlib"],
        )
        if path
    }
    matches = sorted(path for path in candidates if path.is_dir())
    if len(matches) != 1:
        raise RuntimeError(f"expected one installed vllm package, found: {matches}")
    return matches[0]


def definitions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def imported_sparse_mla_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "vllm.models.deepseek_v4.sparse_mla"
        for alias in node.names
    }


def backend_bases(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    backend = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "DeepseekV4FlashInferMLASparseBackend"
    )
    return {base.id for base in backend.bases if isinstance(base, ast.Name)}


def main() -> None:
    root = find_vllm_root()
    sparse_mla = root / "models/deepseek_v4/sparse_mla.py"
    flashinfer_sparse = root / "models/deepseek_v4/nvidia/flashinfer_sparse.py"
    defined = definitions(sparse_mla)
    imported = imported_sparse_mla_names(flashinfer_sparse)
    missing = sorted(imported - defined)
    if missing:
        raise RuntimeError(f"undefined sparse_mla imports: {missing}")
    bases = backend_bases(flashinfer_sparse)
    if bases != {"DeepseekV4FlashMLABackend"}:
        raise RuntimeError(f"unexpected FlashInfer sparse backend base: {bases}")
    print(
        json.dumps(
            {
                "runtime_source_contract": "pass",
                "vllm_root": str(root),
                "sparse_mla_imports": sorted(imported),
                "backend_bases": sorted(bases),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
