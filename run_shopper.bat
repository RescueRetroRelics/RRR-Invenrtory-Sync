@echo off
rem Double-click to run the simulated shopper. Dry run: it never buys anything.
cd /d "%~dp0"
python -c "import playwright" 2>nul || (
  echo First run: installing the browser robot, this takes a few minutes...
  python -m pip install --quiet playwright
  python -m playwright install chromium
)
python -m rrr_shopper %*
pause
