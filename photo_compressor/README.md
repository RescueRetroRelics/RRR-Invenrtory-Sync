# RRR Photo Shrinker

A one-page tool to shrink listing photos on your phone **before** they go to Shopify.

- Runs entirely in the phone's browser. Photos are never uploaded anywhere by this page.
- Every photo comes out **1500 × 1500 square** (Google Merchant Center minimum is 500 × 500).
  Pad with white (default) keeps the whole item; Crop to fill is optional. 2048 and 1000 also offered.
- **JPEG or WebP.** If the phone's browser can't write WebP, it falls back to JPEG and says so.
- Quality steps down automatically until each file is **under 1 MB** (typical: ~370 KB).
- **Bulk:** up to 50 photos per batch, with a progress bar. Pick more and it does the first 50.
- Warns about photos that started under 500 px. Strips GPS/location data.
- Optional name prefix: `pyrex-butterprint-01.jpg`, `-02`, …

## Phone workflow

1. Open the page, tap **Choose photos**, pick the listing photos.
2. Tap **Save all to phone**. On iPhone choose **Save N Images** so they land in Photos.
3. In the Shopify app, add those shrunk photos to the product.

Open it in a normal browser from:
https://raw.githack.com/RescueRetroRelics/RRR-Invenrtory-Sync/claude/rrr-ebay-shopify-sync-0efnu0/photo_compressor/index.html
(works once this is merged).
