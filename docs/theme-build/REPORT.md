# RRR Relic theme: build report (Nov 4–5, 2026)

## What got built
A **custom Shopify theme written from scratch**, so every line is yours. It isn't Dawn, and it isn't copied from
Shopify's Skeleton theme either. Skeleton's license only allows its code on Shopify, so I used it just as a
reference for folder layout.

### The WOW pass (built from your Dropbox folder)
- **Your palette:** cream #F6EBD3, retro orange #EB540A, teal #0D6E77, mustard #DEBB07, raspberry #A03D5E,
  mint #79E2CC and charcoal #2B3A3A.
- **Your fonts:** Luckiest Guy for headings, Bangers for labels and buttons, Pacifico for script accents and Playpen
  Sans for body text. They're bundled in the theme and trimmed down to about 82KB total. American Years (your title
  font) isn't freely licensed for websites, so your logo carries that look.
- **Comic-book pop art**, from your vibe folder: a sunburst hero with halftone dots, comic "POW" bursts, a diner
  checkerboard under the header, wavy section edges, and stickers, tape and polaroids.
- **Your art is built in**, so it looks right the moment you upload it:
  - Bailee's photo, in a tilted polaroid on the homepage
  - Your round logo, in the header
  - Bailee, Savannah and Casey, as homepage mascots (Bailee wears a gold halo)
  - Superhero Savannah and Casey, plus you and Bailee, on Meet the Crew
  - The QC Team comic, in the mission block
- **Brand voice applied:** nothing calls items "vintage," "original" or "one-of-a-kind." Every sale is tied to
  **Detroit** dog rescue, all three dogs use she/her, and there's a 90s Friday-night tone throughout.

### Pages
| Piece | Status |
|---|---|
| **Bailee banner:** sunburst + polaroid (2 other layouts available), never lazy-loaded, `fetchpriority="high"` | ✅ |
| **Scrolling ticker:** "Rescued relics ★ Bailee approved ★ QC by Savannah ★ Naps by Casey…" | ✅ |
| **Meet the Crew strip** on the homepage | ✅ |
| **Mega menu** (desktop columns; mobile slide-down menu; works even with JavaScript off) | ✅ |
| **Collection pages** (filters, sorting, pagination, breadcrumbs, SEO description) | ✅ |
| **Product pages** (main photo loads first, photo strip, "Only 1 left" note, Detroit rescue mission box, "You might also dig") | ✅ |
| **Meet the Crew page:** *Why We Exist*, all 7 crew members, *The Transport Work*, and your closing line, **all in your exact words from meet-the-crew.md** | ✅ |
| Cart, search, blog, article, 404, password, gift card | ✅ |
| SEO schema (Product, Breadcrumb, Store, Website), Open Graph, canonical tags | ✅ |
| Shopify Theme Check (official code checker) | ✅ **0 errors, 0 warnings** |

## Speed test results (Lighthouse, mobile)

| Page | Live site now | New theme | Accessibility |
|---|---|---|---|
| Home | 64 | **95** | **100** |
| Product | 49 | **94** | **100** |
| Collection | 62 | **94** | **100** |
| Meet the Crew | n/a | **100** | **100** |

Blocking JavaScript time dropped from 810–1,680ms on the live site to **0–60ms**. Layout shift on collections went
from **0.335 (poor)** to **0.05 (good)**.

**⚠️ How honest are these numbers?** They come from a **local preview** that used your real products and photos,
not from Shopify itself. My Shopify connection dropped, so I couldn't upload the draft for you. Shopify adds its
own scripts to every page (analytics, Shop Pay, apps), so expect the real draft to score somewhat lower. Your
live-site numbers above already include those scripts.

## What I did NOT touch
No purchases. Nothing published. Nothing deleted on your store. No changes to products, collections, menus or your
live theme. Everything is in GitHub on branch `claude/elegant-meitner-ev2drv`.

## What's left (in order)
1. **Upload the draft (2 minutes, safe).** Shopify Admin → Online Store → Themes → *Add theme* → *Upload zip file*
   → `docs/theme-build/rrr-relic-theme.zip`. It arrives **unpublished**, and your live site doesn't change. Click
   *Customize* to look around.
2. **Meet the Crew page:** Online Store → Pages → *Add page*, title it **Meet the Team** (so the web address is
   `/pages/meet-the-team`) and set its template to `page.meet-the-team`. All your text is already in it.
3. **Photos still needed:** Mr. Proper, Mrs. Peanut Butter, Bella and Jory have colored placeholders for now. Add
   their photos in Customize → the Meet the Crew page. Do the same in the homepage crew strip for Mr. Proper and
   Mrs. Peanut Butter.
4. **Mega menu:** in Navigation → Main menu, drag links *under* a top link to create columns.
5. **Filters:** install the free *Shopify Search & Discovery* app to choose which filters show.
6. **Re-test on Shopify:** run PageSpeed Insights on the draft's preview link.
7. **Later:** use your FAQ, Policies and About PDFs (also in Dropbox) for those pages. Other nice-to-haves: reviews
   app blocks and a slide-out cart.

## Things I chose for you (change any of them)
- Homepage headline "Rescued relics. *Rescued dogs.*" and the buttons "Shop the relics" and "Meet the crew."
- The polaroid caption "Bailee, the reason for all of it."
- The footer tagline "Irish roots. Michigan heart. Rescue soul." with a little shamrock.
- The "Buy it now / Shop Pay" button is **off** by default because it loads extra scripts. Turn it on in the product section if you want it.
- Ticker phrases are editable in the Ticker section, separated with `|`.

## Your independence from Shopify
The theme, art, fonts and copy all live in **your GitHub**. The templates use Shopify's Liquid language, so moving
platforms someday would mean translating them. The design, CSS, images and words are yours to carry anywhere.
