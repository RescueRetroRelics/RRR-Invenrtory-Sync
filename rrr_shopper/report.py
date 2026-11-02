"""Turns the run results into report.html, findings.json and summary.txt."""

import html
import json

IMPACT = {3: "High", 2: "Medium", 1: "Low"}
EFFORT = {1: "Easy", 2: "Medium", 3: "Hard"}


def write(outdir, base, runs, findings, started, safety_note):
    ranked = findings.ranked()
    (outdir / "findings.json").write_text(json.dumps(
        {"site": base, "started": started, "fixes": ranked, "wins": findings.wins,
         "runs": [{"who": r.who, "steps": r.steps} for r in runs]}, indent=2), encoding="utf-8")

    lines = [f"RRR simulated shopper - {base} - {started}", safety_note, ""]
    lines.append(f"WINS ({len(findings.wins)}):")
    lines += [f"  + {w}" for w in findings.wins]
    lines.append("")
    lines.append(f"FIX LIST ({len(ranked)}), most important first:")
    for i, f in enumerate(ranked, 1):
        lines.append(f"  {i}. [{IMPACT[f['impact']]} impact / {EFFORT[f['effort']]} fix / {f['status']}] {f['title']}")
        lines.append(f"     {f['detail']}")
        lines.append(f"     Fix: {f['fix']}")
    (outdir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")

    e = html.escape
    quick = [f for f in ranked if f["impact"] >= 2 and f["effort"] == 1]
    rows = []
    for i, f in enumerate(ranked, 1):
        shots = " ".join(f'<a href="{e(s)}" target="_blank">📷</a>' for s in f["shots"][:3])
        rows.append(
            f'<tr class="imp{f["impact"]}"><td>{i}</td><td><b>{e(f["title"])}</b><div class="d">{e(f["detail"])}</div>'
            f'<div class="fix">Fix: {e(f["fix"])}</div><div class="who">Seen by: {e(", ".join(f["seen_by"]))}</div></td>'
            f'<td><span class="pill i{f["impact"]}">{IMPACT[f["impact"]]}</span></td><td>{EFFORT[f["effort"]]}'
            f'{" ⚡" if f in quick else ""}</td><td><span class="st {f["status"]}">{f["status"]}</span></td><td>{shots}</td></tr>')
    journeys = []
    for r in runs:
        steps = "".join(
            f'<li class="{"ok" if s["ok"] else "bad"}">{"✅" if s["ok"] else "❌"} <b>{e(s["step"])}</b> '
            f'{e(s["note"] or "")}{f" <span class=ms>({s["ms"]/1000:.1f}s)</span>" if s.get("ms") else ""}</li>'
            for s in r.steps)
        shots = "".join(
            f'<figure><a href="{e(s["file"])}" target="_blank"><img src="{e(s["file"])}" loading="lazy" alt="{e(s["label"])}"></a>'
            f'<figcaption>{e(s["label"])}</figcaption></figure>' for s in r.shots)
        journeys.append(f'<section class="card"><h3>{e(r.who)}</h3><p class="goal">{e(r.persona.get("goal", ""))}</p>'
                        f'<ul class="steps">{steps}</ul><div class="shots">{shots}</div></section>')
    wins = "".join(f"<li>✅ {e(w)}</li>" for w in findings.wins) or "<li>None recorded</li>"

    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>RRR Shopper Report</title>
<style>
:root{{--bg:#fbf8f3;--fg:#222;--card:#fff;--line:#e6dfd3;--accent:#8a3b2b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#171513;--fg:#eee;--card:#22201d;--line:#3a352f;--accent:#e59a7f}}}}
body{{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:16px;max-width:1100px;margin:auto}}
h1{{color:var(--accent);margin-bottom:0}} .sub{{opacity:.75;margin-top:4px}}
.safety{{background:#e8f5e9;color:#1b5e20;padding:8px 12px;border-radius:8px;margin:12px 0}}
.stats{{display:flex;gap:12px;flex-wrap:wrap}} .stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px}}
.stat b{{font-size:24px;display:block}}
table{{width:100%;border-collapse:collapse;background:var(--card)}} td,th{{border-bottom:1px solid var(--line);padding:8px;vertical-align:top;text-align:left}}
.d{{font-size:13px;opacity:.85}} .fix{{font-size:13px;color:var(--accent)}} .who{{font-size:12px;opacity:.6}}
.pill{{padding:2px 8px;border-radius:10px;color:#fff;font-size:12px}} .i3{{background:#c62828}} .i2{{background:#ef6c00}} .i1{{background:#757575}}
.st{{font-size:12px;padding:2px 6px;border-radius:6px;border:1px solid var(--line)}} .verified{{background:#e3f2fd;color:#0d47a1}} .hypothesis{{background:#fff8e1;color:#795548}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:12px 0}}
.goal{{opacity:.7;margin-top:-8px}} .steps{{list-style:none;padding:0}} .bad{{color:#c62828}} .ms{{opacity:.6;font-size:12px}}
.shots{{display:flex;gap:8px;overflow-x:auto}} figure{{margin:0;flex:0 0 160px}} figure img{{width:160px;height:110px;object-fit:cover;object-position:top;border:1px solid var(--line);border-radius:6px}}
figcaption{{font-size:11px;opacity:.7}} .table-wrap{{overflow-x:auto}}
</style></head><body>
<h1>🐾 RRR Simulated Shopper Report</h1>
<p class="sub">{e(base)} · {e(started)}</p>
<div class="safety">🔒 {e(safety_note)}</div>
<div class="stats"><div class="stat"><b>{len(findings.wins)}</b>things working</div>
<div class="stat"><b>{len(ranked)}</b>things to fix</div><div class="stat"><b>{len(quick)}</b>⚡ quick wins</div>
<div class="stat"><b>{sum(1 for f in ranked if f['impact'] == 3)}</b>high impact</div></div>
<h2>✅ What's working</h2><ul>{wins}</ul>
<h2>🔧 Fix list (most important first)</h2>
<p class="d">⚡ = high/medium impact and easy to fix, start there. <b>verified</b> = the shopper saw it happen.
<b>hypothesis</b> = likely a problem, check by eye before acting.</p>
<div class="table-wrap"><table><tr><th>#</th><th>Issue</th><th>Impact</th><th>Effort</th><th>Status</th><th>Pics</th></tr>{''.join(rows)}</table></div>
<h2>🛒 Each shopper's journey</h2>{''.join(journeys)}
</body></html>"""
    (outdir / "report.html").write_text(page, encoding="utf-8")
    return outdir / "report.html"
