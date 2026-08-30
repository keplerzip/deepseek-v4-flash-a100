#!/usr/bin/env python3
"""Verify the installed DeepSeek V4 backend import contract without a GPU."""

from __future__ import annotations

import ast
import json
import site
import sysconfig
from pathlib import Path


STARTUP_MODULES = (
    "vllm.v1.worker.gpu_worker",
    "vllm.model_executor.warmup.kernel_warmup",
    "vllm.model_executor.warmup.flashinfer_sparse_mla_warmup",
    "vllm.v1.worker.gpu.warmup",
    "vllm.v1.worker.gpu.model_runner",
    "vllm.models.deepseek_v4.nvidia.flashinfer_sparse",
    "vllm.models.deepseek_v4.nvidia.model",
)


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


def module_path(root: Path, module: str) -> Path | None:
    parts = module.split(".")
    if not parts or parts[0] != "vllm":
        return None
    relative = Path(*parts[1:])
    module_file = (root / relative).with_suffix(".py")
    if module_file.is_file():
        return module_file
    package_file = root / relative / "__init__.py"
    return package_file if package_file.is_file() else None


def module_symbols(path: Path) -> tuple[set[str], bool]:
    tree = ast.parse(path.read_text(), filename=str(path))
    symbols: set[str] = set()
    wildcard_import = False

    def collect(nodes: list[ast.stmt]) -> None:
        nonlocal wildcard_import
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                symbols.update(
                    target.id for target in targets if isinstance(target, ast.Name)
                )
                if any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in targets
                ) and isinstance(node.value, (ast.List, ast.Tuple)):
                    symbols.update(
                        item.value
                        for item in node.value.elts
                        if isinstance(item, ast.Constant)
                        and isinstance(item.value, str)
                    )
            elif isinstance(node, ast.Import):
                symbols.update(
                    alias.asname or alias.name.split(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                wildcard_import |= any(alias.name == "*" for alias in node.names)
                symbols.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name != "*"
                )
            elif isinstance(node, ast.If):
                collect(node.body)
                collect(node.orelse)
            elif isinstance(node, ast.Try):
                collect(node.body)
                collect(node.orelse)
                collect(node.finalbody)
                for handler in node.handlers:
                    collect(handler.body)

    collect(tree.body)
    return symbols, wildcard_import


def local_import_contracts(path: Path) -> list[tuple[str, set[str]]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        (node.module, {alias.name for alias in node.names})
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module is not None
        and node.module.startswith("vllm.")
        and all(alias.name != "*" for alias in node.names)
    ]


def audit_startup_imports(root: Path) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for source_module in STARTUP_MODULES:
        source_path = module_path(root, source_module)
        if source_path is None:
            missing[source_module] = ["<module missing>"]
            continue
        for target_module, imported in local_import_contracts(source_path):
            target_path = module_path(root, target_module)
            if target_path is None:
                continue
            declared, has_wildcard_import = module_symbols(target_path)
            if has_wildcard_import:
                continue
            undeclared = {
                name
                for name in imported - declared
                if module_path(root, f"{target_module}.{name}") is None
            }
            if undeclared:
                key = f"{source_module} -> {target_module}"
                missing[key] = sorted(undeclared)
    return missing


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


def verify_responses_limit_flow(path: Path) -> None:
    tree = ast.parse(path.read_text(), filename=str(path))
    serving_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "OpenAIServingResponses"
    )
    methods = {
        node.name: node
        for node in serving_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    generate = methods["_generate_with_builtin_tools"]
    generate_args = {argument.arg for argument in generate.args.args}
    if "max_model_len" not in generate_args:
        raise RuntimeError("Responses generation does not accept an explicit model limit")
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_get_max_model_len"
        for node in ast.walk(generate)
    ):
        raise RuntimeError("Responses generation re-resolves a model limit from context")

    create = methods["_create_responses"]
    generate_calls = [
        node
        for node in ast.walk(create)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_generate_with_builtin_tools"
    ]
    if len(generate_calls) != 1:
        raise RuntimeError(
            f"expected one Responses generation call, found {len(generate_calls)}"
        )
    max_len_keywords = [
        keyword.value
        for keyword in generate_calls[0].keywords
        if keyword.arg == "max_model_len"
    ]
    if len(max_len_keywords) != 1 or not isinstance(max_len_keywords[0], ast.Name):
        raise RuntimeError("Responses generation is not passed the resolved model limit")
    if max_len_keywords[0].id != "max_model_len":
        raise RuntimeError("Responses generation receives an unexpected limit value")


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
    startup_missing = audit_startup_imports(root)
    if startup_missing:
        raise RuntimeError(f"undefined GPU worker startup imports: {startup_missing}")
    verify_responses_limit_flow(root / "entrypoints/openai/responses/serving.py")
    print(
        json.dumps(
            {
                "runtime_source_contract": "pass",
                "vllm_root": str(root),
                "sparse_mla_imports": sorted(imported),
                "backend_bases": sorted(bases),
                "startup_import_contracts": "pass",
                "startup_modules": list(STARTUP_MODULES),
                "responses_limit_flow": "pass",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
