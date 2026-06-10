#!/usr/bin/env python3
"""Render experiment_summaries.csv → report.html (single-file static report)."""
import csv
import html
from pathlib import Path

HERE = Path(__file__).parent
CSV = HERE / "experiment_summaries.csv"
OUT = HERE / "report.html"

# Each technique → (label, badge, css-class)
TECHNIQUES = [
    ("baseline_title",   "baseline · what `claude --resume` shows", "baseline"),
    ("tier1_structural", "TIER 1 · structural (zero-LLM)",          "winner"),
    ("yake_5",           "YAKE · keyword chips (zero-LLM)",         "winner"),
    ("lead_3",           "Lead-3 · first three user prompts",       "alt"),
    ("textrank_2",       "TextRank · LexRank top 2 sentences",      "alt"),
    ("rake_5",           "RAKE · top 5 keyphrases",                 "reject"),
]

CSS = """
:root {
  --bg: #0f1115;
  --card: #151820;
  --border: #232733;
  --text: #d7dae0;
  --muted: #7a8090;
  --accent: #6ad48a;
  --accent-soft: rgba(106,212,138,.12);
  --warn: #f0a36b;
  --reject: #707380;
  --baseline: #5a9fd4;
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
  background: var(--bg); color: var(--text); margin: 0;
  padding: 32px 24px 80px; line-height: 1.5;
}
header {
  max-width: 1100px; margin: 0 auto 32px;
  display: flex; justify-content: space-between; align-items: baseline; gap: 24px;
}
h1 { font-size: 20px; margin: 0; font-weight: 600; }
.meta { color: var(--muted); font-size: 13px; }
.legend {
  max-width: 1100px; margin: 0 auto 24px;
  display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; color: var(--muted);
}
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot.winner { background: var(--accent); }
.dot.alt { background: var(--warn); }
.dot.baseline { background: var(--baseline); }
.dot.reject { background: var(--reject); }
.card {
  max-width: 1100px; margin: 0 auto 20px;
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px 22px;
}
.card-head {
  display: flex; justify-content: space-between; align-items: baseline;
  border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 14px;
  gap: 16px; flex-wrap: wrap;
}
.sid { font-family: ui-monospace, "SF Mono", Menlo, monospace; color: var(--muted); font-size: 12px; }
.proj { font-weight: 600; font-size: 15px; color: var(--text); }
.stats { color: var(--muted); font-size: 12px; font-family: ui-monospace, "SF Mono", Menlo, monospace; }
.row {
  display: grid; grid-template-columns: 220px 1fr; gap: 14px;
  padding: 8px 0; border-bottom: 1px dashed rgba(255,255,255,.04);
}
.row:last-child { border-bottom: none; }
.row .label {
  font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted); padding-top: 3px;
}
.row .value {
  font-size: 13px; word-wrap: break-word; overflow-wrap: anywhere;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  white-space: pre-wrap;
}
.row.winner .label { color: var(--accent); font-weight: 600; }
.row.winner .value { background: var(--accent-soft); padding: 4px 8px; border-radius: 4px; }
.row.baseline .label { color: var(--baseline); }
.row.alt .label { color: var(--warn); }
.row.reject .label { color: var(--reject); }
.row.reject .value { color: var(--reject); }
.empty { color: var(--reject); font-style: italic; }
"""


def render_value(v):
    if not v:
        return '<span class="empty">(empty)</span>'
    return html.escape(v)


def main():
    rows = list(csv.DictReader(CSV.open()))
    parts = ["<!doctype html><html><head><meta charset='utf-8'>",
             "<title>ccfind summarization experiment</title>",
             f"<style>{CSS}</style></head><body>"]
    parts.append(
        f"<header><h1>ccfind summarization experiment</h1>"
        f"<div class='meta'>{len(rows)} sessions · 6 techniques · "
        f"<a style='color:var(--muted)' href='experiment_summaries.csv'>CSV</a></div></header>"
    )
    parts.append(
        "<div class='legend'>"
        "<span><i class='dot winner'></i>recommended for v1</span>"
        "<span><i class='dot baseline'></i>baseline (claude --resume)</span>"
        "<span><i class='dot alt'></i>alternative (rejected)</span>"
        "<span><i class='dot reject'></i>noise (do not ship)</span>"
        "</div>"
    )
    for r in rows:
        parts.append("<div class='card'>")
        parts.append(
            f"<div class='card-head'>"
            f"<div><span class='proj'>{html.escape(r['project'])}</span> "
            f"<span class='sid'>{html.escape(r['session_id'])}</span></div>"
            f"<div class='stats'>{r['size_kb']} KB · {r['user_turns']} turns · "
            f"{r['tool_calls']} tool calls</div>"
            f"</div>"
        )
        for col, label, cls in TECHNIQUES:
            parts.append(
                f"<div class='row {cls}'><div class='label'>{label}</div>"
                f"<div class='value'>{render_value(r.get(col, ''))}</div></div>"
            )
        parts.append("</div>")
    parts.append("</body></html>")
    OUT.write_text("".join(parts))
    print(f"Wrote {OUT} ({len(rows)} session cards)")


if __name__ == "__main__":
    main()
