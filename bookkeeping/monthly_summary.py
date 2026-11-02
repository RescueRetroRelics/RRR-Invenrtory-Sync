"""Monthly bookkeeping summary for Rescue Retro Relics.

Reads the CSV exports you drop in bookkeeping/exports/ and writes a month-by-month
income and expense summary to bookkeeping/reports/.

Read-only: it never connects to Shopify or eBay and never changes your exports.
Every number in the report comes straight from a file. If a file is missing, the
report says "not in your exports" instead of guessing.

Files it understands (it figures out which is which from the column names):
  * Shopify orders export        (Orders > Export)              -> sales, shipping, tax, refunds
  * Shopify Payments transactions (Finances > Payouts > Export)  -> card processing fees
  * eBay transaction report       (Seller Hub > Payments > Reports) -> sales, fees, labels, refunds
  * expenses.csv                  (your own list)                -> everything else you spent

Run:  python bookkeeping/monthly_summary.py            (all months found)
      python bookkeeping/monthly_summary.py --month 2026-10
"""

import argparse
import csv
import html
import io
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


# ---------- small helpers ----------

def money(text):
    """'$1,234.50' -> 1234.5, '(3.00)' -> -3.0, '--' or '' -> None."""
    if text is None:
        return None
    s = str(text).strip()
    if s in ("", "--", "-", "N/A", "n/a"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^0-9.\-]", "", s)
    if s in ("", "-", ".", "-."):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -abs(v) if neg else v


DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
    "%b %d, %Y", "%b %d, %Y %I:%M:%S %p", "%B %d, %Y",
    "%m/%d/%Y", "%m/%d/%y", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%d-%b-%y", "%d-%b-%Y",
)


def month_of(text):
    """Return 'YYYY-MM' for a date string, or None if it can't be read."""
    if not text:
        return None
    s = str(text).strip()
    s = re.sub(r"\s+(PDT|PST|MST|MDT|CST|CDT|EST|EDT|UTC|GMT)$", "", s)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m")
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def norm(h):
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()


def pick(row, *names):
    """Get the first matching column (matching ignores case and punctuation)."""
    for n in names:
        key = row.get("__map__", {}).get(norm(n))
        if key is not None:
            return row.get(key)
    return None


def read_rows(path):
    """Read a CSV, skipping any title lines eBay puts above the real header row."""
    raw = path.read_bytes().decode("utf-8-sig", errors="replace")
    lines = raw.splitlines()
    header_idx = 0
    for i, line in enumerate(lines[:40]):
        n = norm(line)
        if ("transaction creation date" in n or "transaction date" in n or "paid at" in n
                or ("date" in n and "amount" in n) or "payout date" in n or "created at" in n):
            header_idx = i
            break
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    headers = reader.fieldnames or []
    mapping = {norm(h): h for h in headers if h}
    rows = []
    for r in reader:
        r["__map__"] = mapping
        rows.append(r)
    return [norm(h) for h in headers if h], rows


def detect(headers):
    h = set(headers)
    if "transaction creation date" in h and "net amount" in h:
        return "ebay"
    if "financial status" in h and "subtotal" in h and "total" in h:
        return "shopify_orders"
    if "transaction date" in h and "type" in h and "fee" in h and "net" in h:
        return "shopify_payments"
    if "payout date" in h and "fees" in h and "total" in h:
        return "shopify_payouts"
    if "category" in h and "amount" in h and "date" in h:
        return "expenses"
    return None


# ---------- the ledger ----------

class Ledger:
    def __init__(self):
        # lines[month][(section, label)] = amount
        self.lines = defaultdict(lambda: defaultdict(float))
        self.notes = []
        self.sources = defaultdict(list)   # kind -> filenames
        self.skipped_rows = defaultdict(int)

    def add(self, month, section, label, amount):
        if month is None:
            self.skipped_rows["rows with an unreadable date"] += 1
            return
        if amount is None or amount == 0:
            return
        self.lines[month][(section, label)] += amount


def load_shopify_orders(rows, led):
    seen = set()
    for r in rows:
        name = pick(r, "Name")
        if not name or name in seen:
            continue  # extra rows are more line items of the same order
        seen.add(name)
        source = (pick(r, "Source") or "") + " " + (pick(r, "Tags") or "")
        if "ebay" in source.lower():
            led.skipped_rows["Shopify orders tagged eBay (counted from the eBay report instead)"] += 1
            continue
        status = (pick(r, "Financial Status") or "").lower()
        if status in ("pending", "voided", "authorized", "expired"):
            led.skipped_rows[f"Shopify orders not paid ({status})"] += 1
            continue
        month = month_of(pick(r, "Paid at") or pick(r, "Created at"))
        sub = money(pick(r, "Subtotal")) or 0
        disc_note = money(pick(r, "Discount Amount"))
        led.add(month, "income", "Shopify product sales (after discounts)", sub)
        led.add(month, "income", "Shopify shipping charged to buyers", money(pick(r, "Shipping")))
        led.add(month, "pass", "Shopify sales tax collected (owed to the state, not yours)", money(pick(r, "Taxes")))
        led.add(month, "info", "Shopify discounts given", disc_note)
        refunded = money(pick(r, "Refunded Amount"))
        if refunded:
            led.add(month, "income", "Shopify refunds", -abs(refunded))
        led.add(month, "count", "Shopify orders", 1)


def load_shopify_payments(rows, led, have_orders):
    for r in rows:
        month = month_of(pick(r, "Transaction Date"))
        typ = (pick(r, "Type") or "").strip().lower()
        amount, fee, net = money(pick(r, "Amount")), money(pick(r, "Fee")), money(pick(r, "Net"))
        if fee:
            led.add(month, "expense", "Shopify Payments card fees", abs(fee) if fee > 0 else fee)
        if have_orders:
            if typ not in ("charge", "refund", "payout"):
                led.add(month, "review", f"Shopify Payments '{typ}' (net)", net)
            continue
        # No orders export: use payments as a fallback for sales (includes tax + shipping).
        if typ == "charge":
            led.add(month, "income", "Shopify card sales (incl. tax + shipping; no orders export)", amount)
        elif typ == "refund":
            led.add(month, "income", "Shopify refunds", amount if (amount or 0) < 0 else -(amount or 0))
        elif typ != "payout":
            led.add(month, "review", f"Shopify Payments '{typ}' (net)", net)


def load_shopify_payouts(rows, led):
    for r in rows:
        month = month_of(pick(r, "Payout Date"))
        led.add(month, "cash", "Shopify payouts sent to your bank", money(pick(r, "Total")))


EBAY_FEE_COLS = (
    "Final Value Fee - fixed", "Final Value Fee - variable",
    "Very high \"item not as described\" fee", "Below standard performance fee",
    "International fee", "Regulatory operating fee",
)


def load_ebay(rows, led):
    for r in rows:
        typ = (pick(r, "Type") or "").strip().lower()
        month = month_of(pick(r, "Transaction creation date"))
        net = money(pick(r, "Net amount"))
        desc = (pick(r, "Description") or "").strip()
        if typ == "order":
            led.add(month, "income", "eBay item sales", money(pick(r, "Item subtotal")))
            led.add(month, "income", "eBay shipping charged to buyers", money(pick(r, "Shipping and handling")))
            led.add(month, "pass", "eBay sales tax (eBay collects and pays it, not yours)",
                    money(pick(r, "eBay collected tax")))
            led.add(month, "pass", "Sales tax you collected on eBay (owed to the state)",
                    money(pick(r, "Seller collected tax")))
            fees = 0.0
            for col in EBAY_FEE_COLS:
                v = money(pick(r, col))
                if v:
                    fees += abs(v)
            led.add(month, "expense", "eBay selling fees (final value etc.)", fees)
            led.add(month, "count", "eBay orders", 1)
        elif typ == "refund":
            led.add(month, "income", "eBay refunds (net of fee credits)", net)
        elif typ == "shipping label":
            led.add(month, "expense", "eBay shipping labels", -net if net else None)
        elif typ in ("other fee", "charge"):
            label = "eBay other fees" + (f": {desc[:50]}" if desc else "")
            led.add(month, "expense", label, -net if net else None)
        elif typ == "payout":
            led.add(month, "cash", "eBay payouts sent to your bank", abs(net) if net else None)
        elif typ in ("transfer", "hold"):
            led.add(month, "cash", f"eBay {typ}s (money moving, not income)", net)
        elif typ:
            led.add(month, "review", f"eBay '{typ}' (net){': ' + desc[:40] if desc else ''}", net)


def load_expenses(rows, led):
    for r in rows:
        month = month_of(pick(r, "Date"))
        amt = money(pick(r, "Amount"))
        cat = (pick(r, "Category") or "Uncategorized").strip() or "Uncategorized"
        if amt is not None:
            led.add(month, "expense", f"{cat} (your expenses list)", abs(amt))


# ---------- report ----------

SECTIONS = [
    ("income", "INCOME"),
    ("expense", "EXPENSES"),
]
SIDE = [
    ("pass", "Sales tax (not your money, not counted above)"),
    ("cash", "Money moved to your bank (not counted, so nothing is double-counted)"),
    ("review", "Look at these (not counted: they could be income or expense)"),
    ("info", "For your info"),
]


def month_summary(led, month):
    lines = led.lines[month]
    out = {"month": month, "sections": {}}
    for key, _ in SECTIONS + SIDE + [("count", "")]:
        items = sorted(((lbl, amt) for (sec, lbl), amt in lines.items() if sec == key),
                       key=lambda x: -abs(x[1]))
        out["sections"][key] = items
    out["income"] = round(sum(a for _, a in out["sections"]["income"]), 2)
    out["expenses"] = round(sum(a for _, a in out["sections"]["expense"]), 2)
    out["net"] = round(out["income"] - out["expenses"], 2)
    return out


def fmt(v):
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def missing_notes(led):
    have = set(led.sources)
    notes = []
    if "shopify_orders" not in have and "shopify_payments" not in have:
        notes.append("No Shopify export found, so Shopify sales are NOT in this report.")
    elif "shopify_orders" not in have:
        notes.append("No Shopify orders export: Shopify sales come from the payments file, which "
                     "lumps sales tax and shipping in and misses PayPal/other payment methods.")
    if "shopify_payments" not in have:
        notes.append("No Shopify Payments transactions export, so Shopify card fees are NOT included.")
    if "ebay" not in have:
        notes.append("No eBay transaction report found, so eBay is NOT in this report.")
    if "expenses" not in have:
        notes.append("No expenses.csv found, so only platform fees are counted as expenses "
                     "(no supplies, inventory buys, postage you paid outside eBay, etc.).")
    return notes


def write_reports(led, months, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    summaries = [month_summary(led, m) for m in months]
    notes = missing_notes(led)

    # 1) CSV for Excel: one row per line item per month
    csv_path = outdir / f"monthly_summary_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Month", "Section", "Line", "Amount"])
        for s in summaries:
            for key, title in SECTIONS + SIDE:
                for lbl, amt in s["sections"][key]:
                    w.writerow([s["month"], title.split(" (")[0], lbl, f"{amt:.2f}"])
            w.writerow([s["month"], "TOTAL", "Total income", f"{s['income']:.2f}"])
            w.writerow([s["month"], "TOTAL", "Total expenses", f"{s['expenses']:.2f}"])
            w.writerow([s["month"], "TOTAL", "Net profit (income minus expenses)", f"{s['net']:.2f}"])

    # 2) HTML report to read in a browser
    e = html.escape
    parts = [
        "<!doctype html><meta charset='utf-8'><title>RRR Monthly Bookkeeping</title>",
        "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#222}"
        "h1{margin-bottom:4px}table{border-collapse:collapse;width:100%;margin:8px 0 18px}"
        "td{padding:4px 6px;border-bottom:1px solid #eee}td.n{text-align:right;white-space:nowrap}"
        ".tot td{font-weight:bold;border-top:2px solid #333}.net{font-size:1.25em}.pos{color:#18794e}.neg{color:#b42318}"
        ".warn{background:#fff6e0;border:1px solid #f0c36d;padding:8px 12px;border-radius:6px}"
        ".side td{color:#666;font-size:.92em}h2{border-bottom:3px solid #222;padding-bottom:4px;margin-top:36px}"
        "</style>",
        "<h1>Rescue Retro Relics: monthly money summary</h1>",
        f"<p>Made {e(datetime.now().strftime('%b %d, %Y %I:%M %p'))}. Every number comes from your export files. "
        "Nothing is estimated.</p>",
        "<p><b>Files used:</b> " + e("; ".join(f"{k}: {', '.join(v)}" for k, v in led.sources.items()) or "none") + "</p>",
    ]
    if notes:
        parts.append("<div class='warn'><b>Heads up:</b><ul>" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul></div>")
    if led.skipped_rows:
        parts.append("<p><b>Rows skipped on purpose:</b> " +
                     e("; ".join(f"{k}: {v}" for k, v in led.skipped_rows.items())) + "</p>")
    for s in reversed(summaries):
        counts = ", ".join(f"{int(a)} {lbl}" for lbl, a in s["sections"]["count"])
        parts.append(f"<h2>{e(datetime.strptime(s['month'], '%Y-%m').strftime('%B %Y'))}</h2>")
        if counts:
            parts.append(f"<p>{e(counts)}</p>")
        for key, title in SECTIONS:
            parts.append(f"<b>{title}</b><table>")
            for lbl, amt in s["sections"][key]:
                parts.append(f"<tr><td>{e(lbl)}</td><td class='n'>{fmt(amt)}</td></tr>")
            total = s["income"] if key == "income" else s["expenses"]
            parts.append(f"<tr class='tot'><td>Total {title.lower()}</td><td class='n'>{fmt(total)}</td></tr></table>")
        cls = "pos" if s["net"] >= 0 else "neg"
        parts.append(f"<p class='net'><b>Net profit: <span class='{cls}'>{fmt(s['net'])}</span></b></p>")
        for key, title in SIDE:
            if s["sections"][key]:
                parts.append(f"<b style='color:#666'>{e(title)}</b><table class='side'>")
                for lbl, amt in s["sections"][key]:
                    parts.append(f"<tr><td>{e(lbl)}</td><td class='n'>{fmt(amt)}</td></tr>")
                parts.append("</table>")
    parts.append("<p style='color:#888;font-size:.85em'>This is a bookkeeping helper, not tax advice. "
                 "Hand the CSV to your tax person if they want the detail.</p>")
    html_path = outdir / f"monthly_summary_{stamp}.html"
    html_path.write_text("\n".join(parts), encoding="utf-8")
    return csv_path, html_path, summaries, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description="Monthly income and expense summary from Shopify + eBay exports.")
    ap.add_argument("--exports", default=str(HERE / "exports"), help="folder with your CSV exports")
    ap.add_argument("--out", default=str(HERE / "reports"), help="folder to write the report to")
    ap.add_argument("--month", help="only this month, like 2026-10")
    args = ap.parse_args(argv)

    exports = Path(args.exports)
    files = sorted(p for p in exports.glob("*.csv")) if exports.exists() else []
    if not files:
        print(f"No CSV files found in {exports}. Put your exports there (see README) and run again.")
        return 1

    led = Ledger()
    loaded = defaultdict(list)
    for p in files:
        headers, rows = read_rows(p)
        kind = detect(headers)
        if not kind:
            print(f"  skipped {p.name}: didn't recognize its columns ({', '.join(headers[:6])}...)")
            continue
        loaded[kind].append((p, rows))
        led.sources[kind].append(p.name)
        print(f"  read {p.name} as {kind.replace('_', ' ')} ({len(rows)} rows)")

    have_orders = "shopify_orders" in loaded
    for _, rows in loaded.get("shopify_orders", []):
        load_shopify_orders(rows, led)
    for _, rows in loaded.get("shopify_payments", []):
        load_shopify_payments(rows, led, have_orders)
    for _, rows in loaded.get("shopify_payouts", []):
        load_shopify_payouts(rows, led)
    for _, rows in loaded.get("ebay", []):
        load_ebay(rows, led)
    for _, rows in loaded.get("expenses", []):
        load_expenses(rows, led)

    months = sorted(led.lines)
    if args.month:
        months = [m for m in months if m == args.month]
    if not months:
        print("No dated rows found" + (f" for {args.month}" if args.month else "") + ".")
        return 1

    csv_path, html_path, summaries, notes = write_reports(led, months, Path(args.out))
    print()
    for s in summaries:
        print(f"{s['month']}:  income {fmt(s['income']):>12}   expenses {fmt(s['expenses']):>12}   net {fmt(s['net']):>12}")
    for n in notes:
        print(f"  NOTE: {n}")
    print(f"\nReport: {html_path}\nSpreadsheet: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
