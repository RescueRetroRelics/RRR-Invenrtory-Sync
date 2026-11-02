# 🐾 RRR Simulated Shopper

Pretend customers shop **rescueretrorelics.com** in a real (hidden) browser and tell you what's broken,
slow, or confusing. You get a report with screenshots and a fix list, most important first.

**It never buys anything.** It never types into checkout, refuses to click anything like "Pay now",
"Buy it now" or "Shop Pay", and stops the moment the checkout page loads. Carts it creates are thrown away.
It needs no passwords or API keys.

## Run it (one step)

Double-click **`run_shopper.bat`** in this folder.

Or, in a terminal in this folder:

```
python -m rrr_shopper
```

The first run installs the browser robot (a few minutes, one time only). A full run takes about 5–10 minutes.
When it finishes, the report opens in your browser.

## What it does

Four shoppers, each on desktop and/or phone:

| Shopper | What they do |
|---|---|
| The Collector | Searches "sonny angel", "re-ment snoopy", "snoopy hippers" |
| The Gift Buyer | Shopping for a Snoopy fan, browses cheapest first |
| The Mobile-Only Shopper | Phone only, searches "hello kitty", "funko pop" |
| The Skeptic | Reads shipping, returns, contact, terms, privacy pages before buying |

Each one goes: home page → search → collection → product → add to cart → cart → checkout opens → **STOP**.

Along the way it checks: errors, broken links and images, page speed, missing alt text, tiny buttons on
phones, sideways scrolling on phones, shipping/returns/contact info, reviews and trust signals, search
results, how sold-out items look, and (optionally) whether a discount code works.

## The report

Saved in `shopper_reports\<date_time>\`:

- `report.html` — open this. Wins, fix list, each shopper's journey with screenshots.
- `summary.txt` — same thing in plain text.
- `findings.json` — raw data.

Every fix is labeled:
- **verified** = the shopper actually saw it happen.
- **hypothesis** = probably a problem; eyeball it before you act.
- ⚡ = matters and is easy to fix. Start there.

## Options

```
python -m rrr_shopper --discount RESCUE10      test a discount code in the cart
python -m rrr_shopper --only collector,skeptic  just some shoppers (faster)
python -m rrr_shopper --watch                   show the browser so you can watch it shop
```

## Re-running after you fix something

Just run it again. Each run gets its own dated folder, so you can compare before and after.

## Changing the shoppers

Open `rrr_shopper\config.py` in Notepad. You can change the search words, add sold-out products to test,
or change what counts as "slow". Save and run again.

## Testing the tool itself (for Claude / developers)

`python tests/mock_store.py 8765` starts a tiny fake store, then
`python -m rrr_shopper --site http://127.0.0.1:8765 --no-open` runs against it.
