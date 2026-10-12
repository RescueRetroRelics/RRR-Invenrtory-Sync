# RRR eBay ↔ Shopify Inventory Sync

Keeps stock counts for **Rescue Retro Relics** the same on eBay and Shopify, and exports eBay orders.

- Pulls every active eBay listing (including variations) and matches it to a Shopify product/variant.
- Two-way sync: a sale on either side comes off the shared count on both.
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
python -m rrr_sync sync --apply   # make the changes
python -m rrr_sync orders         # eBay orders since 2026-01-01 -> data/
```

### Reviewing matches

Most Shopify products have no SKU, so matching mostly goes by title. `data/matches.csv` has
one row per eBay listing:

| `method`      | meaning                                    | `approved` starts as |
|---------------|--------------------------------------------|----------------------|
| `sku`         | same SKU on both sides                     | `Y`                  |
| `exact_title` | identical title (ignoring case/punctuation) | `Y`                  |
| `fuzzy`       | close title — **check it**                 | blank                |
| `unmatched`   | nothing close on Shopify                   | blank                |

Put `Y` to sync a row or `N` to never sync it. Only `Y` rows are touched. Running `match`
again keeps your Y/N choices. Rows that need review are listed first.

Tip: giving products the same SKU on both sides makes matching exact.

### How the sync decides the number

The tool remembers the count after the last sync. Next time, whatever sold on eBay **and**
whatever sold on Shopify both come off it, and both sides are set to the result. Restocks
work the same way in reverse. The first sync of a pair uses the **lower** of the two counts
so nothing oversells (`--initial ebay` or `--initial shopify` to pick a side instead).

Shopify updates use a compare-and-swap check, so if a sale lands mid-sync the update is
refused rather than overwriting it; the next run picks it up.

### Things to know

- If an eBay listing hits 0 and **Out-of-stock control** is off in eBay's selling
  preferences, eBay ends the listing. Turn that setting on to keep listings alive at 0.
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
| `ebay_get_active_listings` | all live listings, including Seller Hub ones |
| `ebay_get_orders`          | orders since a date |

Built-in rules: price = Shopify + $2.00, Best Offer on (no floor), condition NEW, SKU = Shopify
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
rrr_sync/sync.py        two-way quantity reconciliation
rrr_sync/orders.py      order CSV/JSON export
```

Never committed: `.env`, `.secrets/` (tokens), `data/` (orders, matches, sync state).

Tests: `python -m unittest discover -s tests -t .`
