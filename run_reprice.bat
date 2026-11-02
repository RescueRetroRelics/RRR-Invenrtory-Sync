@echo off
rem Double-click to get suggested eBay price cuts. Changes NOTHING on eBay.
rem To use a Seller Hub export instead of reading eBay live, drag the CSV onto this file.
cd /d "%~dp0"
if not exist data mkdir data
if "%~1"=="" (
  if not exist ".secrets\ebay_tokens.json" if not exist ".tokens.json" (
    echo No eBay sign-in in this folder, so I need your eBay export instead:
    echo   1. eBay Seller Hub ^> Listings ^> Active ^> Download ^(CSV^)
    echo   2. Drag that CSV file onto run_reprice.bat
    pause
    exit /b 1
  )
  python -m rrr_sync reprice
) else (
  python -m rrr_sync reprice --file "%~1"
)
if errorlevel 1 (
  echo.
  echo Something went wrong. Copy the message above and send it to Claude.
  pause
  exit /b 1
)
for /f "delims=" %%d in ('dir /b /ad /o-n data\reprice\20* 2^>nul') do (
  start "" "data\reprice\%%d\preview.html"
  goto :done
)
:done
pause
