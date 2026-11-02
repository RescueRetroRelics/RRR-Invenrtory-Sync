# Sale Tier Report (pricing and sale analyzer)

Looks at what sold, how fast, and at what price, then suggests which items to put on sale
and how much off. **Read-only.** It never changes a price on Shopify or eBay. It hands you a
report and a spreadsheet; you decide.

Every number in the report is labeled:

- **VERIFIED**: counted straight from your sales and products.
- **HYPOTHESIS**: a rule built on those counts (the tiers, the discount effect). Test it, don't trust it blindly.

## Run it (one command)

Double-click **`run_sale_analyzer.bat`**. Or in a terminal in this folder:

```
python -m sale_analyzer
```

It uses the same `.env` as the inventory sync, so if the sync works, this works. It reads:

| Data | Where it comes from |
|---|---|
| Products, prices, stock, date added, cost | Shopify (automatic) |
| eBay sales | eBay (automatic) |
| Shopify sales | Shopify (automatic) **if** the app has the `read_orders` permission. If not, see below. |

The report opens in your browser when it's done.

### If it says it can't read Shopify orders

Either one fixes it:

- **Easiest:** Shopify admin → **Orders** → **Export** → *All orders*, *CSV for Excel* → save the file into
  `data\sale_analyzer_inputs\` (any name). Re-run.
- **Once and done:** in the Shopify Dev Dashboard app ("RRR Inventory Sync"), add the `read_orders`
  scope to a new version, release it, and approve it on the store.

You can also drop any of these into `data\sale_analyzer_inputs\` and it picks them up by itself:
a Shopify orders export, a Shopify products export, or the eBay orders file from
`python -m rrr_sync orders`. Add `--offline` to use only those files.

## What you get (in `data\sale_analyzer\`)

| File | What it is |
|---|---|
| `sale_report.html` | The report. Tiers, what sells by price and type, stock age, monthly sales. |
| `sale_tier_suggestions.csv` | Every in-stock item: tier, price now, suggested price, why. Sort or filter in Excel. |
| `sales_matched.csv` | Every sale and which Shopify product it was tied to. Check it if a number looks off. |

A sample made from fake data is in `samples/sale_analyzer/output/`.

## How the tiers work

Each item's clock is **days without a sale**: days since it was added, or since its last sale if it has sold.

| Tier | When | Default discount |
|---|---|---|
| Fresh | under tier 1 days | none |
| Tier 1: nudge | tier 1 days | 10% |
| Tier 2: sale | tier 2 days | 20% |
| Tier 3: clearance | tier 3 days | 30% |

The tier days are **learned from your own sales**: tier 1 starts when an item is slower than 75% of
the items that sold, tier 2 slower than 90%. With fewer than 20 dated sales it uses 60 / 120 / 180.

Safety rails:
- Never below **cost + $3** (when the cost is filled in on Shopify) or **$4.99**.
- Product types that sell much faster than average (marked 🔥) wait one tier longer.
- Prices end in .99.

### Change the rules

Copy `sale_tiers.example.json` to `sale_tiers.json` and edit the numbers. For example, deeper cuts:
`"tier_discounts_pct": {"1": 15, "2": 25, "3": 40}`. Fixed days instead of learned ones:
`"tier_days": [60, 120, 180]`. Anything you leave out keeps its default.

## Re-run after changes

Just run it again. Good rhythm: put one tier on sale, wait 2-3 weeks, re-run, and compare.
Did those items sell? The "units sold per month" table and the item list will show it.
Old reports are overwritten, so rename the folder first if you want to keep one.

## Things to know

- "Days to sell" counts from the day a product was added to **Shopify**. Items listed on eBay
  before Shopify existed (before May 2026) really took longer than shown.
- eBay sales are tied to Shopify products by title. Sold-out items that were deleted from Shopify,
  or eBay-only items, can't be tied and are left out of the tiers (the report says how many).
- eBay order totals can include shipping, so eBay revenue is approximate. Tiers only use dates and counts.
- To actually change prices, use the suggestions CSV with the eBay repricing tool or Shopify bulk editor,
  in batches of about 50, after a preview.
