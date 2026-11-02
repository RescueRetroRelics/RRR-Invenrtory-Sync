# 🐾 RRR Site Audit

Checks every page of **rescueretrorelics.com** for SEO problems and broken links, then hands you a
fix list with the most valuable, easiest fixes first.

**It's read-only.** It just looks at your pages the way Google does. It never logs in, never touches
the cart or checkout, and never changes anything. No passwords or API keys needed.

## Run it (one step)

Double-click **`run_site_audit.bat`** in this folder.

Or, in a terminal in this folder:

```
python -m rrr_audit
```

It takes about 5–10 minutes. When it's done, the report opens in your browser. Nothing to install
(it only needs Python, which you already have for the inventory sync).

## What it checks

| Check | Example of what it catches |
|---|---|
| Broken links | A menu or page link that goes to a deleted product (404) |
| Broken images | A picture that no longer loads |
| Menu vs footer | "Terms of Service" going to one page in the menu and a different one in the footer |
| Alt text | About page photos with no description (counts Shopify's blank `alt=""` as missing) |
| Meta descriptions | Missing, too long, too short, copy-pasted, or **cut off mid-sentence** (like the Charizard one) |
| Page titles | Missing, too long, or the same on several pages |
| Headings | No main heading, two main headings, or skipping levels (H2 then H4) |
| Structured data | Broken JSON, product pages without Product data, missing price/stock/brand, and brand showing "Rescue Retro Relics" instead of the real maker |
| Hidden from Google | Pages marked "noindex" |
| Speed | Pages taking over 3 seconds to respond |

## The report

Saved in `site_audit_reports\<date_time>\`:

- **`report.html`** — open this. Fix list, most important first, with "how to fix it in Shopify" steps.
- **`fix_list.csv`** — every problem, one per row, with an empty **done** column. Open in Excel/Sheets and check them off.
- `summary.txt` — the same fix list in plain text.
- `pages.csv` — every page with its title, description, heading, image count.

Every fix is labeled:
- **verified** = the crawler actually saw it on your page.
- **hypothesis** = probably a problem, eyeball it first (e.g. a slow page might just be slow Wi-Fi that minute).
- ⚡ = matters and is easy. Start there.

See `sample_reports\site_audit\` for an example (from a fake practice store, not your real site).

## Re-run after you fix things

Just double-click `run_site_audit.bat` again. The new report tells you **how many problems you fixed
since last time** and how many are new. 🎉 Old reports stay in `site_audit_reports\` so you can compare.

## Options

```
run_site_audit.bat --full           check every single page (all products), no page limit
run_site_audit.bat --max-pages 100  quicker check: the 100 most important pages
run_site_audit.bat --no-external    skip checking links to other websites (Instagram etc.)
```

By default it checks 400 pages: home, your pages and policies, blogs and collections first, then products.

## Good to know

- Links to Facebook/Instagram/eBay sometimes look "broken" because those sites block robots. They're
  marked **hypothesis**: click them yourself before fixing.
- It waits a moment between pages so it never slows down your store for real shoppers.
- Fixing meta descriptions and alt text: product, collection or page in Shopify admin → scroll to
  **Search engine listing** → **Edit**. The report spells out the steps for each problem.
