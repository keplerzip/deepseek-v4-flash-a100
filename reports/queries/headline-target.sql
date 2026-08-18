SELECT
  record_id,
  ttft_p50_s,
  ttft_p95_s,
  decode_tps_mean,
  aggregate_tps,
  CAST(successful_requests AS REAL) / total_requests AS success_rate,
  successful_requests,
  total_requests
FROM field_benchmarks
WHERE record_id = 'TGT-GRAPH-001';
