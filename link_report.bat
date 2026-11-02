@echo off
rem Six read-only lists linking eBay listings to Shopify products. Double-click to run.
cd /d "%~dp0"
if not exist data mkdir data
python -m rrr_sync link-report
echo.
for /f "delims=" %%d in ('dir /b /ad /o-n data\links 2^>nul') do (
  start "" "data\links\%%d"
  goto :done
)
:done
pause
