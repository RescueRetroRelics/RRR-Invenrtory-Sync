# Shopify app replacement plan

Goal: as few third-party apps as possible, with custom code Stephanie owns.

## What the live storefront actually loads (scanned Nov 5, 2026)
Home, collection, product and cart pages were checked for app scripts.

| App | Seen on storefront? | Notes |
|---|---|---|
| **Judge.me** (reviews) | ✅ every page (`judgeme-845` loader + CSS) | Review widget on product pages |
| **Shop Promise** | ✅ product pages | Built into Shopify, not a third-party app |
| **Hoppy Trust Badges** | ❌ **not rendering anywhere** | Installed but doing nothing |
| **Flow** | (back-end only) | Tags `new item` on new listings, removes after 14 days |

I couldn't see the full installed-app list from here because the Shopify connection was down. Anything installed
that doesn't touch the storefront wasn't visible to this scan.

## Replacements

### Flow → `python -m rrr_sync new-tags` ✅ BUILT
- Same behavior. A product listed in the last 14 days gets `new item`. After 14 days the tag comes off, and the
  product stays in all its other collections. Tag a product `keep-new` to pin it.
- Uses the real tag on the store: **`new item`**, not `new`. All 70 products in
  *Retro Relics Rescued - New Finds* carry it today, aged 8–14 days.
- Runs hourly through `run_new_tags.bat` in Windows Task Scheduler, the same setup as the inventory sync.
  No server and no webhook are needed.
- Dry run by default. 13 tests cover it, and the full suite passes (49 tests).
- **Why a scheduled job instead of a webhook:** a webhook needs a public server that's always on. The hourly job
  runs on the PC that already does the eBay sync, costs nothing, and fixes anything it missed on the next run. The
  only difference from Flow is that new listings get tagged within the hour instead of instantly.
- **Switch-over:** see README → *New Finds tagger*. In short: add the `write_products` scope, do a dry run,
  schedule it, watch for a day, then turn off Flow.

### Hoppy Trust Badges → theme code ✅ BUILT
- The new theme's product page has a trust row (Secure checkout · Every sale helps Detroit rescue dogs · Packed with
  love in Michigan) plus payment icons pulled automatically from the payment methods turned on in Shopify. The
  footer shows the same icons. No app and no extra script.
- **Safe to uninstall now,** since it isn't showing anywhere on the current live site either.

### Judge.me → keep it for now (recommendation)
- Reviews are more than a widget. They involve review-request emails after delivery, moderation, photo uploads,
  spam filtering and Google review stars. Rebuilding all that means running your own email and storage, which
  becomes a second job.
- Ways to shrink its footprint:
  1. The new theme only loads Judge.me where you place its block on the product page (theme editor → Product →
     *Add block* → Judge.me). The theme now has a slot for app blocks.
  2. If Judge.me's "app embed" is turned on, it loads on every page. Turn it off and keep only the product-page
     block, so the homepage and collections get lighter.
  3. Later, if you ever drop it: export your reviews (Judge.me allows CSV export). The theme could show them from
     product metafields with zero scripts, but new reviews would stop coming in.

## Other apps
Send the list of what else is installed (Settings → Apps). Common ones that custom theme code replaces easily:
announcement bars, marquees/tickers, "back in stock" text, size charts, sold-out badges, related products, FAQ
accordions, social icons, countdown timers, image sliders, and "free shipping over $X" bars.
Usually worth keeping as apps: email marketing (Shopify Email is built in and free), reviews, shipping labels,
taxes, and the eBay connection.
