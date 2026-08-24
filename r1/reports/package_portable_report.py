#!/usr/bin/env python3
"""Repackage a canonical artifact into the validated portable HTML runtime."""

from __future__ import annotations

import argparse
import base64
import gzip
import html
import json
import os
import re
from pathlib import Path
from typing import Any

PAYLOAD_PATTERN = re.compile(
    r'(<template id="data-analytics-portable-artifact-payload-source"[^>]*>\s*)'
    r".*?"
    r"(\s*</template>)",
    re.DOTALL,
)
FALLBACK_PATTERN = re.compile(
    r'<main id="data-analytics-portable-fallback".*?</main>', re.DOTALL
)
OVERFLOW_FIX_MARKER = 'data-dsv4-portable-overflow-fix="true"'
OVERFLOW_FIX_STYLE = """<style data-dsv4-portable-overflow-fix="true">
.analytics-top-bar {
  width: 100% !important;
  margin-right: 0 !important;
  margin-left: 0 !important;
}
</style>
"""


def escaped(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fallback_html(artifact: dict[str, Any]) -> str:
    manifest = artifact["manifest"]
    snapshot = artifact["snapshot"]
    datasets = snapshot["datasets"]
    summary = datasets["summary"][0]
    matrix = datasets["matrix"]
    title = escaped(manifest["title"])
    description = escaped(manifest.get("description", ""))
    generated_at = escaped(manifest.get("generatedAt", snapshot.get("generatedAt")))
    metrics = [
        ("Planned cells", summary.get("planned_cells")),
        ("Complete cells", summary.get("complete_cells")),
        ("Failed cells", summary.get("failed_cells")),
        ("Pending cells", summary.get("pending_cells")),
        ("Server context", summary.get("server_context_tokens")),
        ("Scheduler max sequences", summary.get("scheduler_max_seqs")),
    ]
    metric_cards = "".join(
        '<article class="portable-metric-card">'
        f'<p class="portable-metric-label">{escaped(label)}</p>'
        f'<p class="portable-metric-value">{escaped(value)}</p>'
        "</article>"
        for label, value in metrics
    )
    columns = [
        ("status", "Status"),
        ("context_target", "Context tokens"),
        ("concurrency", "Concurrency"),
        ("ttft_ms_p95", "P95 TTFT ms"),
        ("latency_ms_p95", "P95 latency ms"),
        ("total_tokens_per_second", "Total tokens/s"),
        ("error_summary", "Error"),
    ]
    table_header = "".join(f'<th scope="col">{label}</th>' for _, label in columns)
    table_rows = "".join(
        "<tr>"
        + "".join(f"<td>{escaped(row.get(field))}</td>" for field, _ in columns)
        + "</tr>"
        for row in matrix
    )
    complete = int(summary.get("complete_cells", 0))
    failed = int(summary.get("failed_cells", 0))
    pending = int(summary.get("pending_cells", 0))
    planned = int(summary.get("planned_cells", len(matrix)))
    technical_summary = (
        f"Canonical target result: {complete} complete, {failed} failed, "
        f"and {pending} pending across the exact {planned}-cell matrix. "
        "No missing value is synthesized."
    )
    return (
        '<main id="data-analytics-portable-fallback" class="portable-fallback" '
        'data-portable-fallback="true" data-portable-surface="report">'
        '<header class="portable-page-header"><div class="portable-page-heading">'
        '<p class="portable-surface-label">Data Analytics target report</p>'
        f'<h1>{title}</h1><p class="portable-description">{description}</p>'
        '</div><div class="portable-page-meta">'
        f'<time datetime="{generated_at}">{generated_at}</time></div></header>'
        '<div class="portable-block-stack">'
        '<div class="portable-block portable-layout-full"><section '
        'class="portable-markdown"><h2>Technical summary</h2>'
        f"<p>{escaped(technical_summary)}</p></section></div>"
        '<div class="portable-block portable-layout-full"><section '
        f'class="portable-metric-grid">{metric_cards}</section></div>'
        '<div class="portable-block portable-layout-full"><section '
        f'class="portable-content-card"><header><h2>All {planned} benchmark cells</h2>'
        "<p>Target-generated fallback table backed by the embedded canonical "
        "artifact.</p>"
        '</header><div class="portable-table-scroll"><table><caption>'
        f"All {planned} benchmark cells</caption><thead><tr>{table_header}</tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div></section></div></div></main>"
    )


def encode_artifact(artifact: dict[str, Any]) -> str:
    serialized = json.dumps(
        artifact, ensure_ascii=False, separators=(",", ":"), sort_keys=False
    ).encode()
    compressed = gzip.compress(serialized, compresslevel=9, mtime=0)
    return base64.encodebytes(compressed).decode().strip()


def package_report(artifact_path: Path, template_path: Path, output_path: Path) -> None:
    artifact = json.loads(artifact_path.read_text())
    if not isinstance(artifact, dict):
        raise RuntimeError("canonical artifact must be a JSON object")
    for key in ("manifest", "snapshot", "sources"):
        if key not in artifact:
            raise RuntimeError(f"canonical artifact omitted {key}")
    datasets = artifact["snapshot"].get("datasets", {})
    matrix = datasets.get("matrix", [])
    summary_rows = datasets.get("summary", [])
    if len(summary_rows) != 1:
        raise RuntimeError("canonical artifact must contain one summary row")
    planned = int(summary_rows[0].get("planned_cells", 0))
    if len(matrix) != planned or planned not in {160, 320}:
        raise RuntimeError(
            "canonical artifact matrix size must match a 160/320-cell scheme"
        )

    document = template_path.read_text()
    payload = encode_artifact(artifact)
    document, payload_replacements = PAYLOAD_PATTERN.subn(
        lambda match: match.group(1) + payload + match.group(2), document
    )
    rendered_fallback = fallback_html(artifact)
    document, fallback_replacements = FALLBACK_PATTERN.subn(
        lambda _match: rendered_fallback, document
    )
    if payload_replacements != 1 or fallback_replacements != 1:
        raise RuntimeError(
            "portable template markers changed: "
            f"payload={payload_replacements} fallback={fallback_replacements}"
        )
    if OVERFLOW_FIX_MARKER not in document:
        if document.count("</head>") != 1:
            raise RuntimeError("portable template must contain one closing head tag")
        document = document.replace("</head>", OVERFLOW_FIX_STYLE + "</head>", 1)
    document = document.replace(
        '<html lang="en" data-data-analytics-portable-artifact="true">',
        '<html lang="en" data-data-analytics-portable-artifact="true" '
        'data-target-runtime-report="true">',
        1,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(document)
    temporary.replace(output_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package_report(args.artifact, args.template, args.output)
    print(f"PORTABLE_TARGET_REPORT=PASS output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
