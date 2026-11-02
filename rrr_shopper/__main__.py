"""Run:  python -m rrr_shopper        (see README_SHOPPER.md)"""

import argparse
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from . import config, report
from .shopper import Findings, Run, SafetyStop, journey, load_store_info, store_checks, trust_tour

SAFETY = "Dry run: no purchases, nothing typed into checkout, stopped as soon as checkout loaded."


def main():
    ap = argparse.ArgumentParser(description="Pretend customers shop rescueretrorelics.com and report what breaks.")
    ap.add_argument("--site", default=os.environ.get("RRR_SHOP_URL", config.BASE_URL), help="store address")
    ap.add_argument("--discount", default=os.environ.get("RRR_TEST_DISCOUNT_CODE", ""), help="discount code to test in the cart")
    ap.add_argument("--only", default="", help="comma list of personas: collector,gift,mobile,skeptic")
    ap.add_argument("--watch", action="store_true", help="show the browser window while it shops")
    ap.add_argument("--no-open", action="store_true", help="don't open the report when done")
    a = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright isn't installed. Run:  pip install playwright  then  python -m playwright install chromium")

    base = a.site.rstrip("/")
    started = datetime.now().strftime("%Y-%m-%d %H:%M")
    outdir = Path("shopper_reports") / datetime.now().strftime("%Y-%m-%d_%H%M")
    (outdir / "screenshots").mkdir(parents=True, exist_ok=True)
    personas = [p for p in config.PERSONAS if not a.only or p["key"] in a.only.split(",")]

    findings = Findings()
    runs = []
    print(f"🐾 Shopping {base} (dry run, never pays)")
    with sync_playwright() as pw:
        launch = {"headless": not a.watch}
        if os.environ.get("RRR_CHROMIUM_PATH"):
            launch["executable_path"] = os.environ["RRR_CHROMIUM_PATH"]
        browser = pw.chromium.launch(**launch)
        store = load_store_info(pw.request.new_context(), base)
        print(f"   Found {len(store['in_stock'])} in-stock and {len(store['sold_out'])} sold-out products in the public list")
        try:
            for p in personas:
                for device in p["devices"]:
                    run = Run(browser, pw, base, p, device, outdir, findings)
                    runs.append(run)
                    print(f" → {run.who}: {p['goal']}")
                    try:
                        if p.get("trust_pages"):
                            trust_tour(run)
                        journey(run, store)
                    except SafetyStop as s:
                        print(f"   🛑 SAFETY STOP: {s}")
                        run.record("SAFETY STOP", False, str(s))
                    except RuntimeError as e:
                        sys.exit(f"   {e}")
                    except Exception as e:
                        run.record("Unexpected problem", False, str(e).splitlines()[0][:150])
                        print(f"   ⚠️  {str(e).splitlines()[0][:150]}")
                    finally:
                        run.errors_report()
                        run.close()
                    good = sum(s["ok"] for s in run.steps)
                    print(f"   {good}/{len(run.steps)} steps OK")
            print(" → Store-wide checks: sold-out items, broken links, discount code")
            runs.append(store_checks(browser, pw, base, outdir, findings, store, a.discount))
        finally:
            browser.close()

    path = report.write(outdir, base, runs, findings, started, SAFETY)
    ranked = findings.ranked()
    print(f"\n✅ {len(findings.wins)} things working · 🔧 {len(ranked)} things to fix")
    for i, f in enumerate(ranked[:5], 1):
        print(f"   {i}. {f['title']}  [{f['status']}]")
    print(f"\nFull report: {path.resolve()}")
    if not a.no_open:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
