SELECT
  record_id,
  execution_mode,
  gpu_memory_utilization,
  kv_cache_memory_bytes,
  prompt_tokens,
  output_tokens,
  concurrency,
  successful_requests,
  total_requests,
  ttft_p50_s,
  decode_tps_mean,
  error
FROM field_benchmarks
WHERE mode = 'dspark'
ORDER BY run_utc, record_id;
