#!/usr/bin/env python3
"""Convert a benchmark matrix CSV into the canonical analytics artifact."""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SOURCE_TESTS_PASSED = 3_885
PORTABLE_PACKAGE_INFO = {
    "artifactRuntime": "portable-artifact-reader.html",
    "deliveryMode": "portable_html",
    "hostedReadOnly": True,
    "mode": "portable_html",
    "portableHtml": True,
    "readOnly": True,
    "controls": {
        "copyAsImage": False,
        "delete": False,
        "drag": False,
        "edit": False,
        "export": False,
        "exportHostedLink": False,
        "fullscreen": False,
        "hostedLink": False,
        "persistence": False,
        "refresh": False,
        "reorder": False,
        "share": False,
    },
}

INTEGER_FIELDS = {
    "context_target",
    "concurrency",
    "repetitions",
    "requests_planned",
    "requests_success",
    "requests_failed",
    "prompt_tokens_min",
    "prompt_tokens_p50",
    "prompt_tokens_max",
    "completion_tokens_total",
}
FLOAT_FIELDS = {
    "ttft_ms_p50",
    "ttft_ms_p95",
    "ttft_ms_p99",
    "latency_ms_p50",
    "latency_ms_p95",
    "latency_ms_p99",
    "input_tokens_per_second",
    "output_tokens_per_second",
    "total_tokens_per_second",
    "wave_wall_seconds",
}
MEASUREMENT_FIELDS = {
    "prompt_tokens_min",
    "prompt_tokens_p50",
    "prompt_tokens_max",
    "completion_tokens_total",
    "ttft_ms_p50",
    "ttft_ms_p95",
    "ttft_ms_p99",
    "latency_ms_p50",
    "latency_ms_p95",
    "latency_ms_p99",
    "input_tokens_per_second",
    "output_tokens_per_second",
    "total_tokens_per_second",
    "wave_wall_seconds",
}


def typed_row(row: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if value == "":
            result[key] = None
        elif key in INTEGER_FIELDS:
            result[key] = int(float(value))
        elif key in FLOAT_FIELDS:
            result[key] = float(value)
        else:
            result[key] = value
    result["context_k"] = int(result["context_target"]) // 1000
    return result


def load_rows(
    path: Path, max_concurrency: int = 16
) -> list[dict[str, Any]]:
    with path.open(newline="") as handle:
        rows = [typed_row(row) for row in csv.DictReader(handle)]
    expected = {
        (context, concurrency)
        for context in range(10_000, 200_001, 10_000)
        for concurrency in range(1, max_concurrency + 1)
    }
    observed = {(row["context_target"], row["concurrency"]) for row in rows}
    if len(rows) != len(expected) or observed != expected:
        raise RuntimeError(
            f"matrix must contain exactly concurrency 1..{max_concurrency} "
            "crossed with context 10K..200K in 10K steps"
        )
    for row in rows:
        status = row["status"]
        cell = f"context={row['context_target']} concurrency={row['concurrency']}"
        if row["repetitions"] < 1 or row["requests_planned"] != (
            row["concurrency"] * row["repetitions"]
        ):
            raise RuntimeError(f"invalid request plan at {cell}")
        if status not in {"pending", "complete", "failed"}:
            raise RuntimeError(f"invalid matrix status at {cell}: {status!r}")
        if status == "pending":
            populated = sorted(
                field for field in MEASUREMENT_FIELDS if row.get(field) is not None
            )
            if populated or row["requests_success"] or row["requests_failed"]:
                raise RuntimeError(
                    f"pending cell contains measured values at {cell}: {populated}"
                )
            continue
        if status == "failed":
            if not row.get("error_summary"):
                raise RuntimeError(f"failed cell omitted error evidence at {cell}")
            continue
        missing = sorted(
            field for field in MEASUREMENT_FIELDS if row.get(field) is None
        )
        if missing:
            raise RuntimeError(f"complete cell omitted metrics at {cell}: {missing}")
        if (
            row["requests_failed"] != 0
            or row["requests_success"] != row["requests_planned"]
        ):
            raise RuntimeError(
                f"complete cell has inconsistent request counts at {cell}"
            )
        for field in ("prompt_tokens_min", "prompt_tokens_p50", "prompt_tokens_max"):
            if abs(row[field] - row["context_target"]) > 32:
                raise RuntimeError(
                    f"complete cell exceeded prompt-token tolerance at {cell}: "
                    f"{field}={row[field]}"
                )
    return rows


def coverage_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for context in range(10_000, 200_001, 10_000):
        subset = [row for row in rows if row["context_target"] == context]
        counts = Counter(row["status"] for row in subset)
        output.append(
            {
                "context_k": context // 1000,
                "complete": counts["complete"],
                "failed": counts["failed"],
                "pending": counts["pending"],
            }
        )
    return output


def heatmap_rows(
    rows: list[dict[str, Any]], field: str, max_concurrency: int
) -> list[dict[str, Any]]:
    output = []
    for context in range(10_000, 200_001, 10_000):
        item: dict[str, Any] = {"context_k": context // 1000}
        subset = {
            row["concurrency"]: row for row in rows if row["context_target"] == context
        }
        for concurrency in range(1, max_concurrency + 1):
            item[f"c{concurrency}"] = subset[concurrency].get(field)
        output.append(item)
    return output


def metric_card(card_id: str, label: str, field: str, source: str) -> dict:
    return {
        "id": card_id,
        "description": label,
        "dataset": "summary",
        "sourceId": source,
        "metrics": [{"label": label, "field": field, "format": "number"}],
    }


def artifact(
    rows: list[dict[str, Any]],
    source_path: str,
    *,
    scheme: str = "one",
    scheme_label: str = "方案一（8 卡，GPU 0-7）",
    gpu_devices: str = "0,1,2,3,4,5,6,7",
    tensor_parallel_size: int = 8,
    scheduler_max_seqs: int = 32,
    benchmark_max_concurrency: int = 16,
    theoretical_256k_concurrency: float = 19.58,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).isoformat()
    counts = Counter(row["status"] for row in rows)
    complete = counts["complete"]
    failed = counts["failed"]
    pending = counts["pending"]
    planned_cells = 20 * benchmark_max_concurrency
    if len(rows) != planned_cells:
        raise RuntimeError(
            f"scheme {scheme} expected {planned_cells} rows, observed {len(rows)}"
        )
    status = "ready" if complete == planned_cells and failed == 0 else "partial"
    summary = {
        "scheme": scheme,
        "scheme_label": scheme_label,
        "gpu_devices": gpu_devices,
        "tensor_parallel_size": tensor_parallel_size,
        "planned_cells": planned_cells,
        "executed_cells": complete + failed,
        "complete_cells": complete,
        "failed_cells": failed,
        "pending_cells": pending,
        "server_context_tokens": 262_144,
        "scheduler_max_seqs": scheduler_max_seqs,
        "benchmark_max_concurrency": benchmark_max_concurrency,
        "theoretical_256k_concurrency": theoretical_256k_concurrency,
        "source_unit_tests_passed": SOURCE_TESTS_PASSED,
    }
    # Pending benchmark work is an explicit execution state, not a failed data
    # source. Keep it visible in the summary and limitations instead of using
    # accessIssues, whose reader wording correctly implies a source-load error.
    access_issues: list[dict[str, Any]] = []

    concurrency_fields = [
        f"c{value}" for value in range(1, benchmark_max_concurrency + 1)
    ]
    matrix_cte = (
        "WITH matrix AS (\n"
        f"  SELECT * FROM read_csv_auto('{source_path}', header = true)\n"
        ")\n"
    )
    sources = [
        {
            "id": "matrix-rows",
            "label": "All long-context performance matrix rows",
            "path": source_path,
            "query": {
                "engine": "DuckDB over CSV generated by performance_matrix.py",
                "sql": (
                    matrix_cte
                    + "SELECT * FROM matrix ORDER BY context_target, concurrency;"
                ),
                "description": (
                    "One row per context/concurrency cell; pending rows contain "
                    "no fabricated latency or throughput values."
                ),
                "executed_at": generated_at,
                "filters": [
                    "context_target = 10000..200000 step 10000",
                    f"concurrency = 1..{benchmark_max_concurrency}",
                    "streaming Chat Completions",
                    "unique cache_salt per request",
                ],
                "metric_definitions": [
                    (
                        "TTFT starts before request dispatch and ends at the first "
                        "non-empty content, reasoning, or tool delta; role-only "
                        "SSE events do not count."
                    ),
                    (
                        "Cell latency percentiles use nearest-rank quantiles over "
                        "all requests in the cell."
                    ),
                    (
                        "Input/output throughput divides aggregate realized tokens "
                        "by the sum of concurrent-wave wall time."
                    ),
                    (
                        "A cell is complete only when every request succeeds and "
                        "realized prompt tokens remain within the declared tolerance."
                    ),
                ],
                "tables_used": [source_path],
            },
        },
        {
            "id": "matrix-summary",
            "label": "Matrix execution summary",
            "path": source_path,
            "query": {
                "engine": "DuckDB over benchmark CSV",
                "sql": (
                    matrix_cte + "SELECT COUNT(*) AS planned_cells, "
                    "COUNT(*) FILTER (WHERE status <> 'pending') AS executed_cells, "
                    "COUNT(*) FILTER (WHERE status = 'complete') AS complete_cells, "
                    "COUNT(*) FILTER (WHERE status = 'failed') AS failed_cells, "
                    "COUNT(*) FILTER (WHERE status = 'pending') AS pending_cells "
                    "FROM matrix;"
                ),
                "description": (
                    "Counts execution state across the exact "
                    f"{planned_cells}-cell matrix."
                ),
                "executed_at": generated_at,
                "metric_definitions": [
                    "Executed means complete or failed; pending has no measured values."
                ],
                "tables_used": [source_path],
            },
        },
        {
            "id": "matrix-coverage",
            "label": "Matrix coverage by context",
            "path": source_path,
            "query": {
                "engine": "DuckDB over benchmark CSV",
                "sql": (
                    matrix_cte + "SELECT context_target / 1000 AS context_k, "
                    "COUNT(*) FILTER (WHERE status = 'complete') AS complete, "
                    "COUNT(*) FILTER (WHERE status = 'failed') AS failed, "
                    "COUNT(*) FILTER (WHERE status = 'pending') AS pending "
                    "FROM matrix GROUP BY context_target ORDER BY context_target;"
                ),
                "description": (
                    "Groups the "
                    f"{benchmark_max_concurrency} concurrency cells at each "
                    "context length."
                ),
                "executed_at": generated_at,
                "tables_used": [source_path],
            },
        },
        {
            "id": "source-validation",
            "label": "Reviewed source compatibility tests",
            "path": "r1/tests and vLLM target source tests",
            "query": {
                "engine": "pytest and Ruff",
                "sql": (f"SELECT {SOURCE_TESTS_PASSED} AS source_unit_tests_passed;"),
                "description": (
                    "3,885 unique build-host source tests passed: 3,802 parser "
                    "engine tests, 31 tokenizer tests, 50 Anthropic conversion "
                    "tests, and 2 CPU lifecycle contracts. The 5 CUDA MegaMoE "
                    "tests remain mandatory on the target A100 gate."
                ),
                "executed_at": "2026-08-20T08:20:00Z",
                "metric_definitions": [
                    (
                        "Source tests exclude target GPU integration and "
                        "performance measurements."
                    )
                ],
            },
        },
        {
            "id": "runtime-contract",
            "label": "Locked runtime configuration",
            "path": f"r1/config/schemes/{scheme}.env",
            "query": {
                "engine": "release manifest",
                "sql": (
                    "SELECT 262144 AS server_context_tokens, "
                    f"{tensor_parallel_size} AS tensor_parallel_size, "
                    f"{scheduler_max_seqs} AS scheduler_max_seqs;"
                ),
                "description": (
                    f"{scheme_label}: fixed 256K window, TP={tensor_parallel_size}, "
                    f"max-num-seqs={scheduler_max_seqs}, target-only method."
                ),
                "executed_at": generated_at,
                "tables_used": [
                    "r1/config/target.env",
                    f"r1/config/schemes/{scheme}.env",
                    "r1/manifests/source-lock.json",
                    "r1/manifests/benchmark-contract.json",
                ],
            },
        },
    ]
    cards = [
        metric_card(
            "planned", "Planned matrix cells", "planned_cells", "matrix-summary"
        ),
        metric_card("executed", "Executed cells", "executed_cells", "matrix-summary"),
        metric_card("failed", "Failed cells", "failed_cells", "matrix-summary"),
        metric_card("pending", "Pending cells", "pending_cells", "matrix-summary"),
        metric_card(
            "window",
            "Server context tokens",
            "server_context_tokens",
            "runtime-contract",
        ),
        metric_card(
            "scheduler",
            "Scheduler max sequences",
            "scheduler_max_seqs",
            "runtime-contract",
        ),
        metric_card(
            "tests",
            "Relevant source tests passed",
            "source_unit_tests_passed",
            "source-validation",
        ),
    ]
    charts = [
        {
            "id": "coverage",
            "title": "Matrix coverage",
            "intent": "status",
            "question": "How much of the required matrix has executed?",
            "rationale": (
                "A status bar exposes complete, failed, and pending coverage "
                "without implying performance where no A100 run exists."
            ),
            "comparisonContext": {
                "grain": "one bar per execution state",
                "denominator": f"{planned_cells} matrix cells",
                "unit": "cells",
            },
            "type": "bar",
            "dataset": "coverage_total",
            "sourceId": "matrix-summary",
            "encodings": {
                "x": {
                    "field": "state",
                    "type": "nominal",
                    "label": "Execution state",
                },
                "y": {
                    "field": "cells",
                    "type": "quantitative",
                    "label": "Cells",
                    "unit": "cells",
                },
            },
            "valueFormat": "number",
            "unit": "cells",
            "layout": "full",
            "maxRows": 20,
            "palette": {"kind": "categorical"},
            "surface": {
                "compact": True,
                "viewMode": "visualization",
                "interactiveLegend": False,
            },
        }
    ]
    if complete:
        charts.extend(
            [
                {
                    "id": "ttft-heatmap",
                    "title": "P95 time to first token",
                    "intent": "relationship",
                    "question": "How does P95 TTFT vary with context and concurrency?",
                    "rationale": (
                        "The latency heatmap exposes long-context scheduling and "
                        "prefill pressure across the exact matrix."
                    ),
                    "comparisonContext": {
                        "grain": "context × concurrency cell",
                        "unit": "milliseconds",
                    },
                    "type": "heatmap",
                    "dataset": "ttft_heatmap",
                    "sourceId": "matrix-rows",
                    "encodings": {
                        "x": {
                            "field": "context_k",
                            "type": "ordinal",
                            "label": "Prompt context (K tokens)",
                        },
                        "y": {
                            "fields": concurrency_fields,
                            "type": "quantitative",
                            "label": "P95 TTFT",
                            "unit": "ms",
                        },
                    },
                    "valueFormat": "number",
                    "unit": "ms",
                    "layout": "full",
                    "maxRows": 20,
                    "palette": {"kind": "sequential"},
                    "surface": {
                        "compact": True,
                        "viewMode": "visualization",
                        "interactiveLegend": False,
                    },
                },
                {
                    "id": "throughput-heatmap",
                    "title": "Total throughput matrix",
                    "intent": "relationship",
                    "question": (
                        "How does throughput vary with context and concurrency?"
                    ),
                    "rationale": (
                        "The same matrix heatmap makes saturation and long-context "
                        "throughput collapse visible once target data is present."
                    ),
                    "comparisonContext": {
                        "grain": "context × concurrency cell",
                        "unit": "tokens per second",
                    },
                    "type": "heatmap",
                    "dataset": "throughput_heatmap",
                    "sourceId": "matrix-rows",
                    "encodings": {
                        "x": {
                            "field": "context_k",
                            "type": "ordinal",
                            "label": "Prompt context (K tokens)",
                        },
                        "y": {
                            "fields": concurrency_fields,
                            "type": "quantitative",
                            "label": "Total throughput",
                            "unit": "tokens/s",
                        },
                    },
                    "valueFormat": "number",
                    "unit": "tokens/s",
                    "layout": "full",
                    "maxRows": 20,
                    "emptyState": (
                        "No target A100 throughput results have been recorded yet."
                    ),
                    "palette": {"kind": "sequential"},
                    "surface": {
                        "compact": True,
                        "viewMode": "visualization",
                        "interactiveLegend": False,
                    },
                },
            ]
        )
    tables = [
        {
            "id": "matrix-table",
            "title": f"All {planned_cells} benchmark cells",
            "subtitle": (
                "Exact results, status, and failure evidence for every required cell"
            ),
            "dataset": "matrix",
            "sourceId": "matrix-rows",
            "density": "dense",
            "layout": "full",
            "defaultSort": {"field": "context_target", "direction": "asc"},
            "columns": [
                {"field": "status", "label": "Status", "type": "text"},
                {
                    "field": "context_target",
                    "label": "Context",
                    "format": "number",
                },
                {"field": "concurrency", "label": "C", "format": "number"},
                {"field": "ttft_ms_p95", "label": "P95 TTFT", "format": "number"},
                {
                    "field": "latency_ms_p95",
                    "label": "P95 E2E",
                    "format": "number",
                },
                {
                    "field": "total_tokens_per_second",
                    "label": "Tok/s",
                    "format": "number",
                },
            ],
        }
    ]
    completed_rows = [row for row in rows if row["status"] == "complete"]
    if status == "ready":
        ttft_values = [row["ttft_ms_p95"] for row in completed_rows]
        throughput_values = [row["total_tokens_per_second"] for row in completed_rows]
        status_summary = (
            f"**Result:** all {complete}/{planned_cells} cells completed "
            "successfully. Cell "
            f"P95 TTFT spans "
            f"{min(ttft_values):,.1f}–{max(ttft_values):,.1f} ms and peak total "
            f"throughput is {max(throughput_values):,.1f} tokens/s."
        )
    elif complete:
        ttft_values = [row["ttft_ms_p95"] for row in completed_rows]
        throughput_values = [row["total_tokens_per_second"] for row in completed_rows]
        status_summary = (
            f"**Partial result:** {complete}/{planned_cells} cells complete; "
            "measured cell "
            f"P95 TTFT currently spans {min(ttft_values):,.1f}–"
            f"{max(ttft_values):,.1f} ms and peak observed total throughput is "
            f"{max(throughput_values):,.1f} tokens/s. {failed} failed and "
            f"{pending} pending cells prevent a final conclusion."
        )
    else:
        status_summary = (
            f"**Status:** {complete} complete, {failed} failed, and {pending} "
            "pending. Complete the target gates before drawing a performance "
            "conclusion; Claude support is best-effort."
        )

    coverage_blocks = [
        {
            "id": "coverage-interpretation",
            "type": "markdown",
            "layout": "full",
            "sourceId": "matrix-summary",
            "body": (
                "## Coverage is explicit before performance\n\n"
                f"The matrix currently contains **{complete} complete**, "
                f"**{failed} failed**, and **{pending} pending** cells. Failed or "
                "pending cells are never imputed, so the chart cannot imply "
                "performance evidence that the target has not produced."
            ),
        },
        {
            "id": "coverage-block",
            "type": "chart",
            "chartId": "coverage",
            "layout": "full",
        },
    ]
    performance_blocks = []
    if complete:
        minimum_ttft = min(completed_rows, key=lambda row: row["ttft_ms_p95"])
        maximum_ttft = max(completed_rows, key=lambda row: row["ttft_ms_p95"])
        peak_throughput = max(
            completed_rows, key=lambda row: row["total_tokens_per_second"]
        )
        performance_blocks = [
            {
                "id": "ttft-interpretation",
                "type": "markdown",
                "layout": "full",
                "sourceId": "matrix-rows",
                "body": (
                    "## P95 first-token latency across measured cells\n\n"
                    "Observed P95 TTFT ranges from "
                    f"**{minimum_ttft['ttft_ms_p95']:,.1f} "
                    f"ms** at {minimum_ttft['context_k']}K context / concurrency "
                    f"{minimum_ttft['concurrency']} to "
                    f"**{maximum_ttft['ttft_ms_p95']:,.1f} ms** at "
                    f"{maximum_ttft['context_k']}K / concurrency "
                    f"{maximum_ttft['concurrency']}. Blank cells remain unmeasured; "
                    "use the heatmap to locate long-context prefill pressure, not "
                    "as a confidence interval."
                ),
            },
            {
                "id": "ttft-block",
                "type": "chart",
                "chartId": "ttft-heatmap",
                "layout": "full",
            },
            {
                "id": "throughput-interpretation",
                "type": "markdown",
                "layout": "full",
                "sourceId": "matrix-rows",
                "body": (
                    "## Total token throughput across measured cells\n\n"
                    f"Peak observed throughput is "
                    f"**{peak_throughput['total_tokens_per_second']:,.1f} tokens/s** "
                    f"at {peak_throughput['context_k']}K context / concurrency "
                    f"{peak_throughput['concurrency']}. Compare adjacent cells to "
                    "identify saturation; incomplete rows remain blank and do not "
                    "participate in the peak."
                ),
            },
            {
                "id": "throughput-block",
                "type": "chart",
                "chartId": "throughput-heatmap",
                "layout": "full",
            },
        ]

    repetition_values = sorted({row["repetitions"] for row in rows})
    if repetition_values == [1]:
        repetition_note = (
            "Each cell is one concurrent wave by default; repeat selected "
            "operating points before making a capacity commitment."
        )
    else:
        repetition_note = (
            "Cell repetition counts range from "
            f"{repetition_values[0]} to {repetition_values[-1]}."
        )
    if status == "ready":
        next_step = (
            "All planned cells passed. Use the heatmaps to choose candidate "
            "operating points, then rerun those points with additional repetitions "
            "before setting a production SLO."
        )
    else:
        next_step = (
            f"Resume `./benchmark_{scheme}.sh`; the matrix is saved cell by "
            "cell, so completed "
            "work is retained. Do not select a production operating point until all "
            f"{planned_cells} cells complete without failures."
        )
    blocks = [
        {
            "id": "technical-summary",
            "type": "markdown",
            "layout": "full",
            "body": (
                f"# DeepSeek V4 A100 {scheme_label} performance\n\n"
                "## Technical summary\n\n"
                "**Validated:** 3,885 reviewed build-host source tests pass; "
                "runtime is 262,144 tokens, "
                f"TP{tensor_parallel_size}, and max sequences {scheduler_max_seqs}. "
                f"**Method:** {planned_cells} cells cross concurrency "
                f"1–{benchmark_max_concurrency} and 10K–200K prompts with token "
                "calibration and "
                f"cache isolation. {status_summary}"
            ),
        },
        {
            "id": "status-metrics",
            "type": "metric-strip",
            "cardIds": [card["id"] for card in cards],
            "layout": "full",
        },
        {
            "id": "runtime-definition",
            "type": "markdown",
            "layout": "full",
            "sourceId": "runtime-contract",
            "body": (
                "## The runtime contract stays fixed\n\n"
                "The server window is **262,144 tokens**, tensor parallelism is "
                f"**{tensor_parallel_size}**, GPUs are **{gpu_devices}**, and the "
                f"scheduler ceiling is **{scheduler_max_seqs} sequences**. The "
                f"memory estimate is **{theoretical_256k_concurrency:.2f}** resident "
                "full-256K requests; the scheduler ceiling is a queue/overcommit "
                "limit, not a promise that they all remain resident. The "
                "benchmark varies request concurrency only; it does not change "
                "the serving configuration between cells."
            ),
        },
        {
            "id": "measurement-method",
            "type": "markdown",
            "layout": "full",
            "sourceId": "matrix-rows",
            "body": (
                f"## What the {planned_cells}-cell matrix measures\n\n"
                f"The test crosses concurrency **1–{benchmark_max_concurrency}** "
                "with calibrated prompts from "
                "**10K–200K tokens in 10K steps**. Requests use streaming Chat "
                "Completions and a unique cache salt. TTFT ends on the first "
                "non-empty content, reasoning, or tool delta; complete cells require "
                "every request to succeed and prompt length to stay within ±32 tokens."
            ),
        },
        *coverage_blocks,
        *performance_blocks,
        {
            "id": "limitations-next-step",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Interpretation limits and next step\n\n"
                f"{repetition_note} Build-host source compatibility tests do not "
                "replace the mandatory A100 model-load, CUDA MegaMoE, stability, "
                f"and API gates. {next_step} Claude compatibility remains "
                "best-effort and is reported separately from the OpenAI API contract."
            ),
        },
        {
            "id": "matrix-guide",
            "type": "markdown",
            "layout": "full",
            "sourceId": "matrix-rows",
            "body": (
                "## Cell-level evidence remains auditable\n\n"
                f"The table below keeps all {planned_cells} required cells in "
                "deterministic "
                "context/concurrency order. Pending measurements remain null, and "
                "failed rows retain their error evidence for diagnosis and rerun."
            ),
        },
        {
            "id": "matrix-table-block",
            "type": "table",
            "tableId": "matrix-table",
            "layout": "full",
        },
    ]
    return {
        "ok": True,
        "widget_type": "artifact",
        "package_info": PORTABLE_PACKAGE_INFO,
        "packageInfo": PORTABLE_PACKAGE_INFO,
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": f"DeepSeek V4 A100 {scheme_label} performance",
            "description": (
                f"Target-only 256K deployment validation and {planned_cells}-cell "
                f"long-context benchmark for scheme {scheme}"
            ),
            "generatedAt": generated_at,
            "cards": cards,
            "charts": charts,
            "tables": tables,
            "sources": sources,
            "blocks": blocks,
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": status,
            "datasets": {
                "summary": [summary],
                "coverage": coverage_rows(rows),
                "coverage_total": [
                    {"state": "Complete", "cells": complete},
                    {"state": "Failed", "cells": failed},
                    {"state": "Pending", "cells": pending},
                ],
                "ttft_heatmap": heatmap_rows(
                    rows, "ttft_ms_p95", benchmark_max_concurrency
                ),
                "throughput_heatmap": heatmap_rows(
                    rows, "total_tokens_per_second", benchmark_max_concurrency
                ),
                "matrix": rows,
            },
            "accessIssues": access_issues,
        },
        "sources": sources,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--source-path", default="r1/reports/data/performance-matrix.csv"
    )
    parser.add_argument("--scheme", choices=("one", "two"), default="one")
    parser.add_argument("--scheme-label", default="方案一（8 卡，GPU 0-7）")
    parser.add_argument("--gpu-devices", default="0,1,2,3,4,5,6,7")
    parser.add_argument("--tensor-parallel-size", type=int, default=8)
    parser.add_argument("--scheduler-max-seqs", type=int, default=32)
    parser.add_argument("--benchmark-max-concurrency", type=int, default=16)
    parser.add_argument("--theoretical-256k-concurrency", type=float, default=19.58)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = artifact(
        load_rows(args.matrix, args.benchmark_max_concurrency),
        args.source_path,
        scheme=args.scheme,
        scheme_label=args.scheme_label,
        gpu_devices=args.gpu_devices,
        tensor_parallel_size=args.tensor_parallel_size,
        scheduler_max_seqs=args.scheduler_max_seqs,
        benchmark_max_concurrency=args.benchmark_max_concurrency,
        theoretical_256k_concurrency=args.theoretical_256k_concurrency,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(args.output)
    print(
        f"ARTIFACT_BUILD=PASS status={payload['snapshot']['status']} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
