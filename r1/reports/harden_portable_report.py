#!/usr/bin/env python3
"""Apply the narrow overflow fix required by long portable report pages."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

MARKER = 'data-dsv4-portable-overflow-fix="true"'
STYLE = """<style data-dsv4-portable-overflow-fix="true">
.analytics-top-bar {
  width: 100% !important;
  margin-right: 0 !important;
  margin-left: 0 !important;
}
</style>
"""


def harden(document: str) -> str:
    if MARKER in document:
        return document
    if document.count("</head>") != 1:
        raise RuntimeError("portable HTML must contain exactly one closing head tag")
    return document.replace("</head>", STYLE + "</head>", 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    document = harden(args.input.read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(document)
    temporary.replace(args.output)
    print(f"PORTABLE_OVERFLOW_FIX=PASS output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
