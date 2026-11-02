@echo off
rem Monthly bookkeeping summary. Double-click to run.
rem Put your Shopify + eBay CSV exports (and expenses.csv) in bookkeeping\exports first.
cd /d "%~dp0"
python bookkeeping\monthly_summary.py %*
if errorlevel 1 (pause & exit /b)
for /f "delims=" %%f in ('dir /b /o-d bookkeeping\reports\monthly_summary_*.html') do (start "" "bookkeeping\reports\%%f" & goto :done)
:done
pause
