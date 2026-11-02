# RRR eBay ↔ Shopify Inventory Sync

Keeps stock counts for **Rescue Retro Relics** the same on eBay and Shopify, and exports eBay orders.

- Pulls every active eBay listing (including variations) and matches it to a Shopify product/variant.
- Shopify holds the real count; eBay always shows 1. An eBay sale comes off Shopify and eBay goes
  back to 1 if there's more; Shopify selling out hides the eBay listing.
- Runs by itself every 15 minutes and can push a notification to your phone.
- Exports eBay orders since 2026-01-01 (or any date up to two years back) to CSV + JSON.
- Pure Python 3.10+ standard library. Nothing to `pip install`.

## Setup

```bash
cp .env.example .env      # then fill in the values; .env is git-ignored
```

### eBay (one time)

```bash
python -m rrr_sync ebay-auth-url
```

Open the link, sign in, click **Agree**. eBay sends you to a page whose address bar contains
`code=...`. Copy the **whole address** and, within 5 minutes:

```bash
python -m rrr_sync ebay-auth-exchange 'https://...code=...'
```

The refresh token is saved to `.secrets/ebay_tokens.json` (git-ignored) and renews access
automatically for about 18 months. When it runs out, repeat these two steps.

### Shopify

Shopify stopped letting stores create new apps under *Settings → Apps → Develop apps* on
January 1, 2026. New apps are made in the **Dev Dashboard** instead:

1. Shopify admin → **Settings → Apps → Develop apps → Build apps in Dev Dashboard**
   (or go to https://dev.shopify.com/dashboard).
2. **Create app** → name it `RRR Inventory Sync`.
3. Create a version with these Admin API scopes: `read_products`, `read_inventory`,
   `write_inventory`, `read_locations`. Release it.
4. **Install** the app on the Rescue Retro Relics store.
5. Copy the **Client ID** and **Client secret** from the app's settings into `.env` as
   `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET`. The tool swaps them for a 24-hour token by itself.

If you already have an older custom app with a `shpat_...` token, put that in
`SHOPIFY_ADMIN_TOKEN` instead.

Then check both connections:

```bash
python -m rrr_sync check
```

## Daily use

```bash
python -m rrr_sync match          # build/refresh data/matches.csv
python -m rrr_sync sync           # dry run: shows what would change
python -m rrr_sync sync --apply   # make the changes (this is what the scheduler runs)
python -m rrr_sync notify-test    # send a test notification to your phone
python -m rrr_sync orders         # eBay orders since 2026-01-01 -> data/
```

### Reviewing matches

Most Shopify products have no SKU, so matching mostly goes by title. `data/matches.csv` has
one row per eBay listing:

| `method`      | meaning                                    | `approved` starts as |
|---------------|--------------------------------------------|----------------------|
| `sku`         | same SKU on both sides                     | `Y`                  |
| `shopify_id`  | eBay SKU is the Shopify product number (listings from the Claude Desktop tool) | `Y` |
| `exact_title` | identical title (ignoring case/punctuation) | `Y`                  |
| `fuzzy`       | close title — **check it**                 | blank                |
| `unmatched`   | nothing close on Shopify                   | blank                |

Put `Y` to sync a row or `N` to never sync it. Only `Y` rows are touched. Running `match`
again keeps your Y/N choices. Rows that need review are listed first.

Tip: giving products the same SKU on both sides makes matching exact.

### The rule: eBay shows 1, Shopify holds the count

Every run, for each matched pair:

| What happened                         | What the tool does                                   |
|---------------------------------------|------------------------------------------------------|
| Sold 1 on eBay, Shopify had 4         | Shopify 4 → 3, eBay back to 1                        |
| Sold the last one on eBay             | Shopify → 0, eBay stays at 0                         |
| Shopify sold its last one             | eBay set to 0 (hidden, not deleted)                  |
| Restocked on Shopify                  | eBay comes back at 1                                 |
| An eBay listing shows more than 1     | trimmed to 1                                         |

eBay sales are counted from eBay's **orders**, once each, only after they're paid. Cancelled
orders are skipped. On the very first run, sales from before that run are treated as already
taken off Shopify by hand, so nothing gets counted twice.

### Turn on eBay's Out-of-stock control (required)

In eBay, go to **Account settings → Selling preferences** and turn on **Out-of-stock control** (eBay calls it "use the out-of-stock option"; if they've moved it again, search that phrase in eBay Help).

With it on, a listing at 0 is just hidden. It keeps its item number, watchers and sales history,
and "relisting" is simply setting it back to 1, which the tool does by itself. With it off, eBay
**ends** the listing at 0, and the tool can only tell you to relist it by hand.

### Run it automatically (Windows)

1. Open **Task Scheduler** → **Create Basic Task** → name it `RRR inventory sync`.
2. Trigger: **Daily**, then in the task's **Properties → Triggers → Edit**, tick
   **Repeat task every 15 minutes** for a duration of **Indefinitely**.
3. Action: **Start a program** → browse to `run_sync.bat` in this folder.
4. In **Properties → General**, tick **Run whether user is logged on or not**.

Each run's output goes to `data\sync_runs.log`.

### Notifications

Everything the sync does is written to `data/activity.log`. To also get it on your phone:

1. Install the free **ntfy** app (iPhone or Android).
2. Subscribe to a long, hard-to-guess topic name, e.g. `rrr-sync-` plus random letters.
   Anyone who knows the name can read the messages, so don't use something obvious.
3. Put that name in `.env` as `NTFY_TOPIC=...`, then run `python -m rrr_sync notify-test`.

You'll get one message per run that did something: sales counted, items sold out or relisted.
These come through as urgent:

- **OVERSOLD**: eBay sold something Shopify already had at 0.
- **NOT MATCHED**: an eBay sale for a listing with no approved Shopify match. Take it off Shopify by hand.
- **ENDED**: an eBay listing ended while Shopify still has stock (Out-of-stock control is probably off).
- **ERROR**: an update failed. The next run tries again.

### Things to know

- Sales are checked every 15 minutes, so if the last unit sells on Shopify and eBay in the same
  15 minutes, you can still oversell. You'll get an OVERSOLD alert if it happens.
- Only the store's one location (13252 Leech Dr) is synced; set `SHOPIFY_LOCATION_ID` to change it.
- Products with inventory tracking turned off in Shopify are skipped.

## Claude Desktop: eBay listing tools (MCP server)

`rrr_ebay_mcp.py` lets Claude create, finish and publish eBay listings straight from Shopify.
Needs Python 3.10+ and `pip install mcp` (1.x or 2.x both work).

| Tool                       | What it does |
|----------------------------|--------------|
| `ebay_check_auth`          | refreshes the token and makes a real eBay call |
| `ebay_get_active_offers`   | every offer, published or not (loops SKU by SKU; eBay has no list-all) |
| `ebay_create_draft`        | Shopify product → eBay inventory item + unpublished offer |
| `ebay_publish_offer`       | attaches photos + RRR policies, then publishes. Photos default to the Shopify product's |
| `ebay_update_price`        | change a listing's price to what you say; live listings update right away |
| `ebay_suggest_category`    | eBay's suggested leaf categories for a product (fixes "not a leaf category") |
| `ebay_get_active_listings` | all live listings, including Seller Hub ones |
| `ebay_get_orders`          | orders since a date |

Built-in rules: **price is whatever you tell Claude**. Nothing is calculated from Shopify, and
Claude asks if you haven't said. Quantity 1, Best Offer on (no floor), condition NEW, SKU = Shopify
product number, policies 284028239015 / 283965435015 / 291780816015 (override with
`EBAY_FULFILLMENT_POLICY_ID` / `EBAY_PAYMENT_POLICY_ID` / `EBAY_RETURN_POLICY_ID`).
eBay needs an inventory location to publish. The first enabled one is used, or set
`EBAY_MERCHANT_LOCATION_KEY`.

Tokens: an old `.tokens.json` next to `rrr_ebay_mcp.py` is picked up automatically, so there's no
need to re-authorize.

Smoke test from a terminal (real eBay calls, nothing changed):

```bash
python rrr_ebay_mcp.py --check
```

Add to `%APPDATA%\Claude\claude_desktop_config.json`, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "rrr-ebay": {
      "command": "python",
      "args": ["C:\\Users\\Steph\\OneDrive\\Desktop\\rrr-ebay-tools\\rrr-ebay-tools\\rrr_ebay_mcp.py"]
    }
  }
}
```

## Files

```
rrr_sync/ebay_auth.py   eBay OAuth: consent link, code exchange, auto-refresh
rrr_sync/ebay.py        active listings + quantity updates (Trading API), orders (Fulfillment API)
rrr_sync/ebay_inventory.py  eBay Inventory API: items, offers (per SKU), publish
rrr_sync/ebay_listings.py   draft/publish logic + RRR pricing and policy rules
rrr_ebay_mcp.py         MCP server for Claude Desktop
rrr_sync/shopify.py     Shopify Admin GraphQL: variants, quantities
rrr_sync/matching.py    eBay ↔ Shopify matching and data/matches.csv
rrr_sync/sync.py        eBay-shows-1 rule: count eBay sales on Shopify, set eBay to 1 or 0
rrr_sync/notify.py      activity log + ntfy phone notifications
run_sync.bat            what Windows Task Scheduler runs
rrr_sync/orders.py      order CSV/JSON export
```

Never committed: `.env`, `.secrets/` (tokens), `data/` (orders, matches, sync state).

Tests: `python -m unittest discover -s tests -t .`

## Other tools in this repo

- **Site audit** (SEO + broken links): double-click `run_site_audit.bat`. See [README_SITE_AUDIT.md](README_SITE_AUDIT.md).
