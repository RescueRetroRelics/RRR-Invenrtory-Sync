# Weekly Business Snapshot

One page that shows how last week went: sales, orders and items sold (Shopify, eBay and both),
compared with the week before and your 4-week average, plus your top sellers and your slow movers.

It only **reads** files. It never changes anything on Shopify or eBay.

Sample (made from fake data): `samples/sample_output/snapshot_2026-11-01.html`. Download it and open it in your browser.

## What to export (about 5 minutes, once a week)

Make a folder called `exports` in this folder (the tool makes it the first time you run it).
Save these files into it. File names don't matter; the tool figures out what each one is.

| File | Where to get it | Needed? |
|------|-----------------|---------|
| Shopify orders | Shopify admin → **Orders** → **Export** → "Orders by date" for the **last 90 days** → **CSV for Excel** → Export. Shopify emails you the file. | Yes |
| Shopify products | Shopify admin → **Products** → **Export** → "All products" → **CSV for Excel**. Emailed to you. | For slow movers |
| eBay orders | eBay **Seller Hub** → **Orders** → **Download report** → last 90 days → CSV. *Skip this if the inventory sync is connected to eBay: the snapshot pulls eBay orders by itself.* | Yes, unless eBay is connected |
| eBay active listings | eBay **Seller Hub** → **Listings** → **Active** → **Download report**. | Optional: adds "days on eBay" and watchers to slow movers |

Tip: export 90 days of orders, not 7. The tool needs the older weeks for the comparisons and
the slow-mover check.

## Run it

**Double-click `weekly_snapshot.bat`.** Your report opens in your browser when it's done.

Or in a terminal in this folder:

```
python -m rrr_snapshot
```

That's it. It reports the last full Monday-to-Sunday week.

Options, if you ever want them:

```
python -m rrr_snapshot --week-ending 2026-10-25   # a different week (give the Sunday)
python -m rrr_snapshot --slow-days 90             # "slow" = no sale in 90 days instead of 60
python -m rrr_snapshot --no-ebay-pull             # only use the files in exports/
```

## What you get

In the `reports` folder:

- `latest.html`: the newest snapshot (also saved as `snapshot_<date>.html`, so old weeks stay)
- `top_sellers_30d_<date>.csv`: every item sold in the last 30 days, best first
- `slow_movers_<date>.csv`: every in-stock item with no sale in 60 days, biggest money tied up first

## How to read it

- **Sales** = item price × quantity, after item discounts. No shipping, tax or fees.
- Cancelled, voided and fully refunded orders are left out.
- The numbers come straight from your exports, so they're **verified**.
- **Slow movers are a flag, not a verdict (hypothesis).** Shopify's product export has no "date listed",
  so something you listed last week can show up as slow. The eBay active-listings export fixes that
  by adding how many days each item has been on eBay.
- The report lists any "Heads up" notes at the bottom, like an export that doesn't go back far enough.

## Re-running after changes

Every week: export fresh files into `exports/` (it's fine to delete or leave last week's; repeated
orders are only counted once), then double-click `weekly_snapshot.bat` again.

If you've changed prices or listed new things, export Products again too so slow movers are current.

## Needs

Python 3.10 or newer (the same one the inventory sync uses). Nothing to install.
Your export files and reports stay on your computer: `exports/` and `reports/` are never uploaded to GitHub.
