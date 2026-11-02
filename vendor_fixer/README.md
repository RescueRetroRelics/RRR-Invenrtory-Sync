# Vendor Field Fixer

Every product in the store says **Rescue Retro Relics** in the Vendor field (1,014 of 1,014,
checked in Shopify on Nov 2, 2026). Shoppers and Google filter by brand, so this tool proposes
the real maker (Re-Ment, Bandai, Takara Tomy, Sanrio...) for each product.

**It never changes your store by itself.** It reads a file, writes a spreadsheet, and you decide.

## What you need

- Python (already installed for the sync tool)
- A Shopify product export: Shopify admin → **Products → Export → All products → CSV for Excel**.
  It gets emailed to you. Save it in this folder as `products_export.csv`.

## Run it (one command)

Double-click **`run_vendor_fixer.bat`**, or in a terminal in this repo folder:

```
python vendor_fixer/vendor_fixer.py products_export.csv
```

You get two files in `data\vendor_fixer\`:

| File | What it is |
|---|---|
| `vendor_preview_<date>.csv` | Every product: current vendor, proposed brand, confidence, why |
| `backup_vendors_<date>.csv` | Every product's vendor **before** any change (your undo file) |

## Reading the preview

| Confidence | Meaning | What to do |
|---|---|---|
| high | Maker is named in the title, type or tags | Already marked **Y**. Skim it. |
| medium | Very likely (e.g. Hello Kitty → Sanrio) | Glance, type **Y** if right |
| low | Only the character is named (Snoopy → Peanuts) | Your call: type the real maker, or **Y** to use the franchise name |
| none | No brand found | Type a brand in **Proposed Vendor** and **Y**, or leave blank to skip |

Open in Excel, change the **Proposed Vendor** column or the **Approve (Y/N)** column, save as CSV.

## Make the import files (still a dry run)

```
python vendor_fixer/vendor_fixer.py products_export.csv --approved data\vendor_fixer\vendor_preview_<date>.csv
```

This writes `import_batch_01.csv`, `import_batch_02.csv`... 50 products each, with only
Handle, Title and Vendor.

## Apply

Two ways. Do one batch, check a few products, then the next.

1. **Ask Claude** to apply a batch through the Shopify connector. You'll get a confirm prompt
   for every change.
2. **Shopify import:** Products → Import → pick `import_batch_01.csv` → tick
   **Overwrite products with matching handles** → Import. Test with a tiny file (3 rows) first and
   check those products still have their price, photos and description before doing a full batch.

## Undo

The backup file has the old vendor for every product. Run it through the same import steps.

## Add brands

Edit `vendor_fixer/brands.csv` in Excel. One row per keyword. Top rows win. No code needed.

## Re-run after changes

Export products again and run the same command. Products that already have a real vendor are
skipped, so only new listings still saying "Rescue Retro Relics" show up. Add `--all` to review
every product.

## Sample

`vendor_fixer/sample/sample_vendor_preview.csv` shows real output from 11 test products.
