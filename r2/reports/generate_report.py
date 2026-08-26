#!/usr/bin/env python3
"""Generate a dependency-free, self-contained HTML report from the R2 CSV."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def number(value: str, digits: int = 2) -> str:
    if value == "":
        return "—"
    try:
        numeric = float(value)
    except ValueError:
        return html.escape(value)
    if math.isnan(numeric):
        return "—"
    return f"{numeric:,.{digits}f}"


def pct(value: str) -> str:
    return "—" if value == "" else f"{float(value) * 100:.2f}%"


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def maybe_float(row: dict[str, str], key: str) -> float | None:
    try:
        return float(row[key]) if row.get(key, "") != "" else None
    except ValueError:
        return None


def summary_card(label: str, value: str, note: str) -> str:
    return (
        '<section class="card"><div class="label">'
        + html.escape(label)
        + '</div><div class="value">'
        + html.escape(value)
        + '</div><div class="note">'
        + html.escape(note)
        + "</div></section>"
    )


def generate(csv_path: Path, output: Path) -> None:
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError("benchmark CSV has no rows")
    complete = [row for row in rows if row.get("status") == "complete"]
    failed = [row for row in rows if row.get("status") == "failed"]
    pending = [row for row in rows if row.get("status") == "pending"]
    sample = rows[0]
    ttfts = [value for row in complete if (value := maybe_float(row, "ttft_ms_p95")) is not None]
    decodes = [value for row in complete if (value := maybe_float(row, "decode_tps_aggregate")) is not None]
    hits = [value for row in complete if (value := maybe_float(row, "cache_hit_actual_p50")) is not None]
    acceptances = [value for row in complete if (value := maybe_float(row, "dspark_acceptance_rate")) is not None]

    cards = "".join(
        (
            summary_card("完成格", f"{len(complete)}/{len(rows)}", f"失败 {len(failed)} · 待测 {len(pending)}"),
            summary_card("P95 TTFT 中位", "—" if not ttfts else f"{median(ttfts):,.0f} ms", "客户端流式首个有效 token"),
            summary_card("聚合 Decode 中位", "—" if not decodes else f"{median(decodes):,.1f} tok/s", "C16 最早首 token 至最晚完成窗口"),
            summary_card("实际缓存命中中位", "—" if not hits else f"{median(hits) * 100:.2f}%", "API usage cached_tokens / prompt_tokens"),
        )
    )
    if acceptances:
        cards += summary_card(
            "DSpark 接受率中位",
            f"{median(acceptances) * 100:.2f}%",
            "accepted speculative tokens / drafted tokens",
        )

    body_rows: list[str] = []
    for row in rows:
        status = row.get("status", "pending")
        body_rows.append(
            "<tr class=\"status-{}\"><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td title=\"{}\">{}</td></tr>".format(
                html.escape(status),
                html.escape(status),
                number(row.get("context_target", ""), 0),
                number(row.get("output_target", ""), 0),
                pct(row.get("cache_hit_target", "")),
                pct(row.get("cache_hit_actual_p50", "")),
                number(row.get("ttft_ms_p50", "")),
                number(row.get("ttft_ms_p95", "")),
                number(row.get("effective_uncached_prefill_tps", "")),
                number(row.get("decode_tps_aggregate", "")),
                number(row.get("itl_ms_p95", ""), 3),
                pct(row.get("dspark_acceptance_rate", "")),
                html.escape(row.get("error_summary", "")),
                html.escape(row.get("error_summary", "") or "—"),
            )
        )

    embedded = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    generated = datetime.now(UTC).isoformat()
    warning = ""
    if failed or pending:
        warning = (
            '<div class="warning">报告尚未通过完整门禁：'
            f"失败 {len(failed)} 格，待测 {len(pending)} 格。空值不会被当成 0。</div>"
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DeepSeek V4 Flash A100 R2 性能报告</title>
<style>
:root{{--bg:#f4f6f8;--panel:#fff;--ink:#18202a;--muted:#65717e;--line:#d9e0e7;--good:#0b7a53;--bad:#b42318;--accent:#315efb}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1600px;margin:auto;padding:32px}} h1{{font-size:28px;margin:0 0 6px}} h2{{margin-top:30px}} .meta{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:22px 0}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}}
.label,.note{{color:var(--muted)}} .value{{font-size:25px;font-weight:700;margin:5px 0}} .warning{{padding:12px 16px;background:#fff4e5;border-left:4px solid #ef8f00;margin:18px 0}}
.definitions{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}} .definitions section{{background:var(--panel);padding:14px;border:1px solid var(--line);border-radius:10px}}
.table-wrap{{overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:12px}} table{{border-collapse:collapse;width:100%;white-space:nowrap}} th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:right}} th{{position:sticky;top:0;background:#edf1f6;z-index:1}} th:first-child,td:first-child,td:last-child{{text-align:left}} .status-complete td:first-child{{color:var(--good);font-weight:700}} .status-failed td:first-child{{color:var(--bad);font-weight:700}} .status-pending{{color:var(--muted)}} code{{background:#e8edf3;padding:2px 5px;border-radius:4px}}
</style></head><body><main>
<h1>DeepSeek V4 Flash A100 R2 性能报告</h1>
<div class="meta">方案 {html.escape(sample.get('scheme',''))} · DSpark k={html.escape(sample.get('dspark_k',''))} · cache profile {html.escape(sample.get('cache_profile',''))} · C{html.escape(sample.get('concurrency',''))} · 生成于 {generated}</div>
{warning}<div class="cards">{cards}</div>
<h2>指标口径与门禁</h2><div class="definitions">
<section><strong>主指标</strong><br>P95 TTFT、未命中部分有效 Prefill TPS、聚合 Decode TPS。</section>
<section><strong>驱动指标</strong><br>实际缓存命中率；DSpark 方案另看 speculative acceptance。</section>
<section><strong>正确性护栏</strong><br>每格 C16 全成功、输出精确达到目标、无原始 parser marker、命中率不得落后目标超过 1 个百分点。</section>
<section><strong>Prefill TPS 限制</strong><br><code>(prompt-cached)/TTFT</code> 是端到端有效值，含排队与首个 decode token，不冒充纯 kernel 吞吐。</section>
</div>
<h2>完整矩阵</h2><div class="table-wrap"><table><thead><tr>
<th>状态</th><th>输入</th><th>输出</th><th>目标命中</th><th>实际命中</th><th>TTFT P50 ms</th><th>TTFT P95 ms</th><th>有效 Prefill tok/s</th><th>聚合 Decode tok/s</th><th>ITL P95 ms</th><th>DSpark 接受率</th><th>错误</th>
</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>
<p class="meta">源 CSV：{html.escape(str(csv_path))}。页面完全自包含，不依赖外部 JavaScript 或 CDN。</p>
<script type="application/json" id="benchmark-data">{embedded}</script>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(document)
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.csv, args.output)
    print(f"REPORT=PASS output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
