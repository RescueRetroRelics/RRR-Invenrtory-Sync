# Monthly Bookkeeping Summary

Turns your Shopify and eBay exports into a month-by-month **income, expenses, and net profit** report.

- **Read-only.** It never logs into Shopify or eBay and never changes anything. It only reads CSV files you download.
- **No made-up numbers.** Every line comes straight from your files. If a file is missing, the report says so at the top instead of guessing.
- Nothing to install except Python (which you already have for the sync tool).

## What you get

Two files in `bookkeeping\reports\`:

- `monthly_summary_<date>.html`: the report. Opens in your browser automatically.
- `monthly_summary_<date>.csv`: same numbers as a spreadsheet, for Excel or your tax person.

See a sample: `sample_report/monthly_summary_SAMPLE.html` (made from fake data in `sample_exports/`).

Each month shows:

| Section | What's in it |
|---|---|
| **Income** | Shopify sales + shipping you charged, eBay sales + shipping you charged, minus refunds |
| **Expenses** | Shopify card fees, eBay selling fees, eBay shipping labels, eBay ad/other fees, plus your own expenses list |
| **Net profit** | Income minus expenses |
| Side notes (not counted) | Sales tax (it's the state's money), payouts to your bank (counting them would double-count), and anything odd to look at |

## Step 1: Download your exports (once a month)

Pick the same date range in each (for example, Oct 1 to Oct 31). Save every file into
**`bookkeeping\exports\`**. File names don't matter; the tool figures out which is which.

**1. Shopify orders** (your sales)
Shopify admin → **Orders** → **Export** (top right) → choose **Orders by date** and your date range →
**Plain CSV file** → **Export orders**. Shopify emails it to you if it's big. Save the CSV.

**2. Shopify Payments transactions** (your card fees)
Shopify admin → **Finance** → **Payouts** → **View transactions** (or the Transactions tab) →
set the date range → **Export** → CSV.

**3. eBay transaction report** (sales, fees, labels, refunds)
eBay **Seller Hub** → **Payments** tab → **Reports** → **Create report** → **Transaction report** →
pick your date range → **Create**. When it says it's ready, click **Download** (CSV).

(Menu names can shift a little when Shopify or eBay update their sites. If something isn't
where this says, look for "Export" or "Reports" on that page.)

**4. Your other expenses** (optional but you want it)
Copy `expenses_template.csv` into `exports\` as `expenses.csv` and add a row for each thing you paid for:
inventory buys, mailers and tape, your Shopify plan, apps, postage you bought outside eBay, etc.

```
Date,Category,Amount,Description,Paid with
2026-10-02,Shipping supplies,18.99,Bubble mailers,Debit card
2026-10-15,Inventory bought,25.00,Thrift haul,Cash
```

Use the same category names every month so they total up together.

## Step 2: Run it

**Easiest:** double-click **`run_bookkeeping.bat`** in the main folder. The report opens when it's done.

**Or** in a terminal in the main folder:

```
python bookkeeping\monthly_summary.py
```

Only one month: `python bookkeeping\monthly_summary.py --month 2026-10`

## Re-running after changes

Just drop the new or updated CSVs into `bookkeeping\exports\` and run it again. Each run makes a new
dated report, so old ones stay put. If you re-download the same month, delete the old copy first
(otherwise that month counts twice).

Your real exports and reports stay on your computer. They are git-ignored and never uploaded.

## Good to know

- **Refunds** from Shopify are counted in the month the order was paid (that's how Shopify's export
  lists them). eBay refunds land in the month they happened.
- **No Shopify orders export?** The tool falls back to the payments file, but that lumps sales tax and
  shipping into sales and misses PayPal. The report warns you when this happens.
- **Shopify orders tagged "eBay"** are skipped so nothing counts twice.
- **"Skipped: didn't recognize its columns"** means a file isn't one of the four above. Send it to Claude
  and the tool can learn it.
- This is a bookkeeping helper, not tax advice.

## Test it

```
python -m unittest tests.test_bookkeeping
```
