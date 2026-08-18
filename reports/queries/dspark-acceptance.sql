SELECT
  snapshot,
  mean_acceptance_length,
  accepted_tokens,
  drafted_tokens,
  average_draft_acceptance_rate
FROM dspark_acceptance
ORDER BY snapshot;
