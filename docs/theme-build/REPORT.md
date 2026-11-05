# RRR Relic theme: build report (Nov 4–5, 2026)

## What got built
A **custom Shopify theme written from scratch**, so every line is yours. It isn't Dawn, and it isn't copied from
Shopify's Skeleton theme either. Skeleton's license only allows its code on Shopify, so I used it just as a
reference for folder layout.

| Piece | Status |
|---|---|
| Full-width **Bailee banner** (separate mobile image option, never lazy-loaded, `fetchpriority="high"`) | ✅ |
| **Mega menu** (desktop panels with columns; mobile slide-down menu; works even with JavaScript off) | ✅ |
| **Collection pages** (filters, sorting, pagination, breadcrumbs, description for SEO) | ✅ |
| **Product pages** (main image loads first, photo strip, variant picker, "One of a kind" note, dog-rescue mission note, "You might also dig" row) | ✅ |
| **Meet the Team** page (Bailee featured "In loving memory", Casey, Savannah, Stephanie) | ✅ |
| Homepage: featured collection, "Dig through the crates" category tiles, mission block | ✅ |
| Cart, search, blog, article, 404 ("This page wandered off"), password, gift card | ✅ |
| SEO: Product / Breadcrumb / Store / Website schema, Open Graph, canonical tags | ✅ |
| Shopify Theme Check (official code checker) | ✅ **0 errors, 0 warnings** |

**Total code the browser downloads: about 20KB of CSS plus 3KB of JavaScript.** Body text uses each phone's
built-in font (zero downloads), so only the headings load a font (Arvo, a retro slab).

Look and feel: warm cream background, retro orange buttons with a chunky "shadow" offset, and teal, mustard and
bubblegum accents. Every color and font can be changed in the theme editor.

## Speed test results (Lighthouse, mobile)

| Page | Your live site now | New theme | LCP now → new | Layout shift now → new |
|---|---|---|---|---|
| Home | 64 | **98** | 3.3s → 2.5s | 0 → 0 |
| Product | 49 | **97** | 5.9s → 2.5s | 0 → 0 |
| Collection | 62 | **95** | 1.4s → 2.9s* | **0.335 → 0** |
| Meet the Team | n/a | **100** | n/a → 0.9s | 0 |

Accessibility: **100 on every page** (live site: 93–97). Blocking JavaScript time: **0ms** (live site: 810–1,680ms).

**⚠️ How honest are these numbers?** The new-theme tests ran on a **local preview** that used your real products
and photos from your store, not on Shopify itself. My Shopify connection dropped mid-session, so I couldn't upload
the draft. Shopify adds its own scripts to every page (analytics, Shop Pay, any apps), and those will lower the real
score somewhat. Your live site's numbers above already include those scripts. Expect the real draft to land a
bit lower than shown here, but still far ahead of where you are now. The *Collection LCP: 2.9s comes from the first
product photo, which is now loaded up front on purpose. On the live site, the page jumps around (0.335 layout shift),
and that's what Google penalizes more.

## What I did NOT touch
No purchases. Nothing published. Nothing deleted on your store. No changes to products, collections, menus or your
live theme. Everything lives in GitHub on branch `claude/elegant-meitner-ev2drv`.

## What's left (in order)
1. **Upload the draft (2 minutes, safe).** Shopify Admin → Online Store → Themes → *Add theme* → *Upload zip file*
   → pick `docs/theme-build/rrr-relic-theme.zip`. It arrives **unpublished**, and your live site doesn't change.
   Then click *Customize* to preview.
   - If the upload complains about the font, open Theme settings → Typography and pick any heading font. I chose
     Arvo but couldn't confirm it's in Shopify's font list from here.
2. **Add the Bailee artwork:** Customize → Home → *Bailee banner* → Banner image. A taller crop for phones is optional.
3. **Meet the Team:** create a page (Online Store → Pages) titled *Meet the Team*, and set its template to
   `page.meet-the-team`. Add photos and **check the bios**. I wrote placeholder text and you should make it yours.
4. **Mega menu:** in Navigation → Main menu, drag links *under* a top link to make columns (e.g. Shop → Cartoons → Betty Boop).
5. **Filters:** install the free *Shopify Search & Discovery* app to choose which filters show on collection pages.
6. **Re-test on Shopify:** run PageSpeed Insights on the draft's preview link and compare it to the table above.
7. **Later, nice-to-haves:** app blocks (for a reviews app, if you add one), a slide-out cart, and colored
   "swatches" for variants.

## Things I guessed (change any of them)
- Heading font **Arvo**, and the colors listed above.
- Hero text "Rescued relics. Rescued dogs." and the button "Shop the relics".
- "Buy it now / Shop Pay" button **off** by default, because it loads extra Shopify scripts. Turn it on in the product section if you want it.
- Team titles (Chief Inspiration Officer, Snack inspector, Head of couch operations).

## Your independence from Shopify
The theme lives in **your GitHub**, so Shopify can't change it on you. The templates are written in Shopify's
Liquid language, which means moving platforms someday would mean translating them. But the design, CSS, layout,
copy and structure are all yours to carry over.
