# HTML report QA artifacts

The PNG files in this directory are failure-only screenshots produced while the packaged browser
verifier diagnosed horizontal overflow. They are retained as process evidence, not as report
deliverables. The failures were caused by full-viewport sticky header CSS interacting with a 15px
Linux scrollbar; report data and chart validation had already passed.

The final `reports/performance-report-2026-08-13.html` was rebuilt, received a bounded outer-header
CSS compatibility correction, and passed the same verifier at 1440px and 390px with source-dialog
interaction and no external network requests.

One later default-timeout rerun produced `reader_not_visible`; the unchanged HTML passed immediately
with a 10-second reader budget and 20-second total budget. Its screenshot is retained to document that
headless startup timing fluctuation rather than presenting it as a report-content failure.
