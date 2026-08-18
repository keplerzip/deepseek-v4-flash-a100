# Benchmarks

Both modes call the same `scripts/benchmark_api.py`, so request construction and
metric definitions are identical. The quick matrix is intentionally small. Run
the complete requested matrix only after selecting a profile that can hold each
prompt plus its output:

```bash
BENCHMARK_MATRIX=full target-only/benchmark.sh
BENCHMARK_MATRIX=full dspark/benchmark.sh
```

Set `INCLUDE_256K=1` to add 262144-token prompts. Set
`BASELINE_PROMPT_FILE=/absolute/path` to use the exact historical MiniMax prompt.
Without it, the benchmark creates and saves an exact 11000-token prompt using
the server's local `/tokenize` and `/detokenize` endpoints; it is explicitly
labelled as a generated prompt, not the historical baseline.

Results are JSON and CSV under `benchmarks/results/`. TTFT is measured at the
client from request start to the first non-empty streamed chunk. The reported
prefill latency is a labelled TTFT proxy and includes scheduling/transport.
DSpark acceptance is derived from vLLM's cumulative speculative counters before
and after the run. This commit does not expose a direct draft-latency metric, so
that field remains null rather than being invented.

Historical machine baseline (reference only): MiniMax-M2.7, 230B total / 10B
activated, about 11K input tokens, prefill/TTFT about 15 seconds. It is never
rewritten as a different parameter scale and is not treated as a pass threshold.

The 2026-08-13 target-host results supplied through terminal output are archived
under `reports/data/` and interpreted in
`reports/performance-report-2026-08-13.md`. Those extracts are evidence grade B:
the exact values and original result filenames are retained, but the source JSON
and GPU CSV files have not yet been copied back from the target host.

The canonical production choice after that tuning is target-only, 256K,
`max_num_seqs=16`, `max_num_batched_tokens=4096`, GPU memory utilization 0.92,
CUDA Graph, TP=8. This exact combination still requires the documented 1-hour
and 24-hour stability runs; it must not be described as fully soak-tested.
