# RRR Photo Shrinker

A one-page tool to shrink listing photos on your phone **before** they go to Shopify.

- Runs entirely in the phone's browser. Photos are never uploaded anywhere by this page.
- Resizes to 2048 px on the longest side (Shopify's recommended size), JPEG quality 82.
  A typical 4–8 MB phone photo comes out around 400–700 KB.
- Optional: square with white padding, and a name prefix (`pyrex-butterprint-01.jpg`, …).
- Strips GPS/location data from the photos.

## Phone workflow

1. Open the page, tap **Choose photos**, pick the listing photos.
2. Tap **Save all to phone**. On iPhone choose **Save N Images** so they land in Photos.
3. In the Shopify app, add those shrunk photos to the product.

Open it in a normal browser from:
https://raw.githack.com/RescueRetroRelics/RRR-Invenrtory-Sync/main/photo_compressor/index.html
(works once this is merged to `main`).
