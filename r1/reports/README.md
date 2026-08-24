# Performance report

`performance-report.html` is the scheme-one self-contained report (320 cells).
`performance-report-two.html` is the scheme-two report (160 cells). Their
canonical artifacts and pending CSVs use the same basename convention. Both
initially show every cell as pending because this build host has no A100; blank
metrics are null—not synthetic zeroes.

After the target run, `r1/scripts/generate_result_artifact.sh` creates the
canonical artifact inside the R1 Docker image, so the target needs no host
Python or Node. Rebuild the final HTML from the exact result CSV on a
workstation with the Data Analytics plugin:

```bash
export DATA_ANALYTICS_PLUGIN_ROOT=/path/to/data-analytics/plugin
r1/reports/build_report.sh /path/to/performance-matrix.csv
DSV4_SCHEME=two r1/reports/build_report.sh /path/to/performance-matrix.csv
```

The generator rejects a CSV unless it contains the selected scheme's exact
cross product: C1–16 or C1–8, crossed with contexts 10,000–200,000 in
10,000-token steps. The portable
builder validates the canonical artifact, embeds the exact payload in one HTML
file, and records its QA receipt under `r1/reports/qa/`. Completed runs show the
P95 TTFT range in the technical summary and total throughput as the full matrix;
the source CSV retains all TTFT, latency, throughput, and failure columns.
