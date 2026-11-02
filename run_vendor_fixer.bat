@echo off
rem Vendor Field Fixer: makes a preview spreadsheet. Changes nothing in Shopify.
cd /d "%~dp0"
if not exist products_export.csv (
  echo Put your Shopify export here as products_export.csv first. See vendor_fixer\README.md
  pause
  exit /b 1
)
python vendor_fixer\vendor_fixer.py products_export.csv
pause
