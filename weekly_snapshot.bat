@echo off
rem Weekly business snapshot. Double-click me. Read-only: changes nothing on Shopify or eBay.
cd /d "%~dp0"
python -m rrr_snapshot %*
if exist reports\latest.html start "" reports\latest.html
pause
