# eBay Repricer

Finds eBay listings that have been sitting too long and suggests lower prices so they sell faster,
**without ever going below break-even** (your cost + eBay's real fees + a minimum profit).

**It never changes anything on eBay.** It makes a preview you look at, plus upload files you send
to eBay yourself, 50 listings at a time.

## Run it (Windows)

**Option A: from an eBay export (works anywhere, no sign-in needed)**

1. eBay **Seller Hub → Listings → Active**, click **Download** (CSV) and save it.
2. Drag that CSV file onto **`run_reprice.bat`**.
3. The preview opens in your browser.

**Option B: read eBay live** (only in a folder where the eBay sign-in is already set up):
double-click **`run_reprice.bat`**.

From a terminal it's one command:

```
python -m rrr_sync reprice --file "C:\path\to\ebay-export.csv"
```

Add costs so the floor protects your real margin (highly recommended):

```
python -m rrr_sync reprice --file ebay-export.csv --costs shopify-products.csv
```

The costs file can be the Shopify product export (**Products → Export**, it has "Cost per item"),
or any CSV with a `cost` column plus `item_id`, `sku` or `title`. Without costs, the floor only
covers eBay's fees + $3, so a cheap price could still be a loss.

## What you get

Everything lands in `data\reprice\<date_time>\`:

| File | What it is |
|---|---|
| `preview.html` | Old vs new price for every listing. Open this first. |
| `preview.csv` | Same thing as a spreadsheet. |
| `upload_batch_01.csv`, `02`… | Up to 50 price changes each, ready for eBay. |
| `undo_batch_01.csv`, `02`… | The old prices for the same listings, to put a batch back. |
| `listings_backup.csv` | Every listing and price before any change. |
| `summary.txt` | The counts. |

Colors/status in the preview:

- **LOWER** (green): price goes down.
- **LOWER_TO_FLOOR** (yellow): would have gone lower, stopped at your break-even.
- **BELOW_FLOOR** (red): already priced under break-even. Not touched. Look at these.
- **KEEP**: too new, or already at the target.
- **SKIP**: auctions, listings with bids, listings with variations (sizes/colors), out of stock.

## Upload a batch to eBay

1. Look over `preview.html`. If a price looks wrong, delete that row from the upload file (Excel is fine, save as CSV).
2. eBay **Seller Hub → Reports → Uploads → Upload template**, choose `upload_batch_01.csv`.
3. Wait for eBay's results file and check it. Then do the next batch.
4. Changed your mind? Upload the matching `undo_batch_01.csv`.

If eBay rejects the file format, send Claude the error from eBay's results file.

## The rules (change them in `reprice_rules.json`)

| Listed for | Cut from the original price |
|---|---|
| under 79 days | none |
| 79+ days | 10% |
| 107+ days | 20% |
| 160+ days | 30% |

These days come from the sale tier report built on your own sales (Nov 2026). They're a starting
rule (hypothesis), so tweak them once you see how batches sell.

- **3+ watchers** → the next-smaller cut. Someone wants it, so send them an offer too.
- **Re-Ment, pins and Hippers** sell well for you, so they wait one tier longer
  (`wait_one_tier_longer` in the rules file).
- **Cuts never stack.** The tool remembers each listing's original price
  (`data\reprice\original_prices.csv`), so running it twice in a row suggests nothing new.
  A listing only drops again when it reaches the next age tier.
- **Floor** = cost + $3.00 profit + eBay's fees, never under $4.99. Fees are set to eBay's standard
  13.6% + $0.40 per order ($0.30 on orders of $10 or less), charged on the price plus about 6% sales tax.
  **Check these against your own eBay fees** (Seller Hub → Payments → Reports) and change them in
  `reprice_rules.json` if they differ. Using Promoted Listings? Put the ad rate in
  `promoted_listing_percent`. Free shipping? Put your average label cost in `shipping_you_pay_per_item`.
- Prices end in .49 or .99.

## Re-running after changes

Just run it again with a fresh eBay export (download a new one after each upload). It picks up where
you left off: listings you already lowered stay put until they hit the next age tier. To change how
aggressive it is, edit the numbers in `reprice_rules.json` and run again.

## Sample

`samples\sample_output\` shows a run on made-up sample listings (`samples\sample_ebay_export.csv`
and `samples\sample_costs.csv`). These are not your real prices.
