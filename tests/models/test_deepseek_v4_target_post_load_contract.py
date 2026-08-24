# SPDX-License-Identifier: Apache-2.0
"""CPU-only contracts for DeepSeek V4 target post-load finalization.

These tests intentionally inspect the target implementation without importing
CUDA modules, so they run in source/package validation before an A100 is
available. Runtime A100 tests still verify the resulting packed weights.
"""

from __future__ import annotations

import ast
from importlib.metadata import distribution
from pathlib import Path

_REPOSITORY_MODEL_PATH = (
    Path(__file__).parents[2] / "vllm/models/deepseek_v4/nvidia/model.py"
)
_MODEL_PATH = (
    _REPOSITORY_MODEL_PATH
    if _REPOSITORY_MODEL_PATH.is_file()
    else Path(
        distribution("vllm").locate_file("vllm/models/deepseek_v4/nvidia/model.py")
    )
)


def _class_method(
    tree: ast.Module, class_name: str, method_name: str
) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"missing {class_name}.{method_name}")


def _self_call_names(method: ast.FunctionDef) -> list[str]:
    names: list[str] = []
    for node in ast.walk(method):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        names.append(ast.unparse(node.func))
    return names


def test_megamoe_forward_never_repackages_weights():
    tree = ast.parse(_MODEL_PATH.read_text(encoding="utf-8"))
    forward = _class_method(tree, "DeepseekV4MegaMoEExperts", "forward")

    assert "self.finalize_weights" not in _self_call_names(forward)


def test_target_model_post_load_finalizes_both_weight_families():
    tree = ast.parse(_MODEL_PATH.read_text(encoding="utf-8"))
    hook = _class_method(tree, "DeepseekV4ForCausalLM", "process_weights_after_loading")
    calls = _self_call_names(hook)

    assert "self.model.finalize_mega_moe_weights" in calls
    assert "self.model.finalize_mhc_broadcast_weights" in calls
