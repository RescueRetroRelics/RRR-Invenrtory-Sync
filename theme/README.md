# RRR Relic: the Rescue Retro Relics Shopify theme

A custom Online Store 2.0 theme written from scratch for Rescue Retro Relics. You own all of it.

- **Fast by design:** one ~20KB stylesheet, one ~3KB script, system fonts for body text, inline SVG icons.
- **Never lazy-loads the important images:** the Bailee banner, the main product photo, and the first row of
  collection products load immediately with `fetchpriority="high"` on the single most important one.
- **Works without JavaScript:** mega menu, mobile menu, filters, sorting, cart, and add-to-cart are plain HTML.
- **SEO:** canonical URLs, Open Graph/Twitter tags, Product + Article + BreadcrumbList + OnlineStore + WebSite schema.

## Pages
| Page | Section file |
|---|---|
| Home (Bailee banner, featured collection, categories, mission) | `templates/index.json` |
| Collection (filters, sort, pagination) | `sections/main-collection.liquid` |
| Product | `sections/main-product.liquid` + `related-products.liquid` |
| Meet the Team | `templates/page.meet-the-team.json`, assign to a page with handle `meet-the-team` |
| Cart, search, blog, article, 404, password, gift card | `sections/main-*.liquid` |

## Mega menu
Built from **Online Store > Navigation > Main menu**. Any top-level link with sub-links opens a full-width
panel; each sub-link becomes a column and its own sub-links list under it.

## Checking the code
Shopify's Theme Check runs clean (0 offenses). Re-run with `@shopify/theme-check-node`.
