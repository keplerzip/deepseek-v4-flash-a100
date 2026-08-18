# Performance report source notes

## Reporting job

- Audience: technical operators and inference engineers.
- Decision: choose a production configuration and retain an auditable field-test history.
- Scope: 2026-08-13 target-host runs supplied in the deployment conversation.
- Baseline: fixed A100 host and vLLM fork; MiniMax is reference-only because its original prompt is unavailable.
- Selected delivery mode: portable HTML, with Markdown retained as the repository-native companion.

## Evidence inventory and quality

| Source | Grain | Rows | Grade | Main limitation |
|---|---|---:|---|---|
| `field-benchmark-summary-2026-08-13.csv` | one benchmark group | 21 | B | source JSON not copied back |
| `target-gpu-summary-2026-08-13.csv` | one GPU over active samples | 8 | B | only one C12 run |
| `dspark-acceptance-snapshots-2026-08-13.csv` | one vLLM log interval | 5 | B | workload labels unavailable |

Checks performed:

- all CSV files parse with a stable column count;
- benchmark row count is 21 and every record id is unique;
- successes never exceed total requests;
- TTFT/decode values are non-negative where present;
- failure rows do not receive fabricated latency values;
- target GPU indices are exactly 0–7;
- acceptance rates are between 0 and 1;
- metric definitions were checked against `scripts/benchmark_api.py`;
- derived 256K capacity is labelled C and is not stored as a measured benchmark row.

Known configuration uncertainty: `DSP-GRAPH-007` is associated with the surrounding 0.80/C6
configuration sequence, but its launch manifest was not pasted. The report therefore treats it as a
single successful matrix, not a general DSpark stability proof.

## Required structure mapping

| Technical report requirement | Visible section |
|---|---|
| Title | report title |
| Technical summary | `技术摘要` |
| Key findings with visual evidence | target-only, MNB tuning, DSpark sections |
| Scope/data/metric definitions | `数据范围与指标口径` |
| Methodology | `方法与可复现性` |
| Limitations and robustness | `局限性、质量风险与稳健性检查` |
| Recommended next steps | `建议的生产动作` |
| Further questions | `仍需回答的问题` |

## Chart map

| Section | Question | Form | Fields | Supported claim | Palette |
|---|---|---|---|---|---|
| MNB tuning | How does prefill scheduling trade TTFT against concurrency? | grouped bar | concurrency, TTFT, batched tokens | 4096 is the middle latency/capacity choice | three approved categorical roots |

Omitted visuals:

- target-only versus DSpark speedup: omitted because no fully matched A/B pair exists;
- GPU utilization: exact eight-row table is more honest for a single run than a trend chart;
- DSpark acceptance: only five unlabeled intervals exist, so a trend shape would imply unsupported time context.

## Reproducibility

The report artifact uses bounded rows copied from the three reviewed CSVs. SQL in `reports/queries/`
was executed against an in-memory SQLite database populated from those CSV files during QA. The
portable HTML is generated from `performance-report-2026-08-13.artifact.json` using the Data
Analytics packaged report builder. No network resource is required by the resulting HTML.

The packaged reader's full-bleed sticky headers use viewport width and negative margins. On the
installed Chromium build, the 15px vertical scrollbar made `.analytics-top-bar` wider than
`documentElement.clientWidth`; the no-script `.portable-page-header` had the same boundary risk. The
canonical builder therefore passed schema and payload checks but initially failed its browser overflow
gate. After that documented failure, generated outer CSS received a bounded compatibility rewrite:
the no-script header uses auto width and zero margins, while one outer style override gives the enhanced
top bar `width:100%` and zero margins. The embedded canonical artifact is unchanged and was compared
byte-for-byte by the final verifier; datasets, charts, tables, and source metadata are unchanged. Final
browser QA passed at 1440px and 390px, including source-dialog keyboard interaction and zero external
network requests.
