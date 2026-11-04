# RRR Bailee: your own Shopify theme

A custom, lightweight Shopify theme for Rescue Retro Relics. You own every line of it. It's saved here on GitHub, so you can re-upload it any time or take it somewhere else.

- **Base:** Shopify's open-source "Skeleton" starter theme (not Dawn). Rebuilt from scratch on top of it.
- **In your store:** Online Store > Themes > **"RRR Bailee (Claude custom) - Nov 2026"** (unpublished)
- **Theme ID:** 191450054952

## Preview it on your phone (nothing goes live)
https://rescueretrorelics.com/?preview_theme_id=191450054952

## What's inside
| Page | What it does |
|---|---|
| Home | Full-width hero for Bailee's memorial artwork, New Finds, 8 collection tiles, mission banner, gift sets |
| Header | Mega menu built from your **Main menu**. Any menu item with sub-items becomes a big dropdown panel. Works on phones as a tap-to-open list |
| Collection pages | Filters (Shopify's free Search & Discovery), sort, 24 per page, breadcrumbs |
| Product pages | Swipeable photo gallery, add to cart, rescue-mission note, description, "More rescued relics" |
| Meet the Team | Cards for Bailee (with an "In loving memory" ribbon), Casey and Savannah. Add photos in Customize |
| Contact, cart, search, blog, 404, password | All included |

## Speed rules built in
- The hero image and the main product photo are **never** lazy loaded. They load first, at high priority.
- Other product photos load right away too, but at low priority so they don't slow down the first photo.
- Everything else (cards lower on the page, collection tiles, thumbnails) lazy loads.
- One tiny script (2.5 KB). No jQuery, no sliders, no animation libraries.
- No web fonts. It uses fonts already on the phone, so text shows instantly.
- Express checkout buttons on product pages are OFF by default (they load extra scripts). Turn them on in Customize > Product > Buy buttons if you want them.

## How to change things (no code)
Online Store > Themes > RRR Bailee > **Customize**
- **Hero image:** Home page > Hero banner > Image (use a wide image, at least 2400px across)
- **Colors:** Theme settings > Colors
- **Meet the Team photos:** pick the Meet the Team page template (see below) > click each dog > Photo
- **Menu:** Online Store > Navigation > Main menu (the mega menu updates by itself)

## Turn on Meet the Team
1. Online Store > Pages > Add page. Title: **Meet the Team**
2. On the right, under Theme template, pick **meet-the-team**
3. Save. The page is at /pages/meet-the-team

## Re-upload this theme (if you ever need to)
1. Download this repo branch as a ZIP from GitHub
2. Zip just the contents of the `theme` folder (the folders `layout`, `sections`, etc. must sit at the top of the zip)
3. Shopify admin > Online Store > Themes > Add theme > Upload zip file

## Check for code errors (for Claude or a developer)
`npx @shopify/cli theme check --path theme` (it should say 0 errors)
