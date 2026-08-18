SELECT
  max_num_batched_tokens,
  CAST(max_num_batched_tokens AS TEXT) AS batched_label,
  concurrency,
  ttft_p50_s,
  decode_tps_mean,
  kv_max_concurrency
FROM field_benchmarks
WHERE record_id LIKE 'MNB-%'
ORDER BY max_num_batched_tokens, concurrency;
