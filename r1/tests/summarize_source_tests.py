#!/usr/bin/env python3
"""Summarize the required source-test JUnit files without pytest plugins."""

from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SUITES = (
    ("deepseek_v4_parser", "deepseek-v4-parser.xml", False),
    ("parser_engine", "parser-engine.xml", False),
    ("deepseek_v4_tokenizer", "deepseek-v4-tokenizer.xml", False),
    ("deepseek_v4_lifecycle", "deepseek-v4-lifecycle.xml", False),
    ("deepseek_v4_mega_moe", "deepseek-v4-mega-moe.xml", True),
    ("anthropic_conversion", "anthropic-conversion.xml", False),
)


def counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    nodes = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        field: sum(int(node.attrib.get(field, 0)) for node in nodes)
        for field in ("tests", "failures", "errors", "skipped")
    }


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    suites: dict[str, Any] = {}
    status = "pass"
    for name, filename, require_no_skips in SUITES:
        path = args.results_dir / filename
        exit_path = args.results_dir / f"{name}.exit-code"
        if not path.is_file() or not exit_path.is_file():
            suites[name] = {"status": "missing", "junit": filename}
            status = "fail"
            continue
        observed = counts(path)
        exit_code = int(exit_path.read_text().strip())
        passed = (
            exit_code == 0
            and observed["failures"] == 0
            and observed["errors"] == 0
            and (not require_no_skips or observed["skipped"] == 0)
        )
        suites[name] = {
            **observed,
            "exit_code": exit_code,
            "require_no_skips": require_no_skips,
            "status": "pass" if passed else "fail",
            "junit": filename,
        }
        if not passed:
            status = "fail"
    payload = {
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "image_scope": "installed patched R1 vLLM",
        "counting_note": (
            "The focused DeepSeek V4 parser suite is also contained in the full "
            "parser-engine suite and is not double-counted in unique_tests."
        ),
        "test_executions": sum(suite.get("tests", 0) for suite in suites.values()),
        "unique_tests": sum(
            suite.get("tests", 0)
            for name, suite in suites.items()
            if name != "deepseek_v4_parser"
        ),
        "suites": suites,
    }
    atomic_json(args.output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
