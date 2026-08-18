SELECT
  record_id,
  max_model_len,
  prompt_tokens,
  output_tokens,
  concurrency,
  successful_requests,
  total_requests,
  ttft_p50_s,
  ttft_p95_s,
  decode_tps_mean,
  aggregate_tps,
  e2e_p50_s
FROM field_benchmarks
WHERE record_id IN ('TGT-GRAPH-001', 'TGT-GRAPH-002')
ORDER BY record_id;
