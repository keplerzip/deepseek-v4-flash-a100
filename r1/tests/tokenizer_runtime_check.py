#!/usr/bin/env python3
"""Load the packaged tokenizer and assert the guided-decoding invariant."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vllm.tokenizers import get_tokenizer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_dir")
    parser.add_argument("--fixed-base-rollback", action="store_true")
    args = parser.parse_args()

    model_dir = Path(args.model_dir)
    config = json.loads((model_dir / "config.json").read_text())
    config_vocab_size = config.get("vocab_size")
    if not isinstance(config_vocab_size, int):
        raise RuntimeError("config.json omitted an integer vocab_size")

    tokenizer = get_tokenizer(
        str(model_dir),
        tokenizer_mode="deepseek_v4",
        trust_remote_code=True,
        local_files_only=True,
    )
    observed = len(tokenizer)
    result = {
        "tokenizer_class": type(tokenizer).__name__,
        "tokenizer_length": observed,
        "config_vocab_size": config_vocab_size,
        "expected_config_vocab_size": 129_280,
        "expected_tokenizer_length": (129_283 if args.fixed_base_rollback else 129_280),
        "mode": "fixed-base-rollback" if args.fixed_base_rollback else "r1",
    }
    print(json.dumps(result, sort_keys=True))
    if args.fixed_base_rollback:
        if config_vocab_size != 129_280 or observed != 129_283:
            raise RuntimeError(
                "fixed base no longer has its locked tokenizer signature: "
                f"len(tokenizer)={observed} config.vocab_size={config_vocab_size}"
            )
        return 0
    if observed != 129_280:
        raise RuntimeError(f"tokenizer length mismatch: {observed} != 129280")
    if observed != config_vocab_size:
        raise RuntimeError(
            "tokenizer/config vocab mismatch: "
            f"len(tokenizer)={observed} config.vocab_size={config_vocab_size}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
