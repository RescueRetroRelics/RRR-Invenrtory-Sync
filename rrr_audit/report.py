"""Write the audit report: report.html (open this), fix_list.csv (work through it), summary.txt, pages.csv."""
import csv
import html
import json
from pathlib import Path

EFFORT = {1: "easy", 2: "medium", 3: "harder"}
IMPACT = {1: "low", 2: "medium", 3: "high"}


def previous_run(reports_dir, current):
    runs = sorted(d for d in Path(reports_dir).glob("*") if d.is_dir() and d != current and (d / "findings.json").exists())
    return runs[-1] if runs else None


def compare(prev_dir, findings):
    if not prev_dir:
        return None
    old = json.loads((prev_dir / "findings.json").read_text(encoding="utf-8"))
    noisy = ("slow_page", "duplicate_title", "duplicate_meta_description")
    key = lambda f: (f["code"], f["url"], "" if f["code"] in noisy else f.get("detail", ""))
    old_keys, new_keys = {key(f) for f in old}, {key(f) for f in findings}
    return {"since": prev_dir.name, "fixed": len(old_keys - new_keys), "new": len(new_keys - old_keys)}


def write_all(out_dir, site, pages, fixes, findings, meta):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "findings.json").write_text(json.dumps(findings, indent=1), encoding="utf-8")

    with open(out_dir / "fix_list.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["priority", "issue", "impact", "effort", "verified_or_hypothesis", "page", "detail", "how_to_fix", "done"])
        for n, fx in enumerate(fixes, 1):
            for item in fx["items"]:
                w.writerow([n, fx["title"], IMPACT[fx["impact"]], EFFORT[fx["effort"]], fx["label"],
                            item["url"], item.get("detail", ""), fx["how"], ""])

    with open(out_dir / "pages.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "status", "seconds", "title", "title_length", "meta_description", "meta_length", "h1",
                    "images", "images_missing_alt", "structured_data_types"])
        for p in sorted(pages.values(), key=lambda p: p.url):
            h1 = " | ".join(t for l, t in p.headings if l == 1)
            w.writerow([p.url, p.status, p.seconds, p.title, len(p.title), p.meta_description or "",
                        len(p.meta_description or ""), h1, len(p.images),
                        sum(1 for i in p.images if i.alt is None and not i.tiny), ", ".join(sorted(p.schema_types()))])

    (out_dir / "summary.txt").write_text(summary_text(site, fixes, meta), encoding="utf-8")
    (out_dir / "report.html").write_text(html_report(site, fixes, meta), encoding="utf-8")
    return out_dir / "report.html"


def summary_text(site, fixes, meta):
    lines = [f"RRR Site Audit - {site}", f"Run: {meta['when']}   Pages checked: {meta['pages']}   Links/images checked: {meta['links']}", ""]
    if meta.get("compare"):
        c = meta["compare"]
        lines += [f"Since last run ({c['since']}): {c['fixed']} problems FIXED, {c['new']} new.", ""]
    if meta.get("skipped"):
        lines += [f"Note: {meta['skipped']} more pages weren't checked (page limit). Run with --full to check everything.", ""]
    good = meta.get("wins", [])
    if good:
        lines += ["Already good:"] + [f"  + {w}" for w in good] + [""]
    lines.append("FIX LIST (do these in order; ⚡ = quick win):")
    for n, fx in enumerate(fixes, 1):
        flag = "⚡ " if fx["quick_win"] else ""
        lines.append(f"{n:>2}. {flag}{fx['title']} - {fx['count']} found on {fx['pages']} page(s) "
                     f"[{IMPACT[fx['impact']]} impact, {EFFORT[fx['effort']]}, {fx['label']}]")
        lines.append(f"     How: {fx['how']}")
        for item in fx["items"][:3]:
            lines.append(f"     e.g. {item['url']}  {item.get('detail', '')[:110]}")
    if not fixes:
        lines.append("Nothing found. Clean bill of health!")
    return "\n".join(lines) + "\n"


def html_report(site, fixes, meta):
    e = html.escape
    quick = sum(1 for f in fixes if f["quick_win"])
    cards = []
    for n, fx in enumerate(fixes, 1):
        rows = "".join(
            f"<tr><td><a href='{e(i['url'])}' target='_blank'>{e(i['url'].split('://', 1)[-1])}</a></td><td>{e(i.get('detail', ''))}</td></tr>"
            for i in fx["items"][:200])
        more = f"<p class='muted'>…and {len(fx['items']) - 200} more in fix_list.csv</p>" if len(fx["items"]) > 200 else ""
        cards.append(f"""
<details class="fix {'qw' if fx['quick_win'] else ''}" {'open' if n <= 3 else ''}>
<summary><span class="n">{n}</span> {'⚡ ' if fx['quick_win'] else ''}{e(fx['title'])}
 <span class="pill">{fx['count']} on {fx['pages']} page(s)</span>
 <span class="pill i{fx['impact']}">{IMPACT[fx['impact']]} impact</span>
 <span class="pill">{EFFORT[fx['effort']]}</span>
 <span class="pill {fx['label']}">{fx['label']}</span></summary>
<p><b>Why it matters:</b> {e(fx['why'])}</p>
<p><b>How to fix:</b> {e(fx['how'])}</p>
<table><tr><th>Page</th><th>What we saw</th></tr>{rows}</table>{more}
</details>""")
    comp = ""
    if meta.get("compare"):
        c = meta["compare"]
        comp = f"<div class='win'>🎉 Since last run ({e(c['since'])}): <b>{c['fixed']} problems fixed</b>, {c['new']} new.</div>"
    wins = "".join(f"<li>{e(w)}</li>" for w in meta.get("wins", []))
    skipped = (f"<p class='muted'>{meta['skipped']} more pages weren't checked because of the page limit. "
               f"Run <code>run_site_audit.bat --full</code> to check every page.</p>") if meta.get("skipped") else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RRR Site Audit</title><style>
body{{font-family:system-ui,Segoe UI,sans-serif;max-width:1000px;margin:0 auto;padding:16px;background:#fbf8f3;color:#222}}
h1{{margin-bottom:0}} .muted{{color:#777}} .stats{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}
.stat{{background:#fff;border:1px solid #e5ddd0;border-radius:10px;padding:10px 16px}} .stat b{{font-size:1.6em;display:block}}
.fix{{background:#fff;border:1px solid #e5ddd0;border-radius:10px;margin:10px 0;padding:10px 14px}}
.fix.qw{{border-left:6px solid #e0a100}} summary{{cursor:pointer;font-weight:600}} .n{{display:inline-block;width:1.6em;color:#999}}
.pill{{font-weight:400;font-size:.8em;background:#eee;border-radius:20px;padding:2px 8px;margin-left:4px;white-space:nowrap}}
.i3{{background:#f8d0cc}} .i2{{background:#fde8c4}} .verified{{background:#d4edda}} .hypothesis{{background:#e2e3f3}}
table{{border-collapse:collapse;width:100%;font-size:.85em}} td,th{{border-top:1px solid #eee;padding:4px;text-align:left;vertical-align:top;word-break:break-word}}
.win{{background:#d4edda;border-radius:10px;padding:10px 14px;margin:10px 0}}
</style></head><body>
<h1>🐾 Site audit: {e(site)}</h1><p class="muted">{e(meta['when'])}. Read-only check, nothing on your store was changed.</p>
{comp}
<div class="stats"><div class="stat"><b>{meta['pages']}</b>pages checked</div><div class="stat"><b>{meta['links']}</b>links &amp; images checked</div>
<div class="stat"><b>{len(fixes)}</b>kinds of problems</div><div class="stat"><b>{quick}</b>⚡ quick wins</div></div>
{skipped}
{'<h2>Already good</h2><ul>' + wins + '</ul>' if wins else ''}
<h2>Fix list</h2><p class="muted">Most valuable and easiest first. ⚡ = matters and is easy. <b>verified</b> = the crawler saw it.
<b>hypothesis</b> = probably a problem, eyeball it first. Every row is also in <code>fix_list.csv</code> with a "done" column.</p>
{''.join(cards) or '<p>Nothing found. Clean bill of health! 🎉</p>'}
</body></html>"""
