@echo off
rem Read-only Shopify vs eBay inventory report. Double-click to run.
cd /d "%~dp0"
if not exist data mkdir data
python -m rrr_sync reconcile
echo.
for /f "delims=" %%d in ('dir /b /ad /o-n data\reconcile 2^>nul') do (
  start "" "data\reconcile\%%d"
  goto :done
)
:done
pause
