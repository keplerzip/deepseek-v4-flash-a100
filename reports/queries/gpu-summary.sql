SELECT
  gpu,
  active_samples,
  util_avg_percent,
  util_p95_percent,
  power_avg_w,
  power_max_w,
  memory_peak_mib
FROM target_gpu_summary
ORDER BY gpu;
